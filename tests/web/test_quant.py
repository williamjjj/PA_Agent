"""Known vectors, causality and invalid-data checks for the factor adapter."""
import json
import math
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.web.quant import FEATURE_VERSION, compute_factors

START = 1_727_683_200_000
STEP = 60_000


def frame(closes=None, volumes=None, count=100):
    closes = closes if closes is not None else [100 + i + math.sin(i) for i in range(count)]
    volumes = volumes if volumes is not None else [100 + i * i for i in range(len(closes))]
    bars = [KlineBar(seq=len(closes) - i, ts_open=START + i * STEP,
                     open=c - 0.1, high=c + 0.5, low=c - 0.5, close=c, volume=volumes[i])
            for i, c in enumerate(closes)]
    return KlineFrame("TEST", "1m", tuple(reversed(bars)), IndicatorBundle((), ()),
                      START + (len(bars) + 1) * STEP)


def factors(result):
    return {f["id"]: f for f in result["factors"]}


def prefix(data, count):
    return replace(data, bars=data.bars[-count:])


def test_known_raw_vectors_and_explicit_warmup():
    values = factors(compute_factors(frame(closes=list(range(10, 50)),
                                         volumes=list(range(40)))))
    assert values["RET"]["series"][0]["value"] is None
    assert values["RET"]["series"][1]["value"] == pytest.approx(math.log(11 / 10))
    assert values["FOMO"]["series"][0]["value"] is None
    assert values["FOMO"]["series"][1]["value"] is None
    assert values["FOMO"]["series"][2]["value"] == pytest.approx(-0.5)
    assert values["PRESSURE"]["series"][0]["value"] == pytest.approx(math.tanh(0.3))
    assert values["DEV"]["series"][18]["value"] is None
    assert values["DEV"]["series"][19]["value"] == pytest.approx((29 - 19.5) / 19.5)
    assert values["VOL"]["series"][1]["value"] == pytest.approx(math.log(2))
    # RET needs 20 real returns, FOMO needs 20 real accelerations, DEV needs 20 averages.
    for name, first in (("RET", 20), ("FOMO", 21), ("DEV", 38), ("VOL", 19)):
        assert all(point["normalized"] is None for point in values[name]["series"][:first])
        assert values[name]["series"][first]["normalized"] is not None
        assert values[name]["series"][first - 1]["percentile"] is None
        assert values[name]["series"][first]["percentile"] is not None


def test_prefix_and_future_mutations_never_revise_historical_factor_points():
    original = frame()
    before = factors(compute_factors(prefix(original, 55)))
    changed_future = [replace(bar, open=bar.open * 20, high=bar.high * 20,
                              low=bar.low * 20, close=bar.close * 20,
                              volume=bar.volume * 50)
                      for bar in original.bars[:45]]
    mutated = replace(original, bars=tuple(changed_future) + original.bars[45:])
    for extended in (original, mutated):
        after = factors(compute_factors(extended))
        for name in before:
            assert after[name]["series"][:55] == before[name]["series"]


def test_normalization_uses_only_last_60_observed_bars_and_midrank_ties():
    volumes = [i * i for i in range(100)]
    result = factors(compute_factors(frame(volumes=volumes)))["VOL"]
    history = sorted(math.log1p(v) for v in volumes[-60:])
    middle = (history[29] + history[30]) / 2
    deviations = sorted(abs(v - middle) for v in history)
    mad = (deviations[29] + deviations[30]) / 2
    assert result["normalized"] == pytest.approx((history[-1] - middle) / (mad + 1e-6))
    assert result["percentile"] == pytest.approx(100 * 59.5 / 60)
    changed_oldest = frame(volumes=[1e12] * 40 + volumes[40:])
    assert factors(compute_factors(changed_oldest))["VOL"]["normalized"] == result["normalized"]


def test_constant_prices_and_zero_volume_stay_finite_and_liquidity_stays_absent():
    original = frame(closes=[100] * 80, volumes=[0] * 80)
    flat = replace(original, bars=tuple(replace(b, open=100, high=100, low=100)
                                        for b in original.bars))
    result = compute_factors(flat)
    assert result["feature_version"] == FEATURE_VERSION
    assert result["status"] == "partial"
    assert result["bar_count"] == 80
    assert result["as_of"] == START + 80 * STEP
    json.dumps(result, allow_nan=False)
    for name, factor in factors(result).items():
        if name == "LIQ":
            assert factor["status"] == "unavailable"
            assert factor["value"] is None
            assert factor["normalized"] is None
            assert factor["change"] is None
            assert all(point["value"] is None for point in factor["series"])
        else:
            assert factor["value"] == pytest.approx(0)
            assert factor["normalized"] == pytest.approx(0)
            assert factor["percentile"] == 50
            assert factor["change"] == pytest.approx(0)
    assert "liquidity_history_unavailable" in {flag["code"] for flag in result["quality_flags"]}


