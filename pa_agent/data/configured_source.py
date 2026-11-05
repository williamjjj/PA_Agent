"""CSV, HTTP and MCP market feeds exposing the existing DataSource contract."""

from __future__ import annotations

import csv
import math
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from pa_agent.data.base import DataSource, DataSourceTransientError as DataSourceError, KlineBar
from pa_agent.data.bar_close_wait import timeframe_to_seconds
from pa_agent.data.connections import (
    ConnectionError,
    mcp_request,
    request_json,
    validate_profile,
    value_at,
)
from pa_agent.data.source_config import SourceProfile


def parse_market_rows(rows, profile: SourceProfile, timeframe: str) -> list[KlineBar]:
    if not isinstance(rows, list) or not rows:
        raise DataSourceError("未返回 K 线数组，请检查数据路径、品种和参数。")
    if len(rows) > 100_000:
        raise DataSourceError("最多读取 100000 行，请缩小数据文件或接口范围。")
    now = time.time() * 1000
    step = timeframe_to_seconds(timeframe)
    if not step:
        raise DataSourceError("不支持此 K 线周期。")
    bars = []
    seen = set()
    for index, row in enumerate(rows, 1):
        try:
            raw = value_at(row, profile.columns["time"])
            try:
                ts = float(raw)
                if ts > 100_000_000_000_000:  # Unix microseconds
                    ts /= 1000
                elif ts < 100_000_000_000:
                    ts *= 1000
            except (TypeError, ValueError):
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                ts = (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).timestamp() * 1000
            o, h, l, c, v = (
                float(value_at(row, profile.columns[key]))
                for key in ("open", "high", "low", "close", "volume")
            )
            if not all(math.isfinite(n) for n in (ts, o, h, l, c, v)):
                raise ValueError
            if not 0 < ts <= now or ts in seen or not 0 < l <= min(o, c) <= max(o, c) <= h or v < 0:
                raise ValueError
            closed = ts + step * 1000 <= now
            if profile.columns.get("closed"):
                flag = str(value_at(row, profile.columns["closed"])).lower()
                if flag not in ("0", "1", "false", "true"):
                    raise ValueError
                closed = closed and flag in ("1", "true")
            bars.append(
                KlineBar(seq=0, ts_open=ts, open=o, high=h, low=l, close=c, volume=v, closed=closed)
            )
            seen.add(ts)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise DataSourceError(
                f"第 {index} 行无效：请检查时间、字段映射、价格范围、成交量或重复时间。"
            ) from None
    bars.sort(key=lambda bar: bar.ts_open, reverse=True)
    return [replace(bar, seq=i + 1) for i, bar in enumerate(bars)]


class ConfiguredSource(DataSource):
    def __init__(self, profile: SourceProfile):
        self.profile = profile.model_copy(deep=True)
        self._connected = False
        self._symbol = ""
        self._timeframe = ""
        self._cache = []
        self._cache_at = 0.0
        self._cache_n = 0

    def connect(self):
        validate_profile(self.profile)
        if self.profile.category != "market":
            raise DataSourceError("该连接是资讯来源，请在资讯窗口查询。")
        self._connected = True

    def disconnect(self):
        self._connected = False
        self.unsubscribe()

    def list_symbols(self):
        return [self.profile.symbol]

    def supported_timeframes(self):
        return (
            [self.profile.timeframe]
            if self.profile.transport == "csv"
            else list(self.profile.timeframe_map)
        )

    def subscribe(self, symbol, timeframe):
        if timeframe not in self.supported_timeframes():
            raise DataSourceError("此数据源不支持当前周期，请选择已配置的周期。")
        if self.profile.transport == "csv" and symbol != self.profile.symbol:
            raise DataSourceError("CSV 文件对应固定品种，请在数据源设置中修改品种。")
        self._symbol, self._timeframe = symbol, timeframe
        self._cache = []
        self._cache_at = 0.0

    def unsubscribe(self):
        self._symbol = self._timeframe = ""
        self._cache = []
        self._cache_at = 0.0

    def latest_snapshot(self, n):
        if not self._connected or not self._symbol:
            raise DataSourceError("请先连接数据源并选择品种。")
        if (
            self._cache
            and n <= self._cache_n
            and time.monotonic() - self._cache_at < self.profile.poll_seconds
        ):
            return self._cache[:n]
        try:
            if self.profile.transport == "csv":
                path = Path(self.profile.csv_path).expanduser()
                if path.stat().st_size > 16_000_000:
                    raise DataSourceError("CSV 文件最大 16 MB。")
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
            else:
                variables = {
                    "symbol": self._symbol,
                    "timeframe": self.profile.timeframe_map[self._timeframe],
                    "limit": str(min(n, self.profile.max_bars)),
                    "query": self._symbol,
                }
                response = (
                    request_json(self.profile, variables)
                    if self.profile.transport == "http"
                    else mcp_request(self.profile, variables=variables)
                )
                rows = value_at(response, self.profile.rows_path)
            self._cache = parse_market_rows(rows, self.profile, self._timeframe)
            self._cache_at = time.monotonic()
            self._cache_n = n
            return self._cache[:n]
        except (OSError, UnicodeError):
            raise DataSourceError("无法读取 CSV，请检查文件路径、权限及 UTF-8 编码。") from None
        except ConnectionError as exc:
            raise DataSourceError(str(exc)) from None
