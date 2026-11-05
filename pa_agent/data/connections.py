"""Bounded HTTP and MCP requests shared by quotes and information queries."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from pa_agent.data.source_config import SourceProfile

MAX_RESPONSE_BYTES = 8_000_000


class ConnectionError(ValueError):
    """Messages may be shown in the GUI; never include credentials or raw URLs."""


def expand(value, variables: dict[str, str], *, url: bool = False):
    if isinstance(value, dict):
        return {key: expand(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, variables) for item in value]
    if isinstance(value, str):
        if not url and value == "{limit}" and "limit" in variables:
            return int(variables["limit"])
        for key, item in variables.items():
            value = value.replace("{" + key + "}", quote(str(item), safe="") if url else str(item))
    return value


def value_at(value, path: str):
    for key in path.split(".") if path else ():
        try:
            value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ConnectionError(f"响应中找不到字段路径：{path}") from None
    return value


def environment_value(name: str) -> str:
    value = os.environ.get(name, "")
    if not value and os.name == "nt":
        # A running desktop app may predate a user's environment-variable update.
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = str(winreg.QueryValueEx(key, name)[0])
        except OSError:
            pass
    if not value:
        raise ConnectionError(f"环境变量 {name} 未设置，请在数据源设置中填写授权信息。")
    return value


def connection_headers(profile: SourceProfile) -> dict[str, str]:
    return {
        **profile.headers,
        **{key: environment_value(name) for key, name in profile.env_headers.items()},
    }


def validate_profile(profile: SourceProfile) -> None:
    if not profile.name.strip():
        raise ConnectionError("请填写数据源名称。")
    if profile.transport == "csv":
        if profile.category != "market" or not profile.csv_path.strip():
            raise ConnectionError("CSV 行情需要选择本地文件。")
    elif profile.transport == "mcp_stdio":
        if not profile.command.strip():
            raise ConnectionError("请填写本地 MCP 程序路径。")
    else:
        url = urlsplit(profile.url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ConnectionError("请填写 http:// 或 https:// 接口地址；授权信息请放在请求头中。")
    if profile.category == "market":
        from pa_agent.data.source_config import OHLCV_COLUMNS, TIMEFRAMES

        if not profile.symbol.strip() or not set(OHLCV_COLUMNS) <= profile.columns.keys():
            raise ConnectionError(
                "行情源需要默认品种和 time/open/high/low/close/volume 六项字段映射。"
            )
        if not profile.timeframe_map or any(t not in TIMEFRAMES for t in profile.timeframe_map):
            raise ConnectionError("周期映射中含有不支持的周期。")
        if profile.timeframe not in profile.timeframe_map:
            raise ConnectionError("默认周期必须包含在周期映射中。")
    if profile.transport.startswith("mcp_") and ("{" in profile.url or "}" in profile.url):
        raise ConnectionError("MCP 地址固定不变，查询变量请放在工具参数中。")


def request_json(profile: SourceProfile, variables: dict[str, str]):
    validate_profile(profile)
    headers = connection_headers(profile)
    url = expand(profile.url, variables, url=True)
    body = expand(profile.body, variables) if profile.method == "POST" else None
    try:
        with httpx.Client(timeout=profile.timeout_seconds, follow_redirects=False) as client:
            with client.stream(profile.method, url, headers=headers, json=body) as response:
                if response.status_code >= 300:
                    raise ConnectionError(
                        f"接口返回 HTTP {response.status_code}，请检查地址、授权或服务额度。"
                    )
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise ConnectionError("接口返回内容超过 8 MB，请缩小查询范围。")
        return json.loads(chunks)
    except httpx.TimeoutException:
        raise ConnectionError("接口请求超时，请检查网络或缩小查询范围。") from None
    except httpx.HTTPError:
        raise ConnectionError("接口连接失败，请检查网络、代理和接口地址。") from None
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, ConnectionError):
            raise
        raise ConnectionError("接口未返回有效 JSON，请检查接口类型。") from None


async def _mcp_request(profile: SourceProfile, *, discover: bool, variables: dict[str, str]):
    from contextlib import AsyncExitStack

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    validate_profile(profile)
    async with asyncio.timeout(profile.timeout_seconds), AsyncExitStack() as stack:
        if profile.transport == "mcp_stdio":
            errlog = stack.enter_context(open(os.devnull, "w"))
            transport = stdio_client(
                StdioServerParameters(
                    command=profile.command, args=profile.args, env={**os.environ, **profile.env}
                ),
                errlog=errlog,
            )
        else:
            factory = sse_client if profile.transport == "mcp_sse" else streamablehttp_client
            transport = factory(
                profile.url,
                headers=connection_headers(profile),
                timeout=profile.timeout_seconds,
                sse_read_timeout=profile.timeout_seconds,
            )
        streams = await stack.enter_async_context(transport)
        session = await stack.enter_async_context(
            ClientSession(
                streams[0],
                streams[1],
                read_timeout_seconds=timedelta(seconds=profile.timeout_seconds),
            )
        )
        await session.initialize()
        tools = []
        cursor = None
        while True:
            page = await session.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.nextCursor
            if not cursor or len(tools) >= 1000:
                break
        if discover:
            return [tool.model_dump(mode="json", exclude_none=True) for tool in tools]
        tool = next((tool for tool in tools if tool.name == profile.mcp_tool), None)
        if tool is None:
            raise ConnectionError("未找到所选 MCP 工具，请先读取工具列表并选择工具。")
        arguments = expand(profile.mcp_arguments, variables)
        from jsonschema import ValidationError, validate

        try:
            validate(arguments, tool.inputSchema)
        except ValidationError:
            raise ConnectionError("MCP 参数不符合工具要求，请参照工具参数说明填写。") from None
        result = await session.call_tool(profile.mcp_tool, arguments)
        if result.isError:
            raise ConnectionError("MCP 工具返回错误，请检查参数、授权或服务额度。")
        if result.structuredContent is not None:
            return result.structuredContent
        texts = [item.text for item in result.content if item.type == "text"]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except ValueError:
                pass
        return {"text": "\n".join(texts)}


def mcp_request(
    profile: SourceProfile, *, discover: bool = False, variables: dict[str, str] | None = None
):
    try:
        return asyncio.run(_mcp_request(profile, discover=discover, variables=variables or {}))
    except ConnectionError:
        raise
    except Exception as exc:
        # SDK transport errors may contain the authenticated URL/headers.
        def safe_message(error):
            if isinstance(error, ConnectionError):
                return str(error)
            for child in getattr(error, "exceptions", ()):
                message = safe_message(child)
                if message:
                    return message
            return ""

        message = safe_message(exc)
        raise ConnectionError(
            message or "MCP 连接或调用失败，请检查服务地址、授权、工具参数和超时设置。"
        ) from None


def import_mcp_config(path: Path) -> list[SourceProfile]:
    """Import user-owned plain MCP configuration; retain environment references."""
    import tomllib

    raw = path.read_text(encoding="utf-8-sig")
    data = tomllib.loads(raw) if path.suffix.lower() == ".toml" else json.loads(raw)
    servers = data.get("mcp_servers", data.get("mcpServers", {}))
    profiles = []
    for name, entry in servers.items():
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        if not entry.get("url") and not entry.get("command"):
            continue
        url = entry.get("url", "")
        transport = "mcp_http" if url else "mcp_stdio"
        if entry.get("type", entry.get("transport")) == "sse":
            transport = "mcp_sse"
        headers = dict(entry.get("http_headers", entry.get("headers", {})))
        env_headers = dict(entry.get("env_http_headers", {}))
        if entry.get("bearer_token_env_var"):
            # Codex's bearer reference requires its prefix, so resolve on import.
            headers["Authorization"] = "Bearer " + environment_value(entry["bearer_token_env_var"])
        profiles.append(
            SourceProfile(
                name=name,
                category="information",
                transport=transport,
                url=url,
                headers=headers,
                env_headers=env_headers,
                command=entry.get("command", ""),
                args=entry.get("args", []),
                env=entry.get("env", {}),
                mcp_tool="mx_finance_search_news"
                if urlsplit(url).hostname == "mxapi.eastmoney.com"
                else "",
                description="从本机 MCP 配置导入。",
            )
        )
    return profiles
