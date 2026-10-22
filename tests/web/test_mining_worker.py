"""The standalone worker resumes durable jobs without publishing stale results."""
import json
import math
import threading
import time

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import insert, update

from pa_agent.web import mining_worker
from pa_agent.web.mining import MiningConfig, MiningSession
from pa_agent.web.mining_store import MiningStore, jobs
from pa_agent.web.mining_versions import contract_versions
from pa_agent.web.mining_worker import MiningWorker
from pa_agent.web.research_data import DatasetImport, parse_dataset_csv
from pa_agent.web.research_store import ResearchStore
from pa_agent.web.store import Store, users


@pytest.fixture
def queue(tmp_path):
    accounts = Store("sqlite:///" + str(tmp_path / "worker.sqlite3"), Fernet.generate_key().decode())
    with accounts.engine.begin() as conn:
        conn.execute(insert(users).values(id="alice", username="alice", password="unused-test-hash",
                     settings=accounts.seal({}), created=time.time(), busy_until=0, lease=""))
    csv = ["time,open,high,low,close,volume"]
    previous = 100
    for index in range(600):
        close = 100 + 12 * math.sin(index / 9) + 3 * math.sin(index / 2)
        csv.append(f"{1727683200 + index * 60},{previous},{max(previous, close)+1},"
                   f"{min(previous, close)-1},{close},{100+index%19}")
        previous = close
    datasets = ResearchStore(accounts.engine)
    dataset = datasets.put_dataset("alice", parse_dataset_csv(DatasetImport(
        csv="\n".join(csv), symbol="SIMULATED", timeframe="1m")))
    store = MiningStore(accounts.engine)
    yield accounts, store, datasets, dataset
    accounts.engine.dispose()


def enqueue(queue, name="test", **changes):
    _, store, _, dataset = queue
    config = MiningConfig(trials=8, **changes).model_dump()
    return store.create_job("alice", dataset["id"], config, name, provenance=contract_versions())


class FakeSession:
    def __init__(self, chart, config, checkpoint=None):
        self.config = config
        self.index = checkpoint["next_index"] if checkpoint else 0

    @property
    def done(self):
        return self.index >= self.config.trials

    def step(self):
        self.index += 1
        return {"next_index": self.index, "best": {"train_metrics": {"total_return": .1}}}

    def finish(self):
        return {"formula": {"id": "fake-formula"}, "research_result": {},
                "selection": {"completed": self.done}}


def worker(queue, **kwargs):
    return MiningWorker(queue[1], queue[2], **kwargs)


def test_once_processes_only_one_job_and_stops_readiness(queue):
    first, second = enqueue(queue, "first"), enqueue(queue, "second")
    current = worker(queue, session_factory=FakeSession)
    assert current.run(once=True)
    completed = queue[1].get_job("alice", first["id"])
    assert completed["status"] == "succeeded"
    assert completed["result"]["versions"] == contract_versions()
    assert completed["result"]["dataset"]["dataset_hash"] == queue[3]["digest"]
    assert completed["checkpoint"]["next_index"] == 8
    assert queue[1].get_job("alice", second["id"])["status"] == "queued"
    assert not queue[1].worker_status()["available"]
    with queue[0].engine.connect() as conn:
        row = conn.execute(users.select().where(users.c.id == "alice")).mappings().one()
    assert row["busy_until"] == 0 and row["lease"] == ""


def test_real_engine_resumes_deterministically_after_graceful_shutdown(queue):
    job = enqueue(queue, "resume")
    current = None
    class PauseSession(MiningSession):
        def step(self):
            checkpoint = super().step()
            if checkpoint["next_index"] == 2:
                current.stop()
            return checkpoint
    current = worker(queue, session_factory=PauseSession)
    current.run(once=True)
    paused = queue[1].get_job("alice", job["id"])
    assert paused["status"] == "queued"
    assert paused["checkpoint"]["next_index"] == 2 and paused["progress"] == .25
    assert paused["consumed_seconds"] > 0 and paused["result"] is None
    assert "validation" not in json.dumps(paused["checkpoint"]["ranked"])
    worker(queue).run(once=True)
    resumed = queue[1].get_job("alice", job["id"])
    assert resumed["status"] == "succeeded", resumed["error"]
    baseline = enqueue(queue, "baseline")
    worker(queue).run(once=True)
    original = queue[1].get_job("alice", baseline["id"])
    assert original["status"] == "succeeded", original["error"]
    for key in ("formula", "selection", "research_result"):
        assert resumed["result"][key] == original["result"][key]


@pytest.mark.parametrize("phase", ["step", "finish"])
def test_cancellation_during_computation_never_publishes_result(queue, phase):
    job = enqueue(queue)
    called = []
    class CancellingSession(FakeSession):
        def step(self):
            checkpoint = super().step()
            if phase == "step":
                queue[1].cancel_job("alice", job["id"])
            return checkpoint

        def finish(self):
            called.append("finish")
            if phase == "finish":
                queue[1].cancel_job("alice", job["id"])
            return super().finish()
    worker(queue, session_factory=CancellingSession).run(once=True)
    saved = queue[1].get_job("alice", job["id"])
    assert saved["status"] == "cancelled" and saved["result"] is None
    assert called == (["finish"] if phase == "finish" else [])


