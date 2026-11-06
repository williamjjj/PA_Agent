"""Repeatable desktop benchmark; runs offline against either checkout.

Example: python scripts/benchmark_desktop.py --repo . --output .local/perf-after.json
Uses synthetic bars and explicitly simulated provider/model latency, never credentials.
"""
from __future__ import annotations

import argparse
import ctypes
import dataclasses
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def memory_mb():
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = wintypes.HANDLE
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    if not fn(process(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return round(counters.WorkingSetSize / 2**20, 2)


def summary(values):
    ordered = sorted(values)
    return {"median_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 3),
            "max_ms": round(max(ordered), 3)}


def run(args):
    repo, output = args.repo.resolve(), args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["PA_AGENT_DATA_DIR"] = str(output.with_suffix(".runtime"))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(repo))
    from PyQt6.QtCore import QT_VERSION_STR, QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.data.base import KlineBar, KlineFrame, IndicatorBundle
    from pa_agent.gui.chart_widget import ChartWidget

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    chart = ChartWidget()
    chart._timer.stop()
    chart.resize(1100, 650)
    chart.show()
    QTest.qWait(100)
    bars = tuple(KlineBar(seq=i+1, ts_open=1793900000000-i*300000,
                         open=500+math.sin(i/10), high=502+math.sin(i/10),
                         low=498+math.sin(i/10), close=501+math.sin(i/10),
                         volume=10000+i) for i in range(3000))
    frame = KlineFrame("PERF_SYNTHETIC", "5m", bars,
                       IndicatorBundle(tuple(b.close for b in bars), (2.,)*3000),
                       1793900300000)
    beats = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start()
    elapsed, memory, scene_counts = [], [], []
    for repeat in range(args.updates+1):
        changed = dataclasses.replace(frame, bars=(dataclasses.replace(
            bars[0], close=bars[0].close+repeat*.001),)+bars[1:])
        start = time.perf_counter()
        chart.set_frame_now(changed, fit_view=True)
        app.processEvents()
        elapsed.append((time.perf_counter()-start)*1000)
        QTest.qWait(20)
        if repeat in (0, args.updates//2, args.updates):
            gc.collect()
            memory.append({"update": repeat, "working_set_mb": memory_mb()})
            scene_counts.append(len(chart.scene().items()))
    timer.stop()
    chart.set_frame_now(frame, fit_view=True)
    app.processEvents()
    chart.grab().save(str(output.with_suffix(".png")))
    table = PromptAssembler._render_kline_table(frame)
    result = {"python": sys.version.split()[0], "qt": QT_VERSION_STR, "bars": 3000,
              "updates": args.updates, "chart_first_ms": round(elapsed[0], 3),
              "chart_updates": summary(elapsed[1:]), "chart_update_samples_ms": elapsed[1:],
              "chart_heartbeat": summary([(b-a)*1000 for a,b in zip(beats, beats[1:])]),
              "memory": memory, "scene_item_counts": scene_counts,
              "candlestick_items": 1 if hasattr(chart, "_candle_series") else len(chart._candle_items),
              "labels": len(chart._seq_labels), "prompt_chars": len(table),
              "prompt_sha256": hashlib.sha256(table.encode()).hexdigest()}
    chart.close()
    chart.deleteLater()
    QTest.qWait(50)

    from pa_agent.gui.main_window import MainWindow
    from tests.e2e.test_smoke_happy_path import _make_ctx, _make_reply
    from tests.fixtures.ai_payloads import VALID_STAGE1, VALID_STAGE2_ORDER
    from tests.fixtures.kline_bars import make_newest_first_bars

    ctx, writer = _make_ctx(output.parent)
    ctx.settings.general.analysis_bar_count = 3000
    ctx.settings.general.incremental_max_new_bars = 0
    window = MainWindow(ctx)
    window.show()
    QTest.qWait(100)
    source_started = threading.Event()
    snapshots = make_newest_first_bars(3055, with_forming=False)

    def fetch(count):
        assert count == 3055
        source_started.set()
        time.sleep(.3)
        return snapshots

    ctx.data_source = SimpleNamespace(_connected=True,
        _symbol=window._symbol_combo.currentText(), _timeframe=window._tf_combo.currentText(),
        latest_snapshot=fetch)
    window._current_data_source_kind = lambda: "tradingview"

    def probe():
        time.sleep(.08)
        return True, None

    with patch("pa_agent.data.tradingview_connectivity.check_tradingview_connectivity", probe):
        beats.clear()
        timer.start()
        QTest.qWait(30)
        start = time.perf_counter()
        window._on_fetch_data_clicked()
        result["fetch_button_return_ms"] = round((time.perf_counter()-start)*1000, 3)
        while window._last_frame_ready_bars is None:
            QTest.qWait(10)
            if time.perf_counter()-start > 10:
                raise TimeoutError("synthetic fetch")
        QTest.qWait(80)
        result["fetch_to_chart_ms"] = round((time.perf_counter()-start)*1000, 3)
        result["fetch_heartbeat"] = summary([(b-a)*1000 for a,b in zip(beats, beats[1:])])
        timer.stop()
        window._stop_refresh_loop()
        QTest.qWait(100)
        source_started.clear()
        window._start_refresh_loop()
        while not source_started.is_set():
            QTest.qWait(5)
        start = time.perf_counter()
        window._stop_refresh_loop()
        result["cancel_fetch_return_ms"] = round((time.perf_counter()-start)*1000, 3)
        QTest.qWait(350)

    model_calls = []

    def reply(*a, **kw):
        model_calls.append(time.perf_counter())
        time.sleep(.15)
        return _make_reply(VALID_STAGE1 if len(model_calls)%2 else VALID_STAGE2_ORDER)

    ctx.client.stream_chat.side_effect = reply
    submits = []
    for repeat in range(3):
        window._last_frame_ready_bars = snapshots
        window._chart_widget.reset()
        beats.clear()
        timer.start()
        QTest.qWait(30)
        start = time.perf_counter()
        window._on_submit_analysis()
        returned = (time.perf_counter()-start)*1000
        while window._analysis_in_progress:
            QTest.qWait(10)
            if time.perf_counter()-start > 15:
                raise TimeoutError("synthetic analysis")
        QTest.qWait(50)
        timer.stop()
        assert len(window._last_analysis_frame.bars) == 3000
        assert writer.save_full.call_count == repeat+1
        submits.append({"button_return_ms": round(returned, 3),
                        "max_gui_gap_ms": round(max((b-a)*1000 for a,b in zip(beats, beats[1:])), 3),
                        "end_to_end_ms": round((time.perf_counter()-start)*1000, 3),
                        "model_input_bars": len(ctx.assembler.build_stage1.call_args.args[0].bars)})
    result["submit_3000"] = submits
    result["simulated_latency_ms"] = {"preflight": 80, "provider": 300, "each_model_stage": 150}
    window.close()
    QTest.qWait(100)
    result["python_threads_after"] = threading.active_count()
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "chart_update_samples_ms"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--updates", type=int, default=50)
    run(parser.parse_args())
