"""Mining jobs must retain ownership, cancellation and lease fencing under races."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, event, insert, text
from sqlalchemy.exc import IntegrityError

from pa_agent.web import mining_store as module
from pa_agent.web.mining_store import MiningConflict, MiningLimit, MiningStore, MiningStoreError
from pa_agent.web.research_store import DatasetInUse, ResearchStore, datasets
from pa_agent.web.store import Store, users


@pytest.fixture
def mining(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1_800_000_000.0)
    monkeypatch.setattr(module.time, "time", lambda: clock.now)
    accounts = Store("sqlite:///" + str(tmp_path / "mining.sqlite3"),
                     Fernet.generate_key().decode())
    with accounts.engine.begin() as conn:
        for uid in ("alice", "bob"):
            conn.execute(insert(users).values(id=uid, username=uid, password="unused-test-hash",
                         settings=accounts.seal({}), created=clock.now, busy_until=0, lease=""))
    research = ResearchStore(accounts.engine)
    versions = {}
    for uid in ("alice", "bob"):
        versions[uid] = research.put_dataset(uid, {
            "source": "csv", "symbol": "SOL", "timeframe": "1m",
            "market_kind": "continuous_spot", "dataset_version": "research-ohlcv-v1",
            "dataset_hash": hashlib.sha256(uid.encode()).hexdigest(),
            "quality": {"bar_count": 300, "start": 1_700_000_040, "end": 1_700_018_040},
            "bars": [{"time": 1_700_000_040 + index * 60, "open": 10, "high": 12,
                      "low": 9, "close": 11, "volume": 5} for index in range(300)],
        })["id"]
    yield SimpleNamespace(accounts=accounts, research=research, store=MiningStore(accounts.engine),
                          datasets=versions, clock=clock)
    accounts.engine.dispose()


def config(**changes):
    return {"max_seconds": 60, "seed": 42, **changes}


def create(ctx, uid="alice", key="request-1", **changes):
    return ctx.store.create_job(uid, ctx.datasets[uid], config(**changes), key)


def test_jobs_are_owner_scoped_and_require_owned_datasets(mining):
    job = create(mining)
    assert mining.store.get_job("bob", job["id"]) is None
    assert mining.store.list_jobs("bob") == []
    assert mining.store.cancel_job("bob", job["id"]) is None
    assert mining.store.delete_job("bob", job["id"]) is False
    with pytest.raises(MiningStoreError):
        mining.store.create_job("bob", mining.datasets["alice"], config(), "foreign-dataset")
    assert mining.store.get_job("alice", job["id"])["status"] == "queued"
    assert len(mining.store.list_jobs("alice")) == 1


def test_concurrent_idempotent_create_has_one_job_and_conflicting_payload_is_rejected(mining):
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: create(mining), range(12)))
    assert len({row["id"] for row in outcomes}) == 1
    assert len(mining.store.list_jobs("alice")) == 1
    with pytest.raises(MiningConflict):
        create(mining, seed=43)
    other = create(mining, uid="bob")
    assert other["id"] != outcomes[0]["id"]


def test_enqueue_provenance_is_frozen_authoritative_and_not_replaced_by_idempotent_retry(mining):
    provenance = {"dataset_hash": "untrusted-supplied-digest",
                  "versions": {"engine": "original-engine", "feature": "causal-v1"}}
    first = mining.store.create_job("alice", mining.datasets["alice"], config(),
                                    "fixed-provenance", provenance=provenance)
    expected_hash = hashlib.sha256(b"alice").hexdigest()
    assert first["provenance"]["dataset_hash"] == expected_hash
    provenance["versions"]["engine"] = "mutated-input"
    retried = mining.store.create_job("alice", mining.datasets["alice"], config(),
                                      "fixed-provenance", provenance={
                                          "versions": {"engine": "new-worker-version"}})
    assert retried["id"] == first["id"]
    assert retried["created_new"] is False
    assert retried["provenance"] == {
        "dataset_hash": expected_hash,
        "versions": {"engine": "original-engine", "feature": "causal-v1"}}
    retried["provenance"]["versions"]["engine"] = "mutated-return"
    assert mining.store.get_job("alice", first["id"])["provenance"]["versions"]["engine"] == "original-engine"


def test_active_quota_is_atomic_and_idempotent_retries_do_not_consume_slots(mining):
    def submit(index):
        try:
            return create(mining, key=f"concurrent-{index}")
        except MiningLimit:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(submit, range(10)))
    accepted = [row for row in outcomes if row is not None]
    assert len(accepted) == 3
    assert len(mining.store.list_jobs("alice")) == 3
    first = accepted[0]
    duplicate = mining.store.create_job("alice", mining.datasets["alice"], config(),
                                        first["idempotency_key"])
    assert duplicate["id"] == first["id"]
    assert mining.store.cancel_job("alice", first["id"])["status"] == "cancelled"
    assert create(mining, key="replacement")["status"] == "queued"
    assert create(mining, uid="bob")["status"] == "queued"


def test_requested_cancellation_keeps_active_slot_until_worker_acknowledges(mining):
    for index in range(3):
        create(mining, key=f"active-{index}")
    lease = mining.store.claim("worker")
    assert mining.store.cancel_job("alice", lease["id"])["status"] == "cancel_requested"
    with pytest.raises(MiningLimit):
        create(mining, key="still-full")
    mining.store.complete(lease["id"], lease["lease_token"], {"discard": True}, 1)
    assert mining.store.get_job("alice", lease["id"])["status"] == "cancelled"
    assert create(mining, key="now-available")["status"] == "queued"


def test_total_quota_counts_terminal_records_but_owner_can_delete_them(mining):
    ids = []
    for index in range(49):
        job = create(mining, key=f"completed-{index}")
        ids.append(job["id"])
        assert mining.store.cancel_job("alice", job["id"])["status"] == "cancelled"

    def last_slot(index):
        try:
            return create(mining, key=f"last-slot-{index}")
        except MiningLimit:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(last_slot, range(8)))
    assert sum(row is not None for row in attempts) == 1
    with pytest.raises(MiningLimit):
        create(mining, key="one-too-many")
    assert create(mining, key="completed-0")["id"] == ids[0]
    assert mining.store.delete_job("alice", ids[0]) is True
    assert create(mining, key="after-removal")["status"] == "queued"


def test_concurrent_claims_lease_only_one_job_per_user(mining):
    for uid in ("alice", "bob"):
        for index in range(3):
            create(mining, uid=uid, key=f"queue-{index}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda i: mining.store.claim(f"worker-{i}"), range(8)))
    leased = [row for row in claims if row is not None]
    assert len(leased) == 2
    assert {row["user_id"] for row in leased} == {"alice", "bob"}
    assert len({row["id"] for row in leased}) == 2
    assert len({row["lease_token"] for row in leased}) == 2
    assert all(row["status"] == "running" and row["attempts"] == 1 for row in leased)
    assert all(row["lease_until"] > mining.clock.now for row in leased)


def test_mining_lease_does_not_consume_pa_analysis_lease(mining):
    pa_lease = mining.accounts.acquire("alice")
    assert pa_lease
    job = create(mining)
    claimed = mining.store.claim("miner")
    assert claimed["id"] == job["id"]
    assert mining.accounts.acquire("alice") is None
    mining.accounts.release("alice", pa_lease)
    fresh_pa_lease = mining.accounts.acquire("alice")
    assert fresh_pa_lease
    mining.accounts.release("alice", fresh_pa_lease)
    second = create(mining, key="next-job")
    assert mining.store.claim("other-miner") is None
    mining.store.complete(job["id"], claimed["lease_token"], {"winner": "RET"}, 1)
    assert mining.store.claim("other-miner")["id"] == second["id"]


def test_queued_cancellation_is_terminal_and_active_jobs_cannot_be_deleted(mining):
    job = create(mining)
    with pytest.raises(MiningConflict):
        mining.store.delete_job("alice", job["id"])
    assert mining.store.cancel_job("alice", job["id"])["status"] == "cancelled"
    assert mining.store.claim("worker") is None
    assert mining.store.cancel_job("alice", job["id"])["status"] == "cancelled"
    assert mining.store.delete_job("bob", job["id"]) is False
    assert mining.store.delete_job("alice", job["id"]) is True
    assert mining.store.get_job("alice", job["id"]) is None


def test_job_foreign_key_prevents_cross_owner_reference_and_dataset_deletion(mining):
    job = create(mining)
    with pytest.raises(DatasetInUse):
        mining.research.delete_dataset("alice", mining.datasets["alice"])
    with pytest.raises(IntegrityError), mining.accounts.engine.begin() as conn:
        conn.execute(delete(datasets).where(datasets.c.id == mining.datasets["alice"]))
    with pytest.raises(IntegrityError), mining.accounts.engine.begin() as conn:
        conn.execute(text("UPDATE web_mining_jobs SET user_id = :uid WHERE id = :ident"),
                     {"uid": "bob", "ident": job["id"]})
    mining.store.cancel_job("alice", job["id"])
    with pytest.raises(DatasetInUse):
        mining.research.delete_dataset("alice", mining.datasets["alice"])
    assert mining.store.delete_job("alice", job["id"])
    assert mining.research.delete_dataset("alice", mining.datasets["alice"])


def test_completed_result_is_immutable_and_owned_lists_do_not_load_large_payloads(mining):
    settings = config()
    job = mining.store.create_job("alice", mining.datasets["alice"], settings, "immutable")
    settings["seed"] = 999
    assert mining.store.get_job("alice", job["id"])["config"]["seed"] == 42
    lease = mining.store.claim("worker")
    result = {"winner": "RET", "equity": list(range(5000))}
    assert mining.store.complete(job["id"], lease["lease_token"], result, 2)
    result["equity"].append(9000)
    frozen = mining.store.get_job("alice", job["id"])
    assert frozen["status"] == "succeeded"
    assert frozen["result"]["equity"] == list(range(5000))
    assert not mining.store.complete(job["id"], lease["lease_token"], {"winner": "VOL"}, 3)
    assert not mining.store.fail(job["id"], lease["lease_token"], "late failure")
    assert mining.store.get_job("alice", job["id"])["result"] == frozen["result"]
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(mining.accounts.engine, "before_cursor_execute", capture)
    try:
        listing = mining.store.list_jobs("alice")
    finally:
        event.remove(mining.accounts.engine, "before_cursor_execute", capture)
    assert len(listing) == 1
    assert "result" not in listing[0] and "checkpoint" not in listing[0]
    assert len(json.dumps(listing)) < 5000
    assert all("web_mining_jobs.result" not in sql for sql in statements)
    assert all("web_mining_jobs.checkpoint" not in sql for sql in statements)


def test_pagination_is_stable_and_does_not_mix_users(mining):
    for index in range(6):
        job = create(mining, key=f"page-{index}")
        mining.store.cancel_job("alice", job["id"])
        mining.clock.now += 1
    create(mining, uid="bob")
    expected = mining.store.list_jobs("alice", limit=100)
    actual = (mining.store.list_jobs("alice", limit=2)
              + mining.store.list_jobs("alice", offset=2, limit=4))
    assert actual == expected and len(expected) == 6
    assert mining.store.list_jobs("alice", offset=100) == []
    assert len(mining.store.list_jobs("bob")) == 1


def test_heartbeat_renews_current_lease_and_rejects_wrong_token(mining):
    job = create(mining)
    lease = mining.store.claim("worker", lease_seconds=10)
    mining.clock.now += 4
    assert mining.store.heartbeat(job["id"], "wrong-token") is None
    beat = mining.store.heartbeat(job["id"], lease["lease_token"], lease_seconds=10,
                                  consumed_seconds=4)
    assert beat["status"] == "running" and beat["cancel_requested"] is False
    assert beat["lease_until"] == mining.clock.now + 10
    assert beat["consumed_seconds"] >= 4
    mining.clock.now += 7
    assert mining.store.claim("contender") is None
    assert mining.store.heartbeat(job["id"], lease["lease_token"], lease_seconds=10)


def test_expired_token_is_fenced_before_recovery_and_checkpoint_survives_new_attempt(mining):
    job = create(mining)
    first = mining.store.claim("first", lease_seconds=5)
    mining.clock.now += 2
    checkpoint = {"cursor": 4, "candidates": [{"formula": "RET", "score": 0.1}]}
    assert mining.store.save_checkpoint(job["id"], first["lease_token"], checkpoint, 0.2, 2)
    mining.clock.now += 4
    assert mining.store.heartbeat(job["id"], first["lease_token"]) is None
    assert not mining.store.save_checkpoint(job["id"], first["lease_token"], {}, 0.5, 6)
    assert not mining.store.complete(job["id"], first["lease_token"], {"wrong": True}, 6)
    assert not mining.store.fail(job["id"], first["lease_token"], "stale worker")
    recovered = mining.store.claim("second", lease_seconds=5)
    assert recovered["id"] == job["id"] and recovered["attempts"] == 2
    assert recovered["lease_token"] != first["lease_token"]
    assert recovered["checkpoint"] == checkpoint and recovered["progress"] == 0.2
    assert recovered["consumed_seconds"] == 5
    assert not mining.store.complete(job["id"], first["lease_token"], {"stale": True}, 7)
    assert mining.store.complete(job["id"], recovered["lease_token"], {"winner": "RET"}, 7)
    assert mining.store.get_job("alice", job["id"])["result"] == {"winner": "RET"}


@pytest.mark.parametrize("operation", ["heartbeat", "checkpoint", "complete", "fail", "release"])
def test_token_expiring_while_waiting_for_account_lock_cannot_update_job(mining, operation):
    job = create(mining)
    lease = mining.store.claim("worker", lease_seconds=5)
    advanced = False

    def simulate_lock_wait(conn, cursor, statement, parameters, context, executemany):
        nonlocal advanced
        if not advanced and statement.startswith("UPDATE web_users SET settings="):
            mining.clock.now += 6
            advanced = True

    actions = {
        "heartbeat": lambda: mining.store.heartbeat(job["id"], lease["lease_token"]),
        "checkpoint": lambda: mining.store.save_checkpoint(
            job["id"], lease["lease_token"], {"cursor": 99}, 0.9, 1),
        "complete": lambda: mining.store.complete(job["id"], lease["lease_token"], {"late": True}, 1),
        "fail": lambda: mining.store.fail(job["id"], lease["lease_token"], "late failure"),
        "release": lambda: mining.store.release(job["id"], lease["lease_token"], 1),
    }
    event.listen(mining.accounts.engine, "before_cursor_execute", simulate_lock_wait)
    try:
        assert not actions[operation]()
    finally:
        event.remove(mining.accounts.engine, "before_cursor_execute", simulate_lock_wait)
    assert advanced
    current = mining.store.get_job("alice", job["id"])
    assert current["status"] == "running"
    assert current["lease_until"] == lease["lease_until"]
    assert current["checkpoint"] == {} and current["result"] is None


def test_stale_recovery_has_three_attempts_and_no_unbounded_requeue(mining):
    job = create(mining)
    for attempt in range(1, 4):
        lease = mining.store.claim(f"crashing-{attempt}", lease_seconds=5)
        assert lease["id"] == job["id"] and lease["attempts"] == attempt
        mining.clock.now += 6
    assert mining.store.claim("fourth") is None
    finished = mining.store.get_job("alice", job["id"])
    assert finished["status"] == "failed" and finished["attempts"] == 3
    assert finished["consumed_seconds"] == 15
    assert finished["finished"] is not None and finished["error"]


def test_concurrent_recovery_assigns_one_new_token_and_one_attempt(mining):
    job = create(mining)
    first = mining.store.claim("crashed", lease_seconds=5)
    mining.clock.now += 6
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda index: mining.store.claim(f"recovery-{index}"), range(8)))
    recovered = [row for row in claims if row is not None]
    assert len(recovered) == 1
    assert recovered[0]["id"] == job["id"]
    assert recovered[0]["attempts"] == 2
    assert recovered[0]["lease_token"] != first["lease_token"]


def test_stale_recovery_charges_consumed_budget_and_times_out(mining):
    job = create(mining, max_seconds=10)
    first = mining.store.claim("first", lease_seconds=5)
    assert first["attempts"] == 1
    mining.clock.now += 6
    second = mining.store.claim("second", lease_seconds=5)
    assert second["attempts"] == 2 and second["consumed_seconds"] == 5
    mining.clock.now += 6
    assert mining.store.claim("third") is None
    final = mining.store.get_job("alice", job["id"])
    assert final["status"] == "timed_out" and final["result"] is None
    assert final["consumed_seconds"] >= 10


def test_long_outage_charges_only_leased_work_and_preserves_checkpoint(mining):
    job = create(mining, max_seconds=60)
    first = mining.store.claim("before-outage", lease_seconds=30)
    mining.clock.now += 2
    checkpoint = {"cursor": 8, "candidates": [{"formula": "RET", "score": 0.2}]}
    assert mining.store.save_checkpoint(job["id"], first["lease_token"], checkpoint, 0.4, 2)
    mining.clock.now += 3600
    recovered = mining.store.claim("after-outage", lease_seconds=30)
    assert recovered["id"] == job["id"] and recovered["status"] == "running"
    assert recovered["attempts"] == 2 and recovered["consumed_seconds"] == 30
    assert recovered["checkpoint"] == checkpoint and recovered["progress"] == 0.4
    assert recovered["lease_until"] == mining.clock.now + 30
    assert recovered["lease_token"] != first["lease_token"]


def test_cancellation_wins_success_and_worker_observes_request(mining):
    job = create(mining)
    lease = mining.store.claim("worker")
    cancelled = mining.store.cancel_job("alice", job["id"])
    assert cancelled["status"] == "cancel_requested"
    beat = mining.store.heartbeat(job["id"], lease["lease_token"])
    assert beat["cancel_requested"] is True
    mining.store.complete(job["id"], lease["lease_token"], {"must_not_publish": True}, 1)
    final = mining.store.get_job("alice", job["id"])
    assert final["status"] == "cancelled" and final["result"] is None
    assert final["finished"] is not None


def test_cancel_and_complete_race_has_one_consistent_terminal_outcome(mining):
    job = create(mining)
    lease = mining.store.claim("worker")
    simultaneous = Barrier(2)
    result = {"winner": "RET", "score": 0.25}

    def cancel():
        simultaneous.wait(timeout=10)
        return mining.store.cancel_job("alice", job["id"])

    def complete():
        simultaneous.wait(timeout=10)
        return mining.store.complete(job["id"], lease["lease_token"], result, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancellation = pool.submit(cancel)
        completion = pool.submit(complete)
        assert cancellation.result(timeout=10)
        completion.result(timeout=10)
    final = mining.store.get_job("alice", job["id"])
    assert final["status"] in {"cancelled", "succeeded"}
    assert final["result"] == (result if final["status"] == "succeeded" else None)
    assert final["finished"] is not None
    assert not final["lease_token"] and final["lease_until"] == 0


def test_stale_cancel_requested_job_is_cancelled_without_restart(mining):
    job = create(mining)
    first = mining.store.claim("worker", lease_seconds=5)
    mining.store.cancel_job("alice", job["id"])
    mining.clock.now += 6
    assert mining.store.claim("replacement") is None
    final = mining.store.get_job("alice", job["id"])
    assert final["status"] == "cancelled" and final["attempts"] == 1
    assert not mining.store.complete(job["id"], first["lease_token"], {"stale": True}, 1)


def test_checkpoint_and_consumed_budget_are_monotonic_and_detached(mining):
    job = create(mining)
    lease = mining.store.claim("worker")
    checkpoint = {"cursor": 4, "candidates": ["RET"]}
    mining.clock.now += 3
    assert mining.store.save_checkpoint(job["id"], lease["lease_token"], checkpoint, 0.4, 3)
    checkpoint["candidates"].append("VOL")
    saved = mining.store.get_job("alice", job["id"])
    assert saved["checkpoint"]["candidates"] == ["RET"]
    mining.clock.now += 1
    mining.store.heartbeat(job["id"], lease["lease_token"], consumed_seconds=1)
    latest = mining.store.get_job("alice", job["id"])
    assert latest["consumed_seconds"] >= saved["consumed_seconds"]
    assert latest["progress"] == 0.4


def test_underreported_worker_time_cannot_extend_the_wall_clock_budget(mining):
    job = create(mining, max_seconds=10)
    lease = mining.store.claim("worker")
    mining.clock.now += 4
    first = mining.store.heartbeat(job["id"], lease["lease_token"], consumed_seconds=1)
    assert first["status"] == "running" and first["consumed_seconds"] >= 4
    mining.clock.now += 4
    second = mining.store.heartbeat(job["id"], lease["lease_token"], consumed_seconds=1)
    assert second["status"] == "running" and second["consumed_seconds"] >= 8
    mining.clock.now += 4
    final = mining.store.heartbeat(job["id"], lease["lease_token"], consumed_seconds=1)
    assert final["status"] == "timed_out" and final["consumed_seconds"] >= 10
    assert mining.store.get_job("alice", job["id"])["status"] == "timed_out"
    assert not mining.store.complete(job["id"], lease["lease_token"], {"late": True}, 1)


def test_graceful_release_keeps_checkpoint_and_fences_previous_worker(mining):
    job = create(mining)
    first = mining.store.claim("first")
    checkpoint = {"cursor": 3}
    assert mining.store.save_checkpoint(job["id"], first["lease_token"], checkpoint, 0.3, 2)
    assert mining.store.release(job["id"], first["lease_token"], 3)
    queued = mining.store.get_job("alice", job["id"])
    assert queued["status"] == "queued" and queued["checkpoint"] == checkpoint
    assert queued["consumed_seconds"] >= 3
    assert mining.store.heartbeat(job["id"], first["lease_token"]) is None
    next_lease = mining.store.claim("next")
    assert next_lease["id"] == job["id"] and next_lease["lease_token"] != first["lease_token"]
    assert next_lease["checkpoint"] == checkpoint


def test_worker_availability_requires_fresh_heartbeat_and_stop_removes_worker(mining):
    assert mining.store.worker_status()["available"] is False
    versions = {"engine": "mining-test-v1", "features": "causal-v1"}
    mining.store.register_worker("worker-1", versions)
    status = mining.store.worker_status()
    assert status["available"] is True
    assert [worker["id"] for worker in status["workers"]] == ["worker-1"]
    assert status["workers"][0]["versions"] == versions
    mining.clock.now += 31
    assert mining.store.worker_status()["available"] is False
    mining.store.heartbeat_worker("worker-1")
    assert mining.store.worker_status()["available"] is True
    mining.store.stop_worker("worker-1")
    assert mining.store.worker_status()["available"] is False


def test_cancel_between_candidate_and_checkpoint_does_not_advance_saved_progress(mining):
    job = create(mining)
    lease = mining.store.claim("worker")
    assert mining.store.save_checkpoint(job["id"], lease["lease_token"], {"cursor": 1}, 0.1, 1)
    mining.store.cancel_job("alice", job["id"])
    assert not mining.store.save_checkpoint(job["id"], lease["lease_token"], {"cursor": 2}, 0.2, 2)
    final = mining.store.get_job("alice", job["id"])
    assert final["status"] == "cancelled"
    assert final["checkpoint"] == {"cursor": 1} and final["progress"] == 0.1
    assert final["consumed_seconds"] >= 2
    assert final["result"] is None
    assert not mining.store.complete(job["id"], lease["lease_token"], {"late": True}, 2)


def test_busy_users_do_not_fill_the_bounded_claim_candidate_window(mining):
    create(mining, key="alice-first")
    create(mining, key="alice-queued")
    bob = create(mining, uid="bob")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, "CLAIM_BATCH", 1)
        first = mining.store.claim("first-worker")
        assert first["user_id"] == "alice"
        second = mining.store.claim("second-worker")
        assert second["id"] == bob["id"]
