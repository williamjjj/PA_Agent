"""Request-scoped, bounded OpenAI-compatible streaming client."""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import asdict
from urllib.parse import urlsplit

import httpx
from openai import APIConnectionError, APIStatusError, OpenAI

from pa_agent.ai.deepseek_client import (
    AIReply, AIUsage, CancelledError, _prepare_chat_messages,
    _provider_max_output_tokens, _resolve_thinking_params,
)


def chat_parameters(settings, messages, *, thinking=None, reasoning_effort=None):
    """Reuse provider compatibility without desktop clients or global caches."""
    provider = settings.core().provider
    extra, effort = _resolve_thinking_params(
        provider, thinking=thinking, reasoning_effort=reasoning_effort)
    output_limit = min(settings.max_tokens, _provider_max_output_tokens(provider))
    if "budget_tokens" in extra.get("thinking", {}):
        if output_limit <= 1024:
            raise ValueError("此模型的思考模式需要最大输出大于 1024 Token。")
        extra["thinking"]["budget_tokens"] = min(extra["thinking"]["budget_tokens"], output_limit - 1)
    api_messages, system = _prepare_chat_messages(provider, messages)
    if system:
        extra["system"] = system
    params = {"model": settings.model, "messages": api_messages, "stream": True,
              "stream_options": {"include_usage": True}}
    native_reasoning = (urlsplit(settings.base_url).hostname == "api.openai.com" and
                        settings.model.startswith(("gpt-5", "o1", "o3", "o4")))
    params["max_completion_tokens" if native_reasoning else "max_tokens"] = output_limit
    if extra:
        params["extra_body"] = extra
    if effort:
        params["reasoning_effort"] = effort
    return params


class ProviderError(RuntimeError):
    """Safe, actionable error text with no upstream body or credentials."""


def list_models(settings):
    try:
        with httpx.Client(transport=PublicTransport(retries=0), follow_redirects=False,
                          trust_env=False, timeout=httpx.Timeout(15, connect=8)) as http:
            with OpenAI(api_key=settings.api_key, base_url=settings.base_url,
                        max_retries=0, http_client=http) as client:
                page = client.models.list()
                models = sorted({item.id for item in page.data[:1000]
                                 if isinstance(item.id, str) and 0 < len(item.id) <= 120
                                 and all(32 < ord(c) < 127 for c in item.id)})
                if not models:
                    raise ProviderError("服务已响应，但未返回模型列表；请按供应商文档填写模型标识。")
                return models
    except APIStatusError as exc:
        messages = {
            401: "模型服务拒绝了 API Key，请检查凭据。",
            403: "此 API Key 没有读取模型列表的权限。",
            404: "此地址未提供模型列表，请检查 Base URL，或手动填写模型标识。",
            429: "模型服务限流或额度不足，请稍后重试并检查账户额度。",
        }
        raise ProviderError(messages.get(exc.status_code, "模型服务暂时无法返回列表，请检查地址或稍后重试。")) from None
    except (APIConnectionError, httpx.HTTPError, ValueError):
        raise ProviderError("无法连接模型服务，请确认地址为可访问的公共 HTTPS API。") from None


class PublicTransport(httpx.HTTPTransport):
    """Validate DNS on every connection, then pin the resolved public IP.

    Keeping the original Host and TLS SNI prevents DNS rebinding between validation
    and connection. Redirects and environment proxies are disabled by the client.
    """
    def handle_request(self, request):
        host = request.url.host
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(row[4][0] for row in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError("模型地址必须解析为公共网络地址。")
        original = request.url
        request.headers["Host"] = host
        request.extensions["sni_hostname"] = host
        request.url = request.url.copy_with(host=ips[0])
        try:
            return super().handle_request(request)
        finally:
            request.url = original


class ModelClient:
    def __init__(self, settings):
        self.settings = settings
        self.deadline = time.monotonic() + 170

    def stream_chat(self, messages, *, cancel_token, on_content_token=None,
                    on_reasoning_token=None, thinking=None, reasoning_effort=None, **kwargs):
        cfg = self.settings
        if not cfg.api_key:
            raise ValueError("请先在个人设置中保存模型 API Key。")
        # Conservatively bound input before sending a potentially expensive request.
        import tiktoken
        encoder = tiktoken.get_encoding("cl100k_base")
        input_size = sum(len(encoder.encode(str(m.get("content", "")) + str(m.get("reasoning_content", "")), disallowed_special=()))
                         + 8 for m in messages)
        if input_size + cfg.max_tokens > cfg.context_window:
            raise ValueError("上下文超过模型窗口，请减少 K 线、提示词或历史追问。")
        started = time.monotonic()
        remaining = self.deadline - started
        if remaining < 1:
            raise TimeoutError("分析超过请求时间限制。")
        if cancel_token.is_set():
            raise CancelledError("已取消")
        content, reasoning, usage, request_id = [], [], AIUsage(), ""
        finish_reason = None
        params = chat_parameters(cfg, messages, thinking=thinking, reasoning_effort=reasoning_effort)
        transport = PublicTransport(retries=0)
        with httpx.Client(transport=transport, follow_redirects=False, trust_env=False,
                          timeout=httpx.Timeout(min(45, remaining), connect=10)) as http:
            with OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, max_retries=0, http_client=http) as client:
                with client.chat.completions.create(**params) as stream:
                    for chunk in stream:
                        if cancel_token.is_set():
                            raise CancelledError("已取消")
                        if time.monotonic() >= self.deadline:
                            raise TimeoutError("分析超过请求时间限制。")
                        request_id = chunk.id or request_id
                        if chunk.usage:
                            u = chunk.usage
                            details = getattr(u, "prompt_tokens_details", None)
                            cached = getattr(details, "cached_tokens", 0) or getattr(u, "prompt_cache_hit_tokens", 0) or 0
                            usage = AIUsage(u.prompt_tokens or 0, cached, u.completion_tokens or 0, u.total_tokens or 0)
                        for choice in chunk.choices:
                            if choice.index != 0:
                                continue
                            if choice.finish_reason:
                                finish_reason = choice.finish_reason
                            delta = choice.delta
                            text = delta.content or ""
                            thought = getattr(delta, "reasoning_content", "") or ""
                            if text:
                                content.append(text)
                                if on_content_token:
                                    on_content_token(text)
                            if thought:
                                reasoning.append(thought)
                                if on_reasoning_token:
                                    on_reasoning_token(thought)
        if finish_reason == "length":
            raise ValueError("模型输出达到 Token 上限，请提高最大输出或精简提示词。")
        if finish_reason != "stop":
            raise ValueError("模型输出未正常结束，请检查连接或模型兼容性后重试。")
        text, thought = "".join(content), "".join(reasoning)
        return AIReply(text, thought, {"content": text, "reasoning_content": thought,
                       "usage": asdict(usage)}, usage, request_id, (time.monotonic()-started)*1000)
