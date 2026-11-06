"""Regression tests for full-history rendering and nonblocking task actions."""
from dataclasses import replace
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import QTimer

from pa_agent.data.base import IndicatorBundle, KlineFrame
from pa_agent.gui.chart_widget import ChartWidget
from pa_agent.gui.main_window import MainWindow
from pa_agent.gui.worker_lifetime import _retired_workers
from tests.e2e.test_smoke_happy_path import _make_ctx
from tests.fixtures.kline_bars import make_newest_first_bars


def test_3000_candles_reuse_history_and_pan_labels(qtbot):
    chart = ChartWidget()
    qtbot.addWidget(chart)
    bars = tuple(make_newest_first_bars(3000, with_forming=False))
    frame = KlineFrame("TEST", "15m", bars,
                       IndicatorBundle((2000.,) * 3000, (20.,) * 3000), 1)
    chart.set_frame_now(frame, fit_view=True)
    pictures = [block[1] for block in chart._candle_series._blocks]
    assert len(pictures) == 24
    assert chart._latest_frame.bars == bars
    assert len(chart._seq_labels) <= 15
    changed = replace(frame, bars=(replace(bars[0], close=2006.),) + bars[1:])
    chart.set_frame_now(changed)
    assert all(a is b[1] for a, b in zip(pictures[:-1], chart._candle_series._blocks[:-1]))
    assert pictures[-1] is not chart._candle_series._blocks[-1][1]
    chart.getViewBox().setXRange(0, 20, padding=0)
    assert "#2999" in {label.toPlainText() for label in chart._seq_labels}
    chart.getViewBox().setXRange(0, 3000, padding=0)
    assert len(chart._seq_labels) <= 150
    assert len(chart._latest_frame.bars) == 3000
    chart.reset()
    assert not chart._candle_series.bars
    assert not chart._seq_labels


def test_fetch_and_submit_share_request_and_cancel_without_wait(qtbot, tmp_path, monkeypatch):
    ctx, _ = _make_ctx(tmp_path)
    ctx.settings.general.analysis_bar_count = 3000
    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.show()
    started, release, exited = threading.Event(), threading.Event(), threading.Event()

    def fetch(count):
        assert count == 3055
        started.set()
        release.wait(2)
        exited.set()
        return make_newest_first_bars(3055, with_forming=False)

    ctx.data_source = SimpleNamespace(
        _connected=True, _symbol=window._symbol_combo.currentText(),
        _timeframe=window._tf_combo.currentText(), latest_snapshot=Mock(side_effect=fetch))
    monkeypatch.setattr(window, "_current_data_source_kind", lambda: "tradingview")
    monkeypatch.setattr("pa_agent.data.tradingview_connectivity.check_tradingview_connectivity",
                        Mock(side_effect=AssertionError("redundant blocking preflight")))
    beats = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()
    before = time.monotonic()
    window._on_fetch_data_clicked()
    assert time.monotonic() - before < .2
    qtbot.waitUntil(started.is_set)
    window._on_submit_analysis()
    assert window._awaiting_analysis_bars is not None
    assert not window._submit_btn.isEnabled()
    qtbot.wait(100)
    assert len(beats) >= 5
    before = time.monotonic()
    window._on_cancel_task()
    assert time.monotonic() - before < .2
    assert not exited.is_set()
    release.set()
    qtbot.waitUntil(lambda: exited.is_set() and not _retired_workers)
    assert ctx.data_source.latest_snapshot.call_count == 1
    assert window._last_frame_ready_bars is None
    assert not window._analysis_in_progress


def test_submit_sends_all_3000_closed_bars_and_keeps_gui_alive(qtbot, tmp_path):
    ctx, writer = _make_ctx(tmp_path)
    ctx.settings.general.analysis_bar_count = 3000
    replies = iter(ctx.client.stream_chat.side_effect)

    def delayed_reply(*args, **kwargs):
        time.sleep(.15)
        return next(replies)

    ctx.client.stream_chat.side_effect = delayed_reply
    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.show()
    window._last_frame_ready_bars = make_newest_first_bars(3055, with_forming=False)
    beats = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()
    started = time.monotonic()
    window._on_submit_analysis()
    assert time.monotonic() - started < .2
    qtbot.waitUntil(lambda: writer.save_full.call_count == 1 and not window._analysis_in_progress,
                    timeout=10000)
    assert len(window._last_analysis_frame.bars) == 3000
    assert len(beats) >= 10
    assert max(b - a for a, b in zip(beats, beats[1:])) < .3
    assert len(ctx.assembler.build_stage1.call_args.args[0].bars) == 3000
