"""Small token quotes must survive tick, structure and chart helpers."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pa_agent.ai.structure_levels import _parse_level_bounds, refresh_stage1_support_resistance
from pa_agent.util.price_precision import price_text, round_price
from pa_agent.util.price_tick import (
    format_breakout_tick_hint,
    infer_price_tick_from_frame,
    normalize_breakout_entry_price,
    round_to_tick,
)
from pa_agent.web.presentation.support_resistance import (
    extract_structure_levels,
    format_level,
    levels_from_stage1_diagnosis,
    nearest_support_resistance_labels,
)


def _token_frame() -> SimpleNamespace:
    quotes = [
        (2.67011e-8, 2.69022e-8, 2.66033e-8, 2.67613e-8),
        (2.66012e-8, 2.70024e-8, 2.64034e-8, 2.67011e-8),
        (2.66012e-8, 2.68024e-8, 2.65034e-8, 2.66012e-8),
    ]
    bars = tuple(
        SimpleNamespace(seq=i + 1, open=o, high=h, low=lo, close=c)
        for i, (o, h, lo, c) in enumerate(quotes)
    )
    return SimpleNamespace(bars=bars)


@pytest.mark.parametrize("value", [2.67613e-6, 2.67613e-8, 2.67613e-12, -2.67613e-12])
def test_small_price_rounding_preserves_nonzero_value_and_decimal_text(value: float) -> None:
    assert round_price(value) == value
    text = price_text(value)
    assert "e" not in text.lower()
    assert float(text) == value


def test_ordinary_prices_keep_existing_rounding_and_integer_zeros() -> None:
    for value in (1.23456789, 4556.595123456, 64360.0):
        assert round_price(value, 6) == round(value, 6)
    assert price_text(100.0, 0) == "100"
    assert price_text(-0.0) == "0"


def test_tick_inference_and_breakout_entry_retain_small_quote_precision() -> None:
    frame = _token_frame()
    assert infer_price_tick_from_frame(frame) == 1e-13
    for direction, extreme, sign in (("做多", "high", 1), ("做空", "low", -1)):
        basis = getattr(frame.bars[0], extreme)
        decision = {
            "order_type": "突破单",
            "order_direction": direction,
            "entry_basis_extreme": extreme,
            "entry_basis_bar": "K1",
            "entry_price": basis,
        }
        assert normalize_breakout_entry_price(decision, kline_frame=frame)
        assert (decision["entry_price"] - basis) * sign > 0
        assert decision["entry_price"] == round_to_tick(basis + sign * 1e-13, 1e-13)
    hint = format_breakout_tick_hint(frame)
    assert "0.0000000000001" in hint
    assert "1e-13" not in hint


def test_round_to_tick_does_not_round_small_increments_to_zero() -> None:
    assert round_to_tick(2.67613e-12, 1e-17) == 2.67613e-12
    assert round_to_tick(2.676134e-12, 1e-17) == 2.67613e-12


def test_refresh_keeps_distinct_small_supports_and_correct_sides() -> None:
    stage1 = {
        "support_levels": ["0.00000002661", "0.00000002662"],
        "resistance_levels": ["0.00000002681", "0.00000002682"],
    }
    assert refresh_stage1_support_resistance(stage1, _token_frame())
    assert len(stage1["support_levels"]) == 3
    assert len(stage1["resistance_levels"]) == 3
    close = _token_frame().bars[0].close
    assert all(0 < float(value) < close for value in stage1["support_levels"])
    assert all(float(value) > close for value in stage1["resistance_levels"])
    assert all("e" not in value.lower() for value in stage1["support_levels"])


def test_scientific_notation_is_a_price_not_an_exponent_range() -> None:
    assert _parse_level_bounds("2.67613e-08") == (2.67613e-8, 2.67613e-8)
    levels = levels_from_stage1_diagnosis({"support_levels": ["2.67613e-08"]})
    assert len(levels) == 1
    assert levels[0].price == 2.67613e-8
    assert not levels[0].is_zone
    assert format_level(levels[0]) == "0.0000000267613"


def test_small_scientific_ranges_preserve_bounds_and_zone_width() -> None:
    stage1 = {
        "support_levels": ["2.65e-8 - 2.67e-8"],
        "resistance_levels": ["2.68e-8 - 2.69e-8"],
    }
    assert _parse_level_bounds(stage1["support_levels"][0]) == (2.65e-8, 2.67e-8)
    levels = levels_from_stage1_diagnosis(stage1)
    assert len(levels) == 2
    assert all(level.is_zone for level in levels)
    assert nearest_support_resistance_labels(stage1) == (
        "0.0000000265-0.0000000267",
        "0.0000000268-0.0000000269",
    )


def test_chart_deduplication_preserves_distinct_small_levels() -> None:
    levels = extract_structure_levels({"support_levels": ["2.61e-8", "2.62e-8", "2.63e-8"]})
    assert [level.price for level in levels] == [2.61e-8, 2.62e-8, 2.63e-8]
