"""Background fetch of K-line snapshots (keeps UI thread off the network)."""
from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from pa_agent.util.threading import CancelToken

logger = logging.getLogger(__name__)


class SnapshotFetchWorker(QThread):
    """One-shot ``latest_snapshot`` on a worker thread."""

    bars_ready = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(
        self,
        data_source: Any,
        n_bars: int,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._source = data_source
        self._n_bars = n_bars
        self._cancel_token = CancelToken()

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            fetch = getattr(self._source, "latest_snapshot_cancellable", None)
            bars = (fetch(self._n_bars, self._cancel_token) if callable(fetch)
                    else self._source.latest_snapshot(self._n_bars))
            if not self.isInterruptionRequested():
                self.bars_ready.emit(bars)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SnapshotFetchWorker failed: %s", exc)
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))

    def cancel(self) -> None:
        self.requestInterruption()
        self._cancel_token.set()
        abort = getattr(self._source, "cancel_pending", None)
        if callable(abort):
            abort()