def test_cancellation_committed_immediately_before_atomic_complete_wins(queue, monkeypatch):
    job = enqueue(queue)
    original_complete = queue[1].complete
    def cancel_then_complete(*args, **kwargs):
        queue[1].cancel_job("alice", job["id"])
        return original_complete(*args, **kwargs)
    monkeypatch.setattr(queue[1], "complete", cancel_then_complete)
    worker(queue, session_factory=FakeSession).run(once=True)
    saved = queue[1].get_job("alice", job["id"])
    assert saved["status"] == "cancelled" and saved["result"] is None


@pytest.mark.parametrize("phase", ["prepare", "step", "finish"])
def test_budget_covers_preparation_candidates_and_final_evaluation(queue, monkeypatch, phase):
    job = enqueue(queue, max_seconds=10)
    clock = [0.0]
    monkeypatch.setattr(mining_worker.time, "monotonic", lambda: clock[0])
    class SlowSession(FakeSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if phase == "prepare":
                clock[0] += 11

        def step(self):
            result = super().step()
            if phase == "step":
                clock[0] += 11
            return result

        def finish(self):
            if phase == "finish":
                clock[0] += 11
            return super().finish()
    worker(queue, session_factory=SlowSession).run(once=True)
    saved = queue[1].get_job("alice", job["id"])
    assert saved["status"] == "timed_out" and saved["result"] is None
    assert saved["consumed_seconds"] >= 10


def test_elapsed_budget_is_shared_across_graceful_restarts(queue, monkeypatch):
    job = enqueue(queue, max_seconds=60)
    clock = [0.0]
    monkeypatch.setattr(mining_worker.time, "monotonic", lambda: clock[0])
    current = None
    class FirstSession(FakeSession):
        def step(self):
            checkpoint = super().step()
            clock[0] += 25
            current.stop()
            return checkpoint
    current = worker(queue, session_factory=FirstSession)
    current.run(once=True)
    first = queue[1].get_job("alice", job["id"])
    assert first["status"] == "queued" and 25 <= first["consumed_seconds"] < 26
    clock[0] = 0
    class SecondSession(FakeSession):
        def step(self):
            checkpoint = super().step()
            clock[0] += 40
            return checkpoint
    worker(queue, session_factory=SecondSession).run(once=True)
    second = queue[1].get_job("alice", job["id"])
    assert second["status"] == "timed_out"
    assert second["consumed_seconds"] >= 60 and second["result"] is None
    assert second["checkpoint"]["next_index"] == 2


def test_background_heartbeat_protects_lease_during_slow_candidate(queue):
    job = enqueue(queue)
    verified = []
    class SlowSession(FakeSession):
        def step(self):
            if self.index == 0:
                threading.Event().wait(.2)
                verified.append(queue[1].worker_status()["available"])
                assert queue[1].claim("competitor", lease_seconds=.08) is None
            return super().step()
    worker(queue, session_factory=SlowSession, heartbeat_seconds=.01, lease_seconds=.08).run(once=True)
    assert verified == [True]
    assert queue[1].get_job("alice", job["id"])["status"] == "succeeded"


def test_lease_loss_discards_result_without_overwriting_successor(queue):
    job = enqueue(queue)
    successor = []
    class LostSession(FakeSession):
        def step(self):
            checkpoint = super().step()
            with queue[0].engine.begin() as conn:
                conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(lease_until=0))
            successor.append(queue[1].claim("successor"))
            return checkpoint
    worker(queue, session_factory=LostSession).run(once=True)
    saved = queue[1].get_job("alice", job["id"])
    assert successor[0]["lease_token"] == saved["lease_token"]
    assert saved["worker_id"] == "successor" and saved["status"] == "running"
    assert saved["result"] is None and saved["checkpoint"] == {}


def test_heartbeat_failure_discards_computation_and_does_not_leak_exception(queue, monkeypatch, caplog):
    job = enqueue(queue)
    def failure(*args, **kwargs):
        raise RuntimeError("postgres://private-password@example.invalid")
    monkeypatch.setattr(queue[1], "heartbeat", failure)
    worker(queue, session_factory=FakeSession).run(once=True)
    saved = queue[1].get_job("alice", job["id"])
    assert saved["status"] == "running" and saved["result"] is None
    assert "private-password" not in caplog.text


def test_version_mismatch_fails_before_any_candidate(queue):
    job = enqueue(queue)
    versions = {**contract_versions(), "code_revision": "different-runtime"}
    def factory(*args, **kwargs):
        pytest.fail("Changed runtime must fail before initializing the engine")
    worker(queue, session_factory=factory, versions=versions).run(once=True)
    saved = queue[1].get_job("alice", job["id"])
    assert saved["status"] == "failed" and saved["result"] is None
    assert "runtime_changed" in saved["error"]


def test_engine_exception_is_replaced_with_fixed_safe_error(queue):
    job = enqueue(queue)
    class BadSession(FakeSession):
        def step(self):
            raise RuntimeError("private-dataset-and-model-key")
    worker(queue, session_factory=BadSession).run(once=True)
    saved = queue[1].get_job("alice", job["id"])
    assert saved["status"] == "failed" and saved["result"] is None
    assert "calculation_failed" in saved["error"]
    assert "private-dataset" not in json.dumps(saved)
