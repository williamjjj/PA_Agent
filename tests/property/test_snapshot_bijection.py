"""Property-based tests for analysis snapshot from bar lists (PR1)."""
from __future__ import annotations

import math

from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from pa_agent.data.base import KlineBar
from pa_agent.data.snapshot import build_analysis_frame, build_live_frame


def _make_bar(seq: int, ts: float, *, closed: bool) -> KlineBar:
    return KlineBar(
        seq=seq,
        ts_open=ts,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100.0,
        closed=closed,
    )


def _bars_with_forming(n_closed: int, extra: int) -> list[KlineBar]:
    """Newest-first: forming at 0, then n_closed+extra closed bars."""
    base = 1_700_000_000_000.0
    step = 3_600_000
    bars = [_make_bar(0, base + (n_closed + extra) * step, closed=False)]
    for i in range(n_closed + extra):
        bars.append(_make_bar(i + 1, base + (n_closed + extra - i - 1) * step, closed=True))
    return bars


@given(
    n=st.integers(min_value=2, max_value=50),
    extra=st.integers(min_value=0, max_value=20),
)
@h_settings(max_examples=200)
def test_analysis_frame_seq_bijection(n: int, extra: int) -> None:
    """build_analysis_frame returns exactly n closed bars with seq 1..n."""
    raw = _bars_with_forming(n, extra)
    frame = build_analysis_frame(raw, n, symbol="TEST", timeframe="1h", now_ms=int(raw[0].ts_open + 1_800_000))
    assert frame is not None
    assert len(frame.bars) == n
    seqs = {b.seq for b in frame.bars}
    assert seqs == set(range(1, n + 1))


@given(
    n=st.integers(min_value=2, max_value=50),
    extra=st.integers(min_value=0, max_value=20),
)
@h_settings(max_examples=200)
def test_live_frame_forming_bar_is_k0(n: int, extra: int) -> None:
    """Live K0 is separate from the closed K1..Kn used by analysis."""
    raw = _bars_with_forming(n, extra)
    frame = build_live_frame(raw, n, symbol="TEST", timeframe="1h", now_ms=int(raw[0].ts_open + 1_800_000))
    assert frame is not None
    assert frame.bars[0].seq == 0
    assert frame.bars[0].closed is False
    assert [bar.seq for bar in frame.bars[1:]] == list(range(1, n + 1))
    assert all(bar.closed for bar in frame.bars[1:])


@given(
    n=st.integers(min_value=2, max_value=50),
    extra=st.integers(min_value=0, max_value=20),
)
@h_settings(max_examples=200)
def test_analysis_frame_ts_strictly_decreasing(n: int, extra: int) -> None:
    """Closed bars are in strictly decreasing ts_open order (newest first)."""
    raw = _bars_with_forming(n, extra)
    frame = build_analysis_frame(raw, n, symbol="TEST", timeframe="1h", now_ms=int(raw[0].ts_open + 1_800_000))
    assert frame is not None
    for i in range(len(frame.bars) - 1):
        assert frame.bars[i].ts_open > frame.bars[i + 1].ts_open
