"""Obtain a separate frozen research snapshot from the configured PA data source."""
import threading

from PyQt6.QtCore import QThread, pyqtSignal


def chart_from_frame(frame, source, exchange="", market_kind="continuous_spot"):
    from pa_agent.data.snapshot import take_snapshot_from_bars
    from pa_agent.quant.data import chart_data
    from pa_agent.quant.snapshots import dataset_hash

    count = sum(bar.closed for bar in frame.bars)
    if not count:
        raise ValueError("当前行情还没有已收盘 K 线。")
    closed = take_snapshot_from_bars([bar for bar in frame.bars if bar.closed], count, frame.symbol, frame.timeframe,
                                     now_ms=frame.snapshot_ts_local_ms)
    chart = chart_data(closed)
    chart.update(source=source, market_kind=market_kind, captured_at=frame.snapshot_ts_local_ms,
                 request={"exchange": exchange}, adjustment="provider")
    chart["dataset_hash"] = dataset_hash(chart)
    return chart


class QuantSourceWorker(QThread):
    result_ready = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, source_kind, settings, symbol, timeframe, count, exchange, market_kind, parent=None):
        super().__init__(parent)
        self.kind, self.settings = source_kind, settings
        self.symbol, self.timeframe, self.count = symbol, timeframe, count
        self.exchange, self.market_kind = exchange, market_kind
        self.cancel = threading.Event()
        self.source = None

    def stop(self):
        self.cancel.set()
        cancel_pending = getattr(self.source, "cancel_pending", None)
        if callable(cancel_pending):
            cancel_pending()

    def run(self):
        from pa_agent.data.factory import create_data_source
        from pa_agent.data.snapshot import take_snapshot_from_bars, INDICATOR_WARMUP_BARS
        try:
            source = self.source = create_data_source(self.kind, self.settings)
            source.connect()
            if callable(getattr(source, "set_exchange", None)):
                source.set_exchange(self.exchange)
            source.subscribe(self.symbol, self.timeframe)
            count = self.count + INDICATOR_WARMUP_BARS + 5
            fetch = getattr(source, "latest_snapshot_cancellable", None)
            bars = fetch(count, self.cancel) if callable(fetch) else source.latest_snapshot(count)
            if self.cancel.is_set():
                return
            frame = take_snapshot_from_bars(bars, self.count, self.symbol, self.timeframe)
            self.result_ready.emit(chart_from_frame(frame, self.kind, self.exchange, self.market_kind))
        except Exception as exc:
            if not self.cancel.is_set():
                self.failed.emit(str(exc))
        finally:
            # MT5's Python module shares one terminal connection with the main
            # window; closing a temporary reader would close that connection.
            if self.source is not None and self.kind != "mt5":
                self.source.disconnect()
