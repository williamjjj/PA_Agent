"""Validated immutable OHLCV datasets for bounded, reproducible research."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import time
from datetime import UTC, datetime
from itertools import pairwise
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pa_agent.data.base import KlineBar, KlineFrame
from pa_agent.data.snapshot import compute_indicators
from pa_agent.web import birdeye
from pa_agent.web.data import chart_data

DATASET_VERSION = "pa-research-ohlcv-v1"
MAX_CSV_BYTES = 2_000_000
MIN_BARS, MAX_BARS = 300, 5000
Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
_REQUIRED = {"time", "open", "high", "low", "close", "volume"}


class ResearchDataError(ValueError):
    """Safe fixed validation messages; uploaded values are never reflected."""


class DatasetImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv: str = Field(min_length=1, max_length=MAX_CSV_BYTES)
    symbol: str = Field(default="BTCUSD", pattern=r"^[A-Za-z0-9._:=^/-]{1,32}$")
    timeframe: Timeframe = "15m"
    market_kind: Literal["continuous_spot"] = "continuous_spot"
    instrument_id: str = Field(default="", max_length=120, pattern=r"^[A-Za-z0-9._:=^/-]*$")

    @field_validator("csv")
    @classmethod
    def bounded_csv(cls, value):
        if len(value.encode("utf-8")) > MAX_CSV_BYTES:
            raise ValueError("研究 CSV 不可超过 2 MB。")
        return value


class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str = Field(min_length=32, max_length=44)
    symbol: str = Field(default="SOL", pattern=r"^[A-Za-z0-9._:=^/-]{1,32}$")
    chain: Literal["solana"] = "solana"
    timeframe: Timeframe = "15m"
    start: int = Field(strict=True, ge=1)
    end: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def closed_bounded_interval(self):
        if not birdeye.valid_solana_address(self.instrument_id):
            raise ValueError("请填写有效的 Solana 代币合约地址（32 字节 Base58）。")
        step = birdeye.TIMEFRAMES[self.timeframe][1]
        if self.start % step or self.end % step or self.end <= self.start:
            raise ValueError("研究时间须对齐 K 线周期，结束时间须晚于开始时间。")
        if self.end > int(time.time()) // step * step:
            raise ValueError("研究时间只能包含已收盘 K 线，结束时间为不包含在内的 UTC 边界。")
        if not MIN_BARS <= (self.end - self.start) // step <= MAX_BARS:
            raise ValueError("研究范围须包含 300 至 5000 个 K 线时间区间。")
        return self


def _timestamp(raw):
    try:
        value = float(raw)
        if value > 100_000_000_000:
            value /= 1000
    except ValueError:
        parsed = datetime.fromisoformat(raw)
        value = (parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed).timestamp()
    if not math.isfinite(value) or value != int(value) or value <= 0:
        raise ResearchDataError("研究数据时间无效。")
    return int(value)


def _validated_row(ts, values, step, now):
    if ts % step or ts + step > now:
        raise ResearchDataError("研究数据仅接受对齐周期的已收盘 K 线。")
    if not all(math.isfinite(v) for v in values):
        raise ResearchDataError("研究数据价格或成交量无效。")
    opening, high, low, close, volume = values
    if (not 0 < low <= min(opening, close) <= max(opening, close) <= high <= 1e12
            or not 0 <= volume <= 1e18):
        raise ResearchDataError("研究数据价格或成交量无效。")
    return ts, opening, high, low, close, volume


def research_dataset_hash(chart):
    """Identify market input, excluding capture time and ingestion diagnostics."""
    instrument = {key: value for key, value in chart["instrument"].items() if key != "symbol"}
    quality = chart["quality"]
    values = {
        "version": DATASET_VERSION, "source": chart["source"], "instrument": instrument,
        "market_kind": chart["market_kind"], "timeframe": chart["timeframe"],
        "coverage": {"start": quality.get("requested_start", quality["start"]),
                     "end": quality.get("requested_end", quality["end"])},
        "bars": [[int(bar["ts_open"]),
                  *[float(bar[key]) for key in ("open", "high", "low", "close", "volume")]]
                 for bar in chart["bars"]],
    }
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=True, allow_nan=False,
                         separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _dataset(rows, *, symbol, timeframe, source, instrument, captured_at, provenance,
             duplicate_count=0, reordered=False, requested_start=None, requested_end=None):
    if not MIN_BARS <= len(rows) <= MAX_BARS:
        raise ResearchDataError("研究数据集需要 300 至 5000 根有效已收盘 K 线；缺失区间不会填充。")
    step = birdeye.TIMEFRAMES[timeframe][1]
    newest = [KlineBar(index + 1, row[0] * 1000, *row[1:], closed=True)
              for index, row in enumerate(reversed(rows))]
    frame = KlineFrame(symbol, timeframe, tuple(newest), compute_indicators(newest), captured_at)
    gaps = [{"start": first[0] + step, "end": second[0],
             "missing_intervals": (second[0] - first[0]) // step - 1}
            for first, second in pairwise(rows) if second[0] - first[0] != step]
    start, end = rows[0][0], rows[-1][0] + step
    if requested_start is not None:
        if start > requested_start:
            gaps.insert(0, {"start": requested_start, "end": start,
                            "missing_intervals": (start - requested_start) // step})
        if end < requested_end:
            gaps.append({"start": end, "end": requested_end,
                         "missing_intervals": (requested_end - end) // step})
    missing = sum(gap["missing_intervals"] for gap in gaps)
    chart = {
        **chart_data(frame), "source": source, "instrument": instrument,
        "market_kind": "continuous_spot", "captured_at": captured_at,
        "dataset_version": DATASET_VERSION,
        "quality": {"bar_count": len(rows), "start": start, "end": end,
                    "time_unit": "unix_seconds", "interval_seconds": step,
                    "gap_count": len(gaps), "gaps": gaps[:100], "missing_intervals": missing,
                    "zero_volume_count": sum(row[-1] == 0 for row in rows),
                    "duplicate_count": duplicate_count, "reordered": reordered,
                    "closed_only": True},
        "provenance": provenance,
    }
    if requested_start is not None:
        chart["quality"].update(requested_start=requested_start, requested_end=requested_end)
    chart["dataset_hash"] = research_dataset_hash(chart)
    return chart


def _csv_rows(reader):
    try:
        yield from reader
    except csv.Error:
        raise ResearchDataError("研究 CSV 格式无效或单个字段过长。") from None


def parse_dataset_csv(data: DatasetImport, *, source: Literal["csv", "demo"] = "csv"):
    """Sort valid input and remove only byte-independent exact OHLCV duplicates."""
    if source not in ("csv", "demo"):
        raise ResearchDataError("研究 CSV 来源无效。")
    reader = csv.DictReader(io.StringIO(data.csv.lstrip("\ufeff")))
    try:
        headers = reader.fieldnames
    except csv.Error:
        raise ResearchDataError("研究 CSV 格式无效或单个字段过长。") from None
    if not headers or len(headers) != 6 or set(headers) != _REQUIRED:
        raise ResearchDataError("研究 CSV 必须包含且仅包含 time,open,high,low,close,volume 六列。")
    now = time.time()
    step = birdeye.TIMEFRAMES[data.timeframe][1]
    rows, order, duplicates = {}, [], 0
    for index, row in enumerate(_csv_rows(reader), 2):
        if index > 10_001:
            raise ResearchDataError("研究 CSV 最多接受 10000 行输入及 5000 根去重后的 K 线。")
        try:
            if None in row or any(value is None for value in row.values()):
                raise ValueError
            ts = _timestamp(row["time"])
            values = [float(row[key]) for key in ("open", "high", "low", "close", "volume")]
            bar = _validated_row(ts, values, step, now)
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            raise ResearchDataError(f"研究 CSV 第 {index} 行无效；请检查价格、成交量、周期对齐和已收盘时间。") from None
        if ts in rows:
            if rows[ts] != bar:
                raise ResearchDataError("研究 CSV 同一时间存在相互冲突的 K 线。")
            duplicates += 1
        else:
            rows[ts] = bar
            order.append(ts)
            if len(rows) > MAX_BARS:
                raise ResearchDataError("研究数据集最多支持 5000 根 K 线。")
    identity = data.instrument_id or data.symbol
    instrument = {"id": f"{source}:{identity}", "symbol": data.symbol,
                  "asset_class": "unspecified", "venue": source, "chain": None,
                  "address": None, "quote_asset": "unspecified", "volume_unit": "provider_reported",
                  "declared_instrument_id": data.instrument_id or None, "verified": False}
    return _dataset([rows[ts] for ts in sorted(rows)], symbol=data.symbol, timeframe=data.timeframe,
                    source=source, instrument=instrument, captured_at=int(now * 1000),
                    provenance={"kind": "simulated" if source == "demo" else "user_supplied",
                                "market_identity_verified": False, "timestamps": "UTC",
                                "missing_data_policy": "preserve_gaps_no_fill",
                                "market_kind_declaration": data.market_kind},
                    duplicate_count=duplicates, reordered=order != sorted(order))


def dataset_from_snapshot(chart, *, market_kind="continuous_spot"):
    """Canonicalize an owned frozen snapshot without refetching its market data."""
    from pa_agent.web.snapshots import instrument_for_chart

    if market_kind != "continuous_spot" or chart.get("source") not in ("csv", "demo", "birdeye"):
        raise ResearchDataError("当前研究仅支持声明为连续现货的 CSV、示例或 Birdeye 数据。")
    timeframe = chart.get("timeframe")
    if timeframe not in birdeye.TIMEFRAMES:
        raise ResearchDataError("当前研究仅支持 1 分钟至 1 日的固定 K 线周期。")
    step = birdeye.TIMEFRAMES[timeframe][1]
    captured = chart.get("captured_at", int(time.time() * 1000))
    rows = []
    try:
        if isinstance(captured, bool) or not math.isfinite(captured) or captured <= 0:
            raise ValueError
        for bar in chart["bars"]:
            values = [bar[key] for key in ("ts_open", "open", "high", "low", "close", "volume")]
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
                raise ValueError
            if bar.get("closed") is not True:
                raise ValueError
            ts = values[0] / 1000
            if not math.isfinite(ts) or ts != int(ts) or ts <= 0:
                raise ValueError
            row = _validated_row(int(ts), values[1:], step, min(captured / 1000, time.time()))
            if rows and row[0] <= rows[-1][0]:
                raise ValueError
            rows.append(row)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ResearchDataError("快照包含无效、未收盘、重复或未排序的研究 K 线。") from None
    quality = chart.get("quality", {})
    requested_start, requested_end = quality.get("requested_start"), quality.get("requested_end")
    if (requested_start is not None or requested_end is not None) and (
            not rows or any(isinstance(value, bool) or not isinstance(value, int)
                            for value in (requested_start, requested_end))
            or requested_start % step or requested_end % step
            or requested_start > rows[0][0] or requested_end < rows[-1][0] + step):
        raise ResearchDataError("快照声明的研究覆盖区间无效。")
    return _dataset(rows, symbol=chart["symbol"], timeframe=timeframe, source=chart["source"],
                    instrument=instrument_for_chart(chart), captured_at=int(captured),
                    requested_start=requested_start, requested_end=requested_end,
                    provenance={"kind": "frozen_snapshot", "source": chart["source"],
                                "original_dataset_hash": chart.get("dataset_hash"),
                                "original_snapshot_id": chart.get("snapshot_id"),
                                "timestamps": "UTC", "market_kind_declaration": market_kind,
                                "missing_data_policy": "preserve_gaps_no_fill"})


class _RequestBudget:
    def __init__(self, client):
        self.client, self.count = client, 0

    def get(self, *args, **kwargs):
        if self.count >= 25:
            raise birdeye.BirdeyeError("Birdeye 历史请求已达 25 次上限，请缩小时间范围后重试。")
        self.count += 1
        return self.client.get(*args, **kwargs)


def fetch_history(data: HistoryRequest, settings):
    """At most 25 HTTP attempts, with one shared 60-second request budget."""
    if not settings.birdeye_api_key:
        raise birdeye.BirdeyeError("请在个人设置中填写 Birdeye API Key。")
    # Revalidate time bounds when a previously constructed request is reused.
    data = HistoryRequest.model_validate(data.model_dump())
    now = time.time()
    api_type, step = birdeye.TIMEFRAMES[data.timeframe]
    rows = {}
    deadline = time.monotonic() + 60
    with httpx.Client(headers={"X-API-KEY": settings.birdeye_api_key, "x-chain": data.chain,
                               "accept": "application/json"},
                      trust_env=True, follow_redirects=False) as client:
        budget = _RequestBudget(client)
        start = data.start
        while start < data.end:
            end = min(data.end, start + 200 * step)
            items = birdeye._items(budget, {"address": data.instrument_id, "type": api_type,
                                           "time_from": start, "time_to": end - 1}, deadline)
            if len(items) > (end - start) // step:
                raise birdeye.BirdeyeError("Birdeye 返回了重复或越界的历史 K 线。")
            for item in items:
                try:
                    ts = birdeye._number(item["unixTime"])
                    if ts != int(ts) or not start <= ts < end or ts in rows:
                        raise ValueError
                    values = [birdeye._number(item[key]) for key in ("o", "h", "l", "c", "v")]
                    rows[ts] = _validated_row(int(ts), values, step, now)
                except (ValueError, TypeError, KeyError, OverflowError):
                    raise birdeye.BirdeyeError("Birdeye 返回了无效、重复或越界的历史 K 线。") from None
            start = end
    instrument = {"id": f"solana:{data.instrument_id}", "asset_class": "crypto",
                  "venue": "birdeye", "chain": "solana", "address": data.instrument_id,
                  "symbol": data.symbol, "quote_asset": "USD", "volume_unit": "provider_reported"}
    return _dataset([rows[ts] for ts in sorted(rows)], symbol=data.symbol, timeframe=data.timeframe,
                    source="birdeye", instrument=instrument, captured_at=int(now * 1000),
                    requested_start=data.start, requested_end=data.end,
                    provenance={"kind": "provider_history", "endpoint": birdeye.ENDPOINT,
                                "provider": "birdeye", "chain": data.chain,
                                "request_count": budget.count, "timestamps": "UTC",
                                "missing_data_policy": "preserve_gaps_no_fill",
                                "historical_liquidity": "unavailable", "historical_fdv": "unavailable"})
