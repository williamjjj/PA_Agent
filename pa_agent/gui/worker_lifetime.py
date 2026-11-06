"""Keep cancelled Qt workers alive until their actual run() has returned."""
from PyQt6.QtCore import QThread

_retired_workers: set[QThread] = set()


def has_running_workers() -> bool:
    return any(worker.isRunning() for worker in _retired_workers)


def retire_worker(worker: QThread) -> None:
    if worker in _retired_workers:
        return
    _retired_workers.add(worker)

    def release():
        if worker not in _retired_workers:
            return
        _retired_workers.discard(worker)
        worker.deleteLater()

    # AnalysisWorker has its own result signal also named finished.
    QThread.finished.__get__(worker).connect(release)
    if not worker.isRunning():
        release()
