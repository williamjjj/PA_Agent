from __future__ import annotations

import csv
import io
import math
import random
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pa_agent.data.base import KlineBar, KlineFrame
from pa_agent.data.snapshot import compute_indicators

MAX_BODY = 600_000


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv: str = Field(min_length=1, max_length=500_000)
    symbol: str = Field(default="XAUUSD", pattern=r"^[A-Za-z0-9._:/-]{1,24}$")
    timeframe: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"] = "15m"
    source: Literal["csv", "demo"] = "csv"


def parse_csv(data: ImportRequest) -> KlineFrame:
    reader = csv.DictReader(io.StringIO(data.csv.lstrip("\ufeff")))
    required = {"time", "open", "high", "low", "close", "volume"}
    if not reader.fieldnames or set(reader.fieldnames) != required:
        raise ValueError("CSV 必须包含且仅包含 time,open,high,low,close,volume 六列。")
    bars = []
    previous = 0.0
    seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}[data.timeframe]
    for index, row in enumerate(reader, 2):
        if len(bars) >= 500:
            raise ValueError("最多导入 500 根 K 线。")
        try:
            raw_time = row["time"]
            try:
                ts = float(raw_time)
                if ts > 100_000_000_000:
                    ts /= 1000
            except ValueError:
                dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                ts = dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
            o, h, l, c, v = (float(row[k]) for k in ("open", "high", "low", "close", "volume"))
            if not all(math.isfinite(x) for x in (ts, o, h, l, c, v)):
                raise ValueError
            if not 0 < l <= min(o, c) <= max(o, c) <= h <= 1e12 or not 0 <= v <= 1e18:
                raise ValueError
            if ts <= previous or ts + seconds > time.time():
                raise ValueError
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ValueError(f"第 {index} 行无效：请检查价格范围、成交量和时间；时间须升序且不重复，只接受已收盘 K 线。") from None
        previous = ts
        bars.append(KlineBar(seq=0, ts_open=ts * 1000, open=o, high=h, low=l, close=c, volume=v))
    if len(bars) < 50:
        raise ValueError("至少需要 50 根已收盘 K 线，以计算 EMA20 和 ATR14。")
    newest = [KlineBar(**{**asdict(b), "seq": i + 1}) for i, b in enumerate(reversed(bars))]
    return KlineFrame(data.symbol, data.timeframe, tuple(newest), compute_indicators(newest), int(time.time() * 1000))


def chart_data(frame: KlineFrame) -> dict:
    return {
        "symbol": frame.symbol,
        "timeframe": frame.timeframe,
        "bars": [
            {**asdict(b), "time": int(b.ts_open / 1000),
             "ema": float(e) if math.isfinite(e) else None,
             "atr": float(a) if math.isfinite(a) else None}
            for b, e, a in reversed(list(zip(frame.bars, frame.indicators.ema20, frame.indicators.atr14)))
        ],
    }


def demo_csv() -> str:
    rng = random.Random(42)
    rows = ["time,open,high,low,close,volume"]
    price = 2632.0
    for i in range(160):
        opening = price
        price += 0.28 + math.sin(i / 9) * 1.1 + rng.uniform(-2.5, 2.5)
        high = max(opening, price) + rng.uniform(0.4, 2.8)
        low = min(opening, price) - rng.uniform(0.4, 2.8)
        rows.append(f"{1727683200 + i * 900},{opening:.2f},{high:.2f},{low:.2f},{price:.2f},{rng.randint(80, 900)}")
    return "\n".join(rows)
