"""Fresh source objects and immutable snapshots for each account/request."""
from __future__ import annotations

import importlib
import importlib.util
import math
import time
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pa_agent.data.base import KlineBar, KlineFrame
from pa_agent.data.kline_adjust import set_kline_adjust
from pa_agent.data.snapshot import compute_indicators, take_snapshot_from_bars

SOURCE_SPECS = {
    "yfinance": ("Yahoo Finance", "yfinance_source", "YFinanceSource", "yfinance", "GC=F", ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"]),
    "tradingview": ("TradingView", "tradingview", "TradingViewSource", "tvDatafeed", "XAUUSD", ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"]),
    "eastmoney": ("东方财富 A股", "eastmoney_source", "EastMoneySource", "requests", "600519", ["1m", "5m", "15m", "30m", "1h", "1d"]),
    "eastmoney_futures": ("东方财富期货", "eastmoney_futures_source", "EastMoneyFuturesSource", "akshare", "RB0", ["1m", "5m", "15m", "30m", "1h", "1d"]),
    "akshare": ("AkShare A股", "akshare_source", "AkShareSource", "akshare", "600519", ["1m", "5m", "15m", "30m", "1h", "1d"]),
    "tushare": ("Tushare Pro", "tushare_source", "TushareSource", "tushare", "600519", ["1m", "5m", "15m", "30m", "1h", "1d"]),
}


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["yfinance", "tradingview", "eastmoney", "eastmoney_futures", "akshare", "tushare"]
    symbol: str = Field(pattern=r"^[A-Za-z0-9._:=^/-]{1,32}$")
    timeframe: str = Field(pattern=r"^(1m|5m|15m|30m|1h|4h|1d|1w|1M)$")
    exchange: str = Field(default="OANDA", pattern=r"^[A-Za-z0-9_]{1,24}$")
    count: int = Field(default=100, ge=50, le=500)


def catalog():
    return [{"id": key, "label": s[0], "available": importlib.util.find_spec(s[3]) is not None,
             "symbol": s[4], "timeframes": s[5]} for key, s in SOURCE_SPECS.items()]


def fetch_frame(data: SourceRequest, settings):
    spec = SOURCE_SPECS[data.source]
    if data.timeframe not in spec[5]:
        raise ValueError("该数据源不支持所选周期。")
    cls = getattr(importlib.import_module("pa_agent.data." + spec[1]), spec[2])
    if data.source == "tradingview":
        source = cls(settings.tradingview_username, settings.tradingview_password)
        source.set_exchange(data.exchange)
    elif data.source == "tushare":
        if not settings.tushare_token:
            raise ValueError("请在个人设置中填写 Tushare Token。")
        source = cls(settings=settings.core())
    else:
        source = cls()
    set_kline_adjust(settings.kline_adjust)
    try:
        source.connect()
        source.subscribe(data.symbol, data.timeframe)
        raw = source.latest_snapshot(data.count + 60)
        frame = take_snapshot_from_bars(raw, data.count, data.symbol, data.timeframe)
        times = [b.ts_open for b in frame.bars]
        if len(set(times)) != len(times) or times != sorted(times, reverse=True):
            raise ValueError("数据源返回了重复或未排序的 K 线。")
        for bar in frame.bars:
            values = (bar.ts_open, bar.open, bar.high, bar.low, bar.close, bar.volume)
            if (not all(math.isfinite(v) for v in values) or
                    not 0 < bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high or
                    bar.volume < 0):
                raise ValueError("数据源返回了无效 K 线。")
        return frame
    finally:
        source.disconnect()


def restore_frame(chart):
    fields = KlineBar.__dataclass_fields__
    bars = tuple(KlineBar(**{k: v for k, v in b.items() if k in fields})
                 for b in reversed(chart["bars"]))
    return KlineFrame(chart["symbol"], chart["timeframe"], bars,
                      compute_indicators(list(bars)), int(time.time()*1000))


def incremental_count(frame, previous_chart, maximum):
    if not previous_chart or maximum == 0:
        return None
    if (frame.symbol, frame.timeframe) != (previous_chart["symbol"], previous_chart["timeframe"]):
        return None
    old = {b["ts_open"]: b for b in previous_chart["bars"]}
    newest_old = max(old)
    new_count = sum(b.ts_open > newest_old for b in frame.bars)
    if not 0 < new_count <= maximum:
        return None
    overlap = [b for b in frame.bars if b.ts_open in old]
    if len(overlap) < 20:
        return None
    for b in overlap:
        if any(asdict(b)[k] != old[b.ts_open][k] for k in ("open", "high", "low", "close", "volume")):
            return None
    return new_count
