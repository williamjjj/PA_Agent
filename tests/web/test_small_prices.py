"""PA consumes the same small-token prices shown by the frozen factor snapshot."""
import math

import pytest

from pa_agent.ai.market_features import compute_simple_market_features
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.data.base import KlineBar, KlineFrame
from pa_agent.data.snapshot import compute_indicators
from pa_agent.util.price_tick import infer_price_tick_from_frame
from pa_agent.web.analysis import AccountPrompts
from pa_agent.web.settings import WebSettings


def small_frame(price):
    oldest = []
    for i in range(60):
        close = price * (1 + 0.01 * math.sin(i))
        if i == 59:
            close = price
        oldest.append(KlineBar(
            seq=60-i, ts_open=1_727_683_200_000 + i * 60_000,
            open=close * 0.999, high=close * 1.002, low=close * 0.998,
            close=close, volume=0.123456789,
        ))
    bars = tuple(reversed(oldest))
    return KlineFrame("SMALL", "1m", bars, compute_indicators(list(bars)),
                      1_727_683_200_000 + 60 * 60_000)


@pytest.mark.parametrize("price", [0.00000267613, 1e-9, 1000.0])
def test_web_kline_table_roundtrips_frozen_prices_and_fractional_volume(price):
    frame = small_frame(price)
    rows = AccountPrompts._render_kline_table(frame).splitlines()[2:-1]
    assert len(rows) == len(frame.bars)
    for i, (row, bar) in enumerate(zip(rows, frame.bars)):
        cells = [cell.strip() for cell in row.split("|")]
        assert [float(cell) for cell in cells[2:6]] == [bar.open, bar.high, bar.low, bar.close]
        assert float(cells[7]) == bar.volume
        for cell, value in zip(cells[8:10], (frame.indicators.ema20[i], frame.indicators.atr14[i])):
            if math.isnan(value):
                assert cell == "N/A"
            else:
                assert float(cell) == value


@pytest.mark.parametrize("price", [0.00000267613, 1e-9])
def test_both_pa_stages_keep_small_candles_and_nonzero_tick(price):
    frame = small_frame(price)
    prompts = AccountPrompts(WebSettings())
    table = prompts._render_kline_table(frame)
    first = prompts.build_stage1(frame)[-1]["content"]
    second = prompts.build_stage2(frame, {}, [], [])[-1]["content"]
    assert table in first
    assert table in second
    tick = infer_price_tick_from_frame(frame)
    assert 0 < tick < frame.bars[0].high - frame.bars[0].low
    assert "最小跳动" in second


@pytest.mark.parametrize("price", [0.00000267613, 1e-9])
def test_small_swing_and_measured_move_values_keep_their_scale(price):
    frame = small_frame(price)
    actual = compute_simple_market_features(frame)
    assert actual.swings
    for pivot in actual.swings:
        bar = frame.bars[pivot.seq-1]
        expected = bar.high if pivot.kind == "high" else bar.low
        assert pivot.price == pytest.approx(expected, rel=1e-11, abs=0)
    assert len({pivot.price for pivot in actual.swings}) > 1
    up = next(move for move in actual.measured_moves if move.kind == "range_up")
    expected_height = actual.range_high - actual.range_low
    assert up.height == pytest.approx(expected_height, rel=1e-11, abs=0)
    assert up.target_price == pytest.approx(actual.range_high + expected_height, rel=1e-11, abs=0)
    assert up.height > 0
    assert up.target_price > actual.range_high


def test_level_hint_recognizes_exponent_and_keeps_small_nonzero_price():
    frame = small_frame(1e-9)
    stage1 = {"cycle_position": "trading_range", "direction": "bullish",
              "support_levels": ["9.999e-10"], "resistance_levels": []}
    assert PromptAssembler._parse_level_midpoint("9.999e-10") == 9.999e-10
    hint = PromptAssembler._render_planned_limit_hint(stage1, frame)
    assert "约 0.0000000009999" in hint
    assert "约 0.0000）" not in hint


def test_web_table_limit_keeps_latest_indicator_alignment():
    frame = small_frame(1e-9)
    table = AccountPrompts._render_kline_table(frame, limit=2)
    full = AccountPrompts._render_kline_table(frame)
    assert table.splitlines()[2:-1] == full.splitlines()[2:4]
