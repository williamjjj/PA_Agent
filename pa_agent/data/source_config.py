"""User-configured market feeds and information services."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

BUILTIN_SOURCES = (
    ("mt5", "MT5"),
    ("tradingview", "TradingView"),
    ("ashare", "A股（专属数据源）"),
    ("akshare", "AkShare（A股）"),
    ("eastmoney_futures", "东方财富期货"),
    ("tushare", "Tushare（A股）"),
    ("yfinance", "Yahoo Finance（全球市场）"),
)
TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w")
OHLCV_COLUMNS = {key: key for key in ("time", "open", "high", "low", "close", "volume")}


class SourceProfile(BaseModel):
    id: str = Field(default_factory=lambda: "custom:" + uuid4().hex)
    name: str = Field(min_length=1, max_length=100)
    category: Literal["market", "information"] = "market"
    transport: Literal["csv", "http", "mcp_http", "mcp_sse", "mcp_stdio"] = "http"
    enabled: bool = True
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    env_headers: dict[str, str] = Field(default_factory=dict)
    method: Literal["GET", "POST"] = "GET"
    body: dict = Field(default_factory=dict)
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    csv_path: str = ""
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    timeframe_map: dict[str, str] = Field(default_factory=lambda: {t: t for t in TIMEFRAMES})
    rows_path: str = ""
    columns: dict[str, str] = Field(default_factory=lambda: dict(OHLCV_COLUMNS))
    max_bars: int = Field(default=1000, ge=1, le=10000)
    poll_seconds: int = Field(default=10, ge=1, le=3600)
    timeout_seconds: int = Field(default=30, ge=3, le=180)
    mcp_tool: str = ""
    mcp_arguments: dict = Field(default_factory=lambda: {"query": "{query}"})
    description: str = ""

    @field_validator("id")
    @classmethod
    def custom_id(cls, value: str) -> str:
        if not value.startswith("custom:") or len(value) > 100:
            raise ValueError("自定义数据源标识须以 custom: 开头。")
        return value

    @field_validator("timeframe")
    @classmethod
    def timeframe_supported(cls, value: str) -> str:
        if value not in TIMEFRAMES:
            raise ValueError("请选择支持的 K 线周期。")
        return value


class SourcesSettings(BaseModel):
    enabled_builtins: list[str] = Field(default_factory=lambda: ["mt5", "tradingview"])
    profiles: list[SourceProfile] = Field(default_factory=list)


def source_templates() -> dict[str, SourceProfile]:
    """Public endpoints only; private keys are always supplied locally."""
    array_columns = {key: str(i) for i, key in enumerate(OHLCV_COLUMNS)}
    return {
        "本地 CSV 行情": SourceProfile(name="本地 CSV", transport="csv", symbol="DEMO"),
        "币安 · 现货行情": SourceProfile(
            name="币安现货",
            url="https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={timeframe}&limit={limit}",
            columns=array_columns,
            description="公开现货行情，无需交易 API Key。",
        ),
        "OKX · 现货 / 合约行情": SourceProfile(
            name="OKX 行情",
            symbol="BTC-USDT",
            max_bars=300,
            url="https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={timeframe}&limit={limit}",
            rows_path="data",
            columns={**array_columns, "closed": "8"},
            timeframe_map={
                "1m": "1m",
                "3m": "3m",
                "5m": "5m",
                "15m": "15m",
                "30m": "30m",
                "1h": "1H",
                "2h": "2H",
                "4h": "4H",
                "6h": "6Hutc",
                "12h": "12Hutc",
                "1d": "1Dutc",
                "1w": "1Wutc",
            },
            description="现货如 BTC-USDT，永续如 BTC-USDT-SWAP；单次最多 300 根。",
        ),
        "自定义 HTTP / JSON 行情": SourceProfile(name="自定义行情"),
        "自定义 HTTP / JSON 资讯": SourceProfile(name="自定义资讯", category="information"),
        "东方财富妙想 MCP": SourceProfile(
            name="东方财富妙想",
            category="information",
            transport="mcp_http",
            timeout_seconds=90,
            url="https://mxapi.eastmoney.com/mxds/mcp",
            mcp_tool="mx_finance_search_news",
            description="可查询股票、期货、宏观、新闻与公告。先读取工具列表，再选择用途。",
        ),
        "金十数据 MCP · 快讯与资讯": SourceProfile(
            name="金十数据",
            category="information",
            transport="mcp_http",
            timeout_seconds=60,
            url="https://mcp.jin10.com/mcp",
            mcp_tool="search_flash",
            mcp_arguments={},
            description="需金十 MCP Token。点击读取工具列表，查看快讯、资讯和行情工具的参数。",
        ),
        "金十数据 MCP · K线": SourceProfile(
            name="金十 K线",
            transport="mcp_http",
            symbol="XAUUSD",
            timeout_seconds=60,
            url="https://mcp.jin10.com/mcp",
            mcp_tool="get_kline",
            mcp_arguments={},
            rows_path="data.klines",
            description="需金十 MCP Token；先读取工具参数，配置代码与周期。",
        ),
        "其他 MCP 服务": SourceProfile(
            name="MCP 资讯", category="information", transport="mcp_http"
        ),
    }
