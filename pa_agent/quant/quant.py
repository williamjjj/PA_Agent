"""Causal OHLCV adaptation of AlphaGPT's six basic factor definitions.

Modified from AlphaGPT ``model_core/factors.py``: no torch, circular lags,
zero-padded averages, or whole-series normalization. See
``docs/ALPHAGPT_FACTORS.md`` and ``third_party/AlphaGPT`` for provenance and
the Apache-2.0 license. This version cannot consume legacy trained formulas.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from statistics import median

from pa_agent.data.base import KlineBar, KlineFrame

FEATURE_VERSION = "alphagpt-causal-v1"
WINDOW = 60
MIN_PERIODS = 20
CLIP = 5.0
_DURATIONS_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000,
}
_META = (
    ("RET", "对数收益", "相邻已收盘 K 线的收盘价对数收益；时间间隔不连续时留空。", "log_return"),
    ("LIQ", "流动性健康度", "需要同一历史时点的流动性和 FDV；OHLCV 无法计算。", "ratio"),
    ("PRESSURE", "蜡烛压力代理", "tanh(3 × 实体 / 振幅)，仅是蜡烛形态代理，不是真实买卖订单流。", "score"),
    ("FOMO", "量能加速度", "相邻两次成交量变化率之差，截断到 [-5, 5]；需要连续三根 K 线。", "score"),
    ("DEV", "均价偏离", "收盘价相对最近 20 根已收盘 K 线简单均价的偏离。", "ratio"),
    ("VOL", "对数成交量", "log(1 + 成交量)；原始成交量单位随行情来源而异。", "log_volume"),
)


def _bar_end_ms(bar: KlineBar, timeframe: str) -> int:
    if timeframe in _DURATIONS_MS:
        return int(bar.ts_open + _DURATIONS_MS[timeframe])
    if timeframe == "1M":
        dt = datetime.fromtimestamp(bar.ts_open / 1000, UTC)
        end = dt.replace(year=dt.year + (dt.month == 12), month=dt.month % 12 + 1,
                         day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(end.timestamp() * 1000)
    raise ValueError("量化因子不支持该 K 线周期。")


def _validated_bars(frame: KlineFrame) -> tuple[list[KlineBar], list[int]]:
    """Reject bad snapshots instead of silently repairing or filtering their bars."""
    if frame.timeframe not in {*_DURATIONS_MS, "1M"}:
        raise ValueError("量化因子不支持该 K 线周期。")
    if (not isinstance(frame.snapshot_ts_local_ms, (int, float)) or
            isinstance(frame.snapshot_ts_local_ms, bool) or
            not math.isfinite(frame.snapshot_ts_local_ms) or
            frame.snapshot_ts_local_ms <= 0):
        raise ValueError("行情快照时间无效。")
    bars = list(reversed(frame.bars))
    ends = []
    previous = None
    for bar in bars:
        values = (bar.ts_open, bar.open, bar.high, bar.low, bar.close, bar.volume)
        if (any(not isinstance(v, (int, float)) or isinstance(v, bool) or
                not math.isfinite(v) for v in values) or
                not 0 < bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <=
                bar.high <= 1e12 or not 0 <= bar.volume <= 1e18 or bar.ts_open <= 0):
            raise ValueError("量化因子需要有效的价格、成交量和 UTC 毫秒时间。")
        if bar.closed is not True:
            raise ValueError("量化因子仅接受已收盘 K 线。")
        if previous is not None and bar.ts_open <= previous:
            raise ValueError("行情快照必须按新到旧排序，且时间不能重复。")
        try:
            end = _bar_end_ms(bar, frame.timeframe)
        except (ValueError, OverflowError, OSError):
            raise ValueError("行情 K 线时间超出有效范围。") from None
        if end > frame.snapshot_ts_local_ms:
            raise ValueError("行情快照包含尚未收盘的 K 线。")
        if ends and bar.ts_open < ends[-1] - 1:
            raise ValueError("行情快照包含时间重叠的 K 线。")
        previous = bar.ts_open
        ends.append(end)
    return bars, ends


def _normalize(values: list[float | None]) -> tuple[list[float | None], list[float | None]]:
    normalized: list[float | None] = []
    percentiles: list[float | None] = []
    for index, value in enumerate(values):
        history = [v for v in values[max(0, index + 1 - WINDOW):index + 1] if v is not None]
        if value is None or len(history) < MIN_PERIODS:
            normalized.append(None)
            percentiles.append(None)
            continue
        center = median(history)
        mad = median(abs(v - center) for v in history)
        normalized.append(max(-CLIP, min(CLIP, (value - center) / (mad + 1e-6))))
        # Midrank ties keep a constant series at the neutral 50th percentile.
        percentiles.append(100.0 * (sum(v < value for v in history) +
                                   0.5 * sum(v == value for v in history)) / len(history))
    return normalized, percentiles


def compute_factors(frame: KlineFrame) -> dict:
    """Return finite, causal factors over exactly the supplied immutable snapshot.

    Invalid/forming bars raise ``ValueError``. Empty valid snapshots produce an
    unavailable result. No external data, user settings or credentials are read.
    Windows use observed candles. Without a market calendar, interval breaks
    cannot distinguish closed sessions from missing data; lagged values across
    those boundaries are withheld, but observed-candle windows remain usable.
    """
    bars, ends = _validated_bars(frame)
    count = len(bars)
    adjacent = [False] + [abs(bars[i].ts_open - ends[i - 1]) <= 1
                          for i in range(1, count)] if count else []
    raw: dict[str, list[float | None]] = {item[0]: [] for item in _META}
    for index, bar in enumerate(bars):
        ret = (math.log(bar.close) - math.log(bars[index - 1].close)
               if adjacent[index] else None)
        fomo = None
        if index >= 2 and adjacent[index] and adjacent[index - 1]:
            previous, earlier = bars[index - 1], bars[index - 2]
            current_growth = (bar.volume - previous.volume) / (previous.volume + 1.0)
            previous_growth = (previous.volume - earlier.volume) / (earlier.volume + 1.0)
            fomo = max(-CLIP, min(CLIP, current_growth - previous_growth))
        dev = None
        if index >= 19:
            mean = math.fsum(b.close / 20 for b in bars[index - 19:index + 1])
            dev = (bar.close - mean) / mean
        raw["RET"].append(ret)
        raw["LIQ"].append(None)
        raw["PRESSURE"].append(math.tanh(3.0 * (bar.close - bar.open) /
                                         (bar.high - bar.low + 1e-9)))
        raw["FOMO"].append(fomo)
        raw["DEV"].append(dev)
        raw["VOL"].append(math.log1p(bar.volume))

    factors = []
    for name, label, description, unit in _META:
        values = raw[name]
        normalized, percentiles = _normalize(values)
        value = values[-1] if count else None
        norm = normalized[-1] if count else None
        change = (value - values[-2] if count >= 2 and adjacent[-1] and
                  value is not None and values[-2] is not None else None)
        status, reason = "ready", None
        if not count:
            status, reason = "unavailable", "快照没有已收盘 K 线。"
        elif name == "LIQ":
            status, reason = "unavailable", "OHLCV 不含历史流动性与 FDV，未使用当前值回填。"
        elif value is None and (name == "RET" and count >= 2 or
                                name == "FOMO" and count >= 3):
            status, reason = "unavailable", "时间间隔不连续；未跨越该间隔计算滞后值。"
        elif norm is None:
            status, reason = "warming_up", "原始窗口或最近 60 根内的 20 个有效因子值尚未满足。"
        factors.append({
            "id": name, "label": label, "description": description, "unit": unit,
            "value": value, "normalized": norm, "change": change,
            "percentile": percentiles[-1] if count else None,
            "status": status, "reason": reason,
            "series": [{"time": bar.ts_open / 1000, "value": values[i],
                        "normalized": normalized[i], "percentile": percentiles[i]}
                       for i, bar in enumerate(bars)],
        })
    flags = [{"code": "liquidity_history_unavailable",
              "message": "此快照仅含 OHLCV，LIQ 不可计算；未把缺失值当作零。"},
             {"code": "pressure_is_candle_proxy",
              "message": "压力因子是蜡烛形态代理，不代表真实买卖成交或订单流。"},
             {"code": "volume_unit_source_defined",
              "message": "成交量单位取决于行情来源；不同来源或市场的原始量能不可直接比较。"}]
    discontinuities = sum(not adjacent[i] for i in range(1, count))
    if discontinuities:
        flags.append({"code": "calendar_unverified",
                      "message": f"发现 {discontinuities} 处时间间隔不连续；未提供交易日历，"
                                 "无法区分休市与缺失数据。滞后因子跨间隔留空，滚动窗口按已观测 K 线计数。"})
    if any(f["status"] == "warming_up" for f in factors):
        flags.append({"code": "insufficient_history",
                      "message": "部分因子尚在预热；归一化和历史分位需最近 60 根内至少 20 个有效值。"})
    available = sum(f["value"] is not None for f in factors)
    return {
        "feature_version": FEATURE_VERSION,
        "status": "unavailable" if not available else
                  "ready" if all(f["status"] == "ready" for f in factors) else "partial",
        "as_of": ends[-1] if count else None,
        "bar_count": count,
        "normalization": {"method": "trailing_median_mad", "window": WINDOW,
                          "min_periods": MIN_PERIODS, "clip": CLIP},
        "factors": factors,
        "quality_flags": flags,
    }
