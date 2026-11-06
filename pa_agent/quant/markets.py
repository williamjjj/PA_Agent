"""Shared market identity and bar-clock conventions for local research."""
from typing import Literal

MarketKind = Literal["continuous_spot", "stock", "futures", "forex"]
Timeframe = Literal["1m", "3m", "5m", "10m", "15m", "30m", "45m", "1h", "2h", "4h",
                    "6h", "8h", "12h", "1d", "3d", "1w"]
TIMEFRAME_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "10m": 600, "15m": 900,
                     "30m": 1800, "45m": 2700, "1h": 3600, "2h": 7200, "4h": 14400,
                     "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400,
                     "3d": 259200, "1w": 604800}
MARKET_LABELS = {"continuous_spot": "虚拟币现货（连续交易）", "stock": "股票 / ETF",
                 "futures": "期货 / 永续合约", "forex": "外汇 / 贵金属"}


def infer_market(source, symbol="", exchange=""):
    exchange = str(exchange).upper()
    if "future" in source or exchange in {"CME", "CME_MINI", "CBOT", "COMEX", "NYMEX", "SHFE", "DCE", "CZCE", "CFFEX", "INE"} or str(symbol).endswith(("!", ".P")):
        return "futures"
    if source in {"ashare", "akshare", "tushare", "yfinance"} or exchange in {"NASDAQ", "NYSE", "AMEX", "ARCA", "SSE", "SZSE", "HKEX"}:
        return "stock"
    if source == "mt5" or exchange in {"OANDA", "FOREXCOM", "FX", "PEPPERSTONE", "CAPITALCOM"}:
        return "forex"
    return "continuous_spot"


def bar_clock(market_kind):
    return "continuous_utc" if market_kind == "continuous_spot" else "observed_trading_bars"
