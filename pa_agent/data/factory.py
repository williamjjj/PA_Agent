"""Construct :class:`DataSource` implementations by kind id."""
from __future__ import annotations

from typing import Literal

from pa_agent.data.base import DataSource
from pa_agent.data.source_config import BUILTIN_SOURCES
from pa_agent.data.market_defaults import (
    A_SHARE_DEFAULT_SYMBOL,
    GOLD_MT5_SYMBOL,
    GOLD_TV_SYMBOL,
)

DataSourceKind = Literal[
    "mt5",
    "tradingview",
    "ashare",
    "akshare",
    "eastmoney_futures",
    "tushare",
    "yfinance",
]

#: Compatibility default for callers without settings. The desktop's data-source
#: manager persists enabled built-ins and custom profiles independently.
SHOW_ASHARE_DATA_SOURCE: bool = False

#: A股专属数据源在界面/状态栏的统一文案。
ASHARE_SOURCE_LABEL = "A股（专属数据源）"

#: 出厂下拉框内容：MT5 为默认数据源，排在首位。
_BASE_SOURCE_CHOICES: tuple[tuple[DataSourceKind, str], ...] = (
    ("mt5", "MT5"),
    ("tradingview", "TradingView"),
)


def data_source_choices(
    show_ashare: bool | None = None,
    *, settings=None,
) -> tuple[tuple[str, str], ...]:
    """界面可选数据源列表；*show_ashare* 缺省时取 :data:`SHOW_ASHARE_DATA_SOURCE`。"""
    if settings is not None:
        enabled = set(settings.data_sources.enabled_builtins)
        # Retain previously selected built-ins when upgrading older settings.
        enabled.add(settings.general.last_data_source)
        builtins = tuple((key, label) for key, label in BUILTIN_SOURCES if key in enabled)
        custom = tuple((p.id, p.name) for p in settings.data_sources.profiles
                       if p.enabled and p.category == "market")
        return (*builtins, *custom) or _BASE_SOURCE_CHOICES
    show = SHOW_ASHARE_DATA_SOURCE if show_ashare is None else bool(show_ashare)
    if not show:
        return _BASE_SOURCE_CHOICES
    return (*_BASE_SOURCE_CHOICES, ("ashare", ASHARE_SOURCE_LABEL))


#: 供界面/测试直接使用的可选列表（导入时求值 → 改开关需重启程序生效）。
DATA_SOURCE_CHOICES: tuple[tuple[DataSourceKind, str], ...] = data_source_choices()

#: 不在下拉框里显示、但仍可通过 kind 创建的源（含未开启显示的 ashare）。
_HIDDEN_KINDS: frozenset[DataSourceKind] = frozenset(
    {"ashare", "akshare", "tushare", "yfinance", "eastmoney_futures"}
)

_DEFAULT_SYMBOLS: dict[DataSourceKind, str] = {
    "mt5": GOLD_MT5_SYMBOL,
    "tradingview": GOLD_TV_SYMBOL,
    "ashare": A_SHARE_DEFAULT_SYMBOL,
    "akshare": A_SHARE_DEFAULT_SYMBOL,
    "eastmoney_futures": "RB0 螺纹钢",
    "tushare": A_SHARE_DEFAULT_SYMBOL,
    "yfinance": "GC=F",
}


def default_tradingview_exchange() -> str:
    """Empty string = UI «（自动）» — probe all TV preset venues."""
    return ""


def normalize_data_source_kind(kind: str | None, settings=None) -> str:
    """Return a supported data-source kind, defaulting to MT5."""
    if kind and kind.startswith("custom:"):
        return kind if source_profile(kind, settings) is not None else "mt5"
    supported = {k for k, _ in DATA_SOURCE_CHOICES} | _HIDDEN_KINDS
    if kind in supported:
        return kind  # type: ignore[return-value]
    return "mt5"


def data_source_label(kind: str | None, settings=None) -> str:
    """Human-readable label for *kind*."""
    profile = source_profile(kind, settings)
    if profile is not None:
        return profile.name
    normalized = normalize_data_source_kind(kind, settings)
    for key, label in DATA_SOURCE_CHOICES:
        if key == normalized:
            return label
    if normalized == "ashare":
        return ASHARE_SOURCE_LABEL
    if normalized == "eastmoney_futures":
        return "东方财富期货"
    if normalized == "tushare":
        return "Tushare(A股)"
    if normalized == "akshare":
        return "AkShare"
    if normalized == "yfinance":
        return "YFinance"
    return "MT5"


def source_profile(kind, settings=None):
    if not kind or not kind.startswith("custom:"):
        return None
    if settings is None:
        from pa_agent.config.settings import load_settings
        settings = load_settings()
    return next((p for p in settings.data_sources.profiles if p.id == kind), None)


def default_symbol_for_kind(kind: str | None, settings=None) -> str:
    profile = source_profile(kind, settings)
    if profile is not None:
        return profile.symbol
    return _DEFAULT_SYMBOLS[normalize_data_source_kind(kind, settings)]


def create_data_source(kind: str | None, settings=None) -> DataSource:
    """Instantiate a fresh data source for *kind* (not connected)."""
    if kind and kind.startswith("custom:"):
        from pa_agent.data.configured_source import ConfiguredSource
        profile = source_profile(kind, settings)
        if profile is None:
            raise ValueError("该数据源已删除，请重新选择。")
        return ConfiguredSource(profile)
    normalized = normalize_data_source_kind(kind, settings)
    if normalized == "tradingview":
        from pa_agent.config.paths import SETTINGS_JSON_PATH
        from pa_agent.config.settings import load_settings
        from pa_agent.data.tradingview import TradingViewSource

        settings = settings or load_settings(SETTINGS_JSON_PATH)
        return TradingViewSource(
            username=settings.tradingview.username,
            password=settings.tradingview.password,
        )
    if normalized == "ashare":
        from pa_agent.data.eastmoney_source import EastMoneySource

        return EastMoneySource()
    if normalized == "eastmoney_futures":
        from pa_agent.data.eastmoney_futures_source import EastMoneyFuturesSource

        return EastMoneyFuturesSource()
    if normalized == "tushare":
        from pa_agent.config.paths import SETTINGS_JSON_PATH
        from pa_agent.config.settings import load_settings
        from pa_agent.data.tushare_source import TushareSource

        return TushareSource(settings=settings or load_settings(SETTINGS_JSON_PATH))
    if normalized == "akshare":
        from pa_agent.data.akshare_source import AkShareSource

        return AkShareSource()
    if normalized == "yfinance":
        from pa_agent.data.yfinance_source import YFinanceSource

        return YFinanceSource()
    from pa_agent.data.mt5 import MT5Source

    return MT5Source()
