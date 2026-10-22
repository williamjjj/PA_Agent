"""Standalone CPU worker for durable, fenced research formula mining jobs.

Start explicitly with ``python -m pa_agent.web.mining_worker``. Importing this
module or the ASGI app never starts a worker or a background computation.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
import uuid
from contextlib import suppress

from pa_agent.web.mining import MiningConfig, MiningSession
from pa_agent.web.mining_store import MiningStore
from pa_agent.web.mining_versions import contract_versions
from pa_agent.web.research_store import ResearchStore
from pa_agent.web.store import get_store

LOG = logging.getLogger(__name__)
POLL_SECONDS = 2.0
HEARTBEAT_SECONDS = 5.0
LEASE_SECONDS = 30.0
_ERRORS = {
    "runtime_changed": "任务创建时的引擎版本与当前工作进程不同，请重新创建任务。",
    "dataset_unavailable": "任务引用的研究数据不可用，请重新创建数据集。",
    "invalid_input": "任务配置、数据或恢复检查点无效，请检查后重新创建任务。",
    "no_valid_formula": "本次搜索未得到可报告的有效公式，请检查配置或增加搜索次数。",
    "calculation_failed": "公式挖掘未能完成，请稍后重试；未保存不完整的结果。",
    "timed_out": "任务已达到累计计算时间上限，仅保留训练段搜索进度。",
    "cancelled": "任务已取消，未发布公式或测试结果。",
}


class _LeaseGuard:
    """Renew independently of candidate evaluation and remember lost ownership."""

    def __init__(self, worker, job):
        self.worker, self.job = worker, job
        self.started = time.monotonic()
        self.previous = max(0.0, float(job.get("consumed_seconds", 0)))
        self.maximum = float(job["config"].get("max_seconds", 60))
        self.reason = None
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="pa-mining-heartbeat", daemon=True)

    def elapsed(self):
        return self.previous + max(0.0, time.monotonic() - self.started)

    def refresh(self):
        with self._lock:
            if self.reason:
                return False
            try:
                current = self.worker.store.heartbeat(
                    self.job["id"], self.job["lease_token"],
                    lease_seconds=self.worker.lease_seconds, consumed_seconds=self.elapsed())
                self.worker.store.heartbeat_worker(self.worker.worker_id)
            except Exception:  # noqa: BLE001 -- uncertain leases must stop publication
                self.reason = "lease_lost"
                LOG.warning("Mining lease heartbeat failed; computation will not publish results.")
                return False
            if current is None:
                self.reason = "lease_lost"
            elif current.get("status") == "timed_out" or self.elapsed() >= self.maximum:
                self.reason = "timed_out"
            elif current.get("cancel_requested") or current.get("status") in {"cancel_requested", "cancelled"}:
                self.reason = "cancelled"
            elif current.get("status") != "running":
                self.reason = "lease_lost"
            return self.reason is None

    def start(self):
        self._thread.start()

    def _loop(self):
        while not self._done.wait(self.worker.heartbeat_seconds):
            if not self.refresh():
                return

    def close(self):
        self._done.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)


class MiningWorker:
    """Process one CPU job at a time; never acquire PA analysis account leases."""

    def __init__(self, store, datasets, *, worker_id=None, session_factory=MiningSession,
                 poll_seconds=POLL_SECONDS, heartbeat_seconds=HEARTBEAT_SECONDS,
                 lease_seconds=LEASE_SECONDS, versions=None):
        self.store, self.datasets = store, datasets
        self.worker_id = worker_id or "worker-" + uuid.uuid4().hex
        self.session_factory = session_factory
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_seconds = lease_seconds
        self.versions = dict(versions or contract_versions())
        self.stopping = threading.Event()

    def stop(self):
        """Signal handlers only set a flag; SQL work stays outside the handler."""
        self.stopping.set()

    def _fail(self, job, guard, code, *, status="failed"):
        if guard.reason == "lease_lost":
            return
        try:
            self.store.fail(job["id"], job["lease_token"],
                            f"{code}: {_ERRORS[code]}", status=status,
                            consumed_seconds=guard.elapsed())
        except Exception:  # noqa: BLE001 -- never reflect database exception contents
            # Do not overwrite an uncertain lease or leak provider/user values.
            LOG.warning("Mining failure could not be recorded; lease recovery remains authoritative.")

    def _interrupted(self, job, guard):
        if guard.reason == "lease_lost":
            return True
        if guard.reason in {"cancelled", "timed_out"}:
            self._fail(job, guard, guard.reason, status=guard.reason)
            return True
        if self.stopping.is_set():
            try:
                self.store.release(job["id"], job["lease_token"], consumed_seconds=guard.elapsed())
            except Exception:  # noqa: BLE001 -- preserve lease-expiry recovery on shutdown
                LOG.warning("Mining shutdown could not release its lease; expiry will recover it.")
            return True
        return False

    def process_once(self):
        """Claim at most one job, returning whether a job was claimed."""
        if self.stopping.is_set():
            return False
        job = self.store.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        guard = _LeaseGuard(self, job)
        guard.start()
        phase = "input"
        try:
            guard.refresh()
            if self._interrupted(job, guard):
                return True
            provenance = job.get("provenance", {})
            if {key: provenance.get(key) for key in self.versions} != self.versions:
                self._fail(job, guard, "runtime_changed")
                return True
            dataset = self.datasets.get_dataset(job["user_id"], job["dataset_id"])
            if dataset is None or dataset["digest"] != provenance.get("dataset_hash"):
                self._fail(job, guard, "dataset_unavailable")
                return True
            config = MiningConfig.model_validate(job["config"])
            session = self.session_factory(dataset["chart"], config, checkpoint=job.get("checkpoint") or None)
            guard.refresh()
            if self._interrupted(job, guard):
                return True
            phase = "search"
            while not session.done:
                guard.refresh()
                if self._interrupted(job, guard):
                    return True
                checkpoint = session.step()
                # A completed candidate may be checkpointed during graceful
                # shutdown, but never after losing ownership/cancellation.
                if guard.reason:
                    self._interrupted(job, guard)
                    return True
                progress = min(1.0, max(0.0, checkpoint["next_index"] / config.trials))
                # This fenced write atomically rechecks cancellation, lease
                # ownership and elapsed budget before accepting the checkpoint.
                if not self.store.save_checkpoint(
                        job["id"], job["lease_token"], checkpoint, progress,
                        consumed_seconds=guard.elapsed()):
                    return True
                guard.refresh()
                if self._interrupted(job, guard):
                    return True
            guard.refresh()
            if self._interrupted(job, guard):
                return True
            phase = "finish"
            result = session.finish()
            guard.refresh()
            if self._interrupted(job, guard):
                return True
            result = {**result, "versions": self.versions,
                      "dataset": {"id": dataset["id"], "dataset_hash": dataset["digest"],
                                  **dataset["metadata"]},
                      "execution": {"consumed_seconds": guard.elapsed(),
                                    "max_seconds": config.max_seconds,
                                    "stopping_reason": "search_complete"}}
            self.store.complete(job["id"], job["lease_token"], result,
                                consumed_seconds=guard.elapsed())
        except ValueError:
            code = "invalid_input" if phase == "input" else (
                "no_valid_formula" if phase == "finish" else "calculation_failed")
            guard.refresh()
            if not self._interrupted(job, guard):
                self._fail(job, guard, code)
        except Exception:  # noqa: BLE001 -- worker boundary persists only fixed safe errors
            guard.refresh()
            if not self._interrupted(job, guard):
                self._fail(job, guard, "calculation_failed")
        finally:
            guard.close()
        return True

    def run(self, *, once=False):
        """Register readiness only for this explicitly started worker process."""
        self.store.register_worker(self.worker_id, self.versions)
        try:
            while not self.stopping.is_set():
                self.store.heartbeat_worker(self.worker_id)
                claimed = self.process_once()
                if once:
                    return claimed
                if not claimed:
                    self.stopping.wait(self.poll_seconds)
        finally:
            with suppress(Exception):
                self.store.stop_worker(self.worker_id)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the standalone PA research formula worker.")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit.")
    args = parser.parse_args(argv)
    accounts = None
    handlers = {}
    try:
        accounts = get_store()
        worker = MiningWorker(MiningStore(accounts.engine), ResearchStore(accounts.engine))
        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(signum, lambda *_: worker.stop())
        worker.run(once=args.once)
        return 0
    except Exception:  # noqa: BLE001 -- CLI must not print credentials from connection failures
        print("研究工作进程启动或运行失败，请检查数据库和环境配置。", file=sys.stderr)
        return 1
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        if accounts is not None:
            accounts.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