def test_extreme_but_valid_inputs_are_finite_and_clipped():
    result = compute_factors(frame(closes=[1.0] * 99 + [1e12 - 1],
                                   volumes=[0] * 99 + [1e18]))
    json.dumps(result, allow_nan=False)
    for factor in result["factors"]:
        if factor["normalized"] is not None:
            assert -5 <= factor["normalized"] <= 5
    assert factors(result)["FOMO"]["value"] == 5


def test_interval_discontinuity_does_not_invent_missing_candles_or_cross_lags():
    original = frame()
    bars = list(reversed(original.bars))
    bars = [replace(b, ts_open=b.ts_open + (2 * STEP if i >= 50 else 0))
            for i, b in enumerate(bars)]
    result = compute_factors(replace(original, bars=tuple(reversed(bars)),
                                     snapshot_ts_local_ms=original.snapshot_ts_local_ms + 2 * STEP))
    actual = factors(result)
    assert len(actual["RET"]["series"]) == 100
    assert actual["RET"]["series"][50]["value"] is None
    assert actual["RET"]["series"][51]["value"] is not None
    assert actual["FOMO"]["series"][50]["value"] is None
    assert actual["FOMO"]["series"][51]["value"] is None
    assert actual["FOMO"]["series"][52]["value"] is not None
    assert actual["DEV"]["series"][50]["value"] is not None
    assert "calendar_unverified" in {flag["code"] for flag in result["quality_flags"]}
    latest_gap = compute_factors(replace(original, bars=(replace(original.bars[0],
                                    ts_open=original.bars[0].ts_open + STEP),) + original.bars[1:]))
    assert factors(latest_gap)["RET"]["status"] == "unavailable"
    assert all(f["change"] is None for f in latest_gap["factors"])


def test_daily_weekend_is_flagged_as_unknown_calendar_not_claimed_missing_data():
    original = frame(count=40)
    start = datetime(2024, 9, 2, tzinfo=UTC).timestamp() * 1000  # Monday
    bars = [replace(b, ts_open=start + (i + 2 * (i // 5)) * 86_400_000)
            for i, b in enumerate(reversed(original.bars))]
    result = compute_factors(replace(original, timeframe="1d", bars=tuple(reversed(bars)),
                                     snapshot_ts_local_ms=bars[-1].ts_open + 86_400_000))
    actual = factors(result)
    assert actual["RET"]["series"][5]["value"] is None
    assert actual["DEV"]["status"] == "ready"
    assert actual["VOL"]["status"] == "ready"
    flag = next(flag for flag in result["quality_flags"] if flag["code"] == "calendar_unverified")
    assert "无法区分休市与缺失数据" in flag["message"]


def test_monthly_asof_uses_calendar_month_end_and_leap_year():
    original = frame(count=2)
    jan = datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000
    feb = datetime(2024, 2, 1, tzinfo=UTC).timestamp() * 1000
    march = int(datetime(2024, 3, 1, tzinfo=UTC).timestamp() * 1000)
    monthly = replace(original, timeframe="1M", snapshot_ts_local_ms=march,
                      bars=(replace(original.bars[0], ts_open=feb),
                            replace(original.bars[1], ts_open=jan)))
    result = compute_factors(monthly)
    assert result["as_of"] == march
    assert factors(result)["RET"]["value"] is not None
    assert "calendar_unverified" not in {flag["code"] for flag in result["quality_flags"]}


@pytest.mark.parametrize("field,value", [
    ("close", float("nan")), ("close", float("inf")), ("open", 0),
    ("close", -1), ("volume", -1), ("volume", float("nan")),
    ("volume", "100"), ("high", 2), ("low", 1e6),
    ("ts_open", float("inf")), ("ts_open", 0), ("closed", False),
])
def test_malformed_or_flagged_forming_bars_reject_entire_snapshot(field, value):
    original = frame()
    invalid = replace(original, bars=(replace(original.bars[0], **{field: value}),)
                      + original.bars[1:])
    with pytest.raises(ValueError):
        compute_factors(invalid)


def test_unsorted_duplicate_overlapping_or_unclosed_by_timestamp_bars_are_rejected():
    original = frame()
    invalid_frames = [
        replace(original, bars=tuple(reversed(original.bars))),
        replace(original, bars=(original.bars[0],) + original.bars),
        replace(original, bars=(replace(original.bars[0],
                                        ts_open=original.bars[0].ts_open - STEP / 2),)
                + original.bars[1:]),
        replace(original, snapshot_ts_local_ms=original.bars[0].ts_open + STEP - 1),
        replace(original, timeframe="unknown"),
        replace(original, snapshot_ts_local_ms=float("nan")),
    ]
    for invalid in invalid_frames:
        with pytest.raises(ValueError):
            compute_factors(invalid)


def test_empty_snapshot_has_no_fabricated_latest_values():
    result = compute_factors(frame(count=0))
    assert result["status"] == "unavailable"
    assert result["bar_count"] == 0
    assert result["as_of"] is None
    assert all(f["status"] == "unavailable" and f["value"] is None and f["series"] == []
               for f in result["factors"])
    json.dumps(result, allow_nan=False)
