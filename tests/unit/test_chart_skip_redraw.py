"""Chart skip-redraw when frozen closed frame matches snapshot."""
from __future__ import annotations

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.data.snapshot import frame_is_pure_closed, frames_equal_for_chart
from pa_agent.gui.chart_widget import ChartWidget


def _bar(seq: int, ts: float, *, close: float = 10.0, closed: bool = True) -> KlineBar:
    return KlineBar(
        seq=seq,
        ts_open=ts,
        open=1.0,
        high=2.0,
        low=0.5,
        close=close,
        volume=100.0,
        closed=closed,
    )


def _frame(*, forming: bool = False, close: float = 10.0) -> KlineFrame:
    bars = (
        _bar(1, 300.0, close=close, closed=not forming),
        _bar(2, 200.0, close=9.0),
    )
    n = len(bars)
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(1.5 for _ in range(n)),
            atr14=tuple(0.5 for _ in range(n)),
        ),
        snapshot_ts_local_ms=1,
    )


def test_frame_is_pure_closed() -> None:
    assert frame_is_pure_closed(_frame())
    assert not frame_is_pure_closed(_frame(forming=True))


def test_frames_equal_ignores_snapshot_ts() -> None:
    a = _frame()
    b = KlineFrame(
        symbol=a.symbol,
        timeframe=a.timeframe,
        bars=a.bars,
        indicators=a.indicators,
        snapshot_ts_local_ms=999,
    )
    assert frames_equal_for_chart(a, b)


def test_set_frame_now_skips_identical_closed_frame(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    f1 = _frame()
    widget.set_frame_now(f1)
    assert widget._candle_series.bars == f1.bars
    picture = widget._candle_series._blocks[0][1]
    f2 = KlineFrame(
        symbol=f1.symbol,
        timeframe=f1.timeframe,
        bars=f1.bars,
        indicators=f1.indicators,
        snapshot_ts_local_ms=2,
    )
    widget.set_frame_now(f2)
    assert widget._candle_series._blocks[0][1] is picture


def test_set_frame_now_redraws_when_forming_removed(qtbot) -> None:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.set_frame_now(_frame(forming=True))
    picture = widget._candle_series._blocks[0][1]
    widget.set_frame_now(_frame())
    assert widget._candle_series.bars == _frame().bars
    assert widget._candle_series._blocks[0][1] is not picture
