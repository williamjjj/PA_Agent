"""Research versions remain isolated, immutable and quota-safe under concurrency."""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, event, insert, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from pa_agent.web import research_store as module
from pa_agent.web.research_store import (
    DatasetInUse,
    ResearchLimit,
    ResearchStore,
    ResearchStoreError,
    datasets,
    runs,
)
from pa_agent.web.store import Store, users


@pytest.fixture
def research(tmp_path):
    accounts = Store("sqlite:///" + str(tmp_path / "research.sqlite3"),
                     Fernet.generate_key().decode())
    with accounts.engine.begin() as conn:
        for uid in ("alice", "bob"):
            conn.execute(insert(users).values(id=uid, username=uid, password="unused-test-hash",
                         settings=accounts.seal({}), created=time.time(), busy_until=0, lease=""))
    yield ResearchStore(accounts.engine)
    accounts.engine.dispose()


def chart(marker="first", count=300):
    return {
        "source": "csv", "symbol": "SOL", "timeframe": "1m",
        "market_kind": "continuous_spot", "dataset_version": "research-ohlcv-v1",
        "dataset_hash": hashlib.sha256(marker.encode()).hexdigest(),
        "captured_at": 1_800_000_000_000,
        "instrument": {"id": "csv:SOL", "symbol": "SOL"},
        "quality": {"bar_count": count, "start": 1_700_000_040,
                    "end": 1_700_000_040 + count * 60, "gaps": 0},
        "provenance": {"identity_verified": False},
        "bars": [{"time": 1_700_000_040 + index * 60, "open": 10, "high": 12,
                  "low": 9, "close": 11, "volume": 5} for index in range(count)],
    }


def test_schema_is_created_with_enforced_referential_integrity(research):
    with research.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert conn.execute(select(datasets.c.id)).all() == []
        assert conn.execute(select(runs.c.id)).all() == []
    for dialect in (sqlite.dialect(), postgresql.dialect()):
        dataset_sql = str(CreateTable(datasets).compile(dialect=dialect))
        run_sql = str(CreateTable(runs).compile(dialect=dialect))
        assert "UNIQUE (user_id, digest)" in dataset_sql
        assert "FOREIGN KEY(user_id, dataset_id)" in run_sql
        assert "REFERENCES web_research_datasets (user_id, id) ON DELETE RESTRICT" in run_sql


def test_same_owner_duplicate_is_idempotent_but_other_owner_gets_own_version(research):
    original = chart()
    first = research.put_dataset("alice", original)
    duplicate = chart()
    duplicate["captured_at"] += 10_000
    again = research.put_dataset("alice", duplicate)
    other = research.put_dataset("bob", original)
    assert first["created_new"] is True and again["created_new"] is False
    assert first["id"] == again["id"] != other["id"]
    assert again["metadata"]["captured_at"] == original["captured_at"]
    assert research.count_datasets("alice") == research.count_datasets("bob") == 1
    assert research.get_dataset("bob", first["id"]) is None
    assert research.delete_dataset("bob", first["id"]) is False
    assert [item["id"] for item in research.list_datasets("bob")] == [other["id"]]


def test_concurrent_duplicate_ingestion_has_one_row_and_keeps_all_candles(research):
    original = chart(count=5000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: research.put_dataset("alice", original), range(12)))
    assert len({row["id"] for row in results}) == 1
    assert sum(row["created_new"] for row in results) == 1
    assert research.count_datasets("alice") == 1
    saved = research.get_dataset("alice", results[0]["id"])
    assert saved["chart"]["bars"] == original["bars"]


def test_metadata_lists_avoid_loading_chart_and_run_time_series(research):
    version = research.put_dataset("alice", chart(count=5000))
    payload = {"result": {"config": {"factor": "RET", "train_fraction": 0.6},
                          "metrics": {"return": 0.12}, "equity": list(range(5000)),
                          "segments": {"test": {"metrics": {"total_return": 0.12,
                                                             "benchmark_return": 0.08}}},
                          "trades": [{"entry": index} for index in range(1000)]},
               "dataset": {"id": version["id"], "symbol": "SOL", "timeframe": "1m",
                           "bars": list(range(5000))}}
    run = research.create_run("alice", version["id"], payload)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(research.engine, "before_cursor_execute", capture)
    try:
        listed_data = research.list_datasets("alice")
        listed_runs = research.list_runs("alice")
    finally:
        event.remove(research.engine, "before_cursor_execute", capture)
    assert len(listed_data) == len(listed_runs) == 1
    assert "chart" not in listed_data[0] and "bars" not in json.dumps(listed_data)
    assert listed_data[0]["metadata"]["bar_count"] == 5000
    assert "payload" not in listed_runs[0]
    assert listed_runs[0]["summary"]["config"] == payload["result"]["config"]
    assert listed_runs[0]["summary"]["factor"] == "RET"
    assert listed_runs[0]["summary"]["test_return"] == 0.12
    assert listed_runs[0]["summary"]["benchmark_return"] == 0.08
    assert listed_runs[0]["summary"]["symbol"] == "SOL"
    assert listed_runs[0]["summary"]["timeframe"] == "1m"
    assert "equity" not in listed_runs[0]["summary"]["result"]
    assert "trades" not in listed_runs[0]["summary"]["result"]
    assert len(json.dumps(listed_runs)) < 2000
    assert all("web_research_datasets.chart" not in sql for sql in statements)
    assert all("web_research_runs.payload" not in sql for sql in statements)
    assert research.get_run("alice", run["id"])["payload"] == payload


def test_stored_chart_and_run_do_not_alias_mutable_input_or_return_objects(research):
    original = chart()
    version = research.put_dataset("alice", original)
    original["bars"][0]["close"] = 999
    version["metadata"]["quality"]["bar_count"] = 1
    assert research.get_dataset("alice", version["id"])["chart"]["bars"][0]["close"] == 11
    assert research.list_datasets("alice")[0]["metadata"]["quality"]["bar_count"] == 300
    payload = {"result": {"config": {"factor": "RET"}, "equity": [1, 2, 3]}}
    run = research.create_run("alice", version["id"], payload)
    payload["result"]["equity"].append(4)
    run["payload"]["result"]["config"]["factor"] = "VOL"
    assert research.get_run("alice", run["id"])["payload"] == {
        "result": {"config": {"factor": "RET"}, "equity": [1, 2, 3]}}


def test_run_scope_and_dataset_references_are_checked_in_database(research):
    version = research.put_dataset("alice", chart())
    with pytest.raises(ResearchStoreError, match="不存在"):
        research.create_run("bob", version["id"], {"result": {}})
    run = research.create_run("alice", version["id"], {"result": {"status": "complete"}})
    assert research.get_run("bob", run["id"]) is None
    assert research.list_runs("bob") == []
    with pytest.raises(DatasetInUse):
        research.delete_dataset("alice", version["id"])
    with pytest.raises(IntegrityError), research.engine.begin() as conn:
        conn.execute(delete(datasets).where(datasets.c.id == version["id"]))
    with pytest.raises(IntegrityError), research.engine.begin() as conn:
        conn.execute(insert(runs).values(id="cross-owner", user_id="bob",
                     dataset_id=version["id"], payload={}, summary={}, created=time.time()))
    assert research.get_dataset("alice", version["id"])
    assert research.count_runs("alice") == 1


def test_dataset_quota_is_atomic_and_duplicate_does_not_consume_it(research, monkeypatch):
    monkeypatch.setattr(module, "MAX_DATASETS", 3)

    def put(index):
        try:
            return research.put_dataset("alice", chart(str(index)))
        except ResearchLimit:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(put, range(8)))
    created = [row for row in outcomes if row is not None]
    assert len(created) == research.count_datasets("alice") == 3
    saved = research.get_dataset("alice", created[0]["id"])
    duplicate = research.put_dataset("alice", saved["chart"])
    assert duplicate["id"] == saved["id"] and duplicate["created_new"] is False
    assert research.delete_dataset("alice", created[1]["id"])
    assert research.put_dataset("alice", chart("replacement"))["created_new"]
    assert research.count_datasets("alice") == 3
    assert research.put_dataset("bob", chart("separate-owner"))["created_new"]


def test_run_deletion_is_owner_scoped_and_releases_dataset_reference(research):
    version = research.put_dataset("alice", chart())
    run = research.create_run("alice", version["id"], {"result": {}})
    assert research.delete_run("bob", run["id"]) is False
    assert research.get_run("alice", run["id"]) is not None
    with pytest.raises(DatasetInUse):
        research.delete_dataset("alice", version["id"])
    assert research.delete_run("alice", run["id"]) is True
    assert research.delete_run("alice", run["id"]) is False
    assert research.count_runs("alice") == 0
    assert research.delete_dataset("alice", version["id"]) is True


def test_run_quota_is_atomic(research, monkeypatch):
    monkeypatch.setattr(module, "MAX_RUNS", 4)
    version = research.put_dataset("alice", chart())

    def create(index):
        try:
            return research.create_run("alice", version["id"], {"result": {"number": index}})
        except ResearchLimit:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(create, range(10)))
    assert sum(row is not None for row in outcomes) == 4
    assert research.count_runs("alice") == 4
    assert len({row["id"] for row in outcomes if row is not None}) == 4


def test_concurrent_delete_and_run_never_leave_a_dangling_reference(research):
    version = research.put_dataset("alice", chart())

    def remove():
        try:
            return research.delete_dataset("alice", version["id"])
        except DatasetInUse:
            return False

    def create():
        try:
            return research.create_run("alice", version["id"], {"result": {}})
        except ResearchStoreError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(remove)
        creation = pool.submit(create)
        removed, run = deletion.result(), creation.result()
    if removed:
        assert run is None and research.get_dataset("alice", version["id"]) is None
        assert research.count_runs("alice") == 0
    else:
        assert run and research.get_dataset("alice", version["id"])
        assert research.count_runs("alice") == 1


def test_pagination_is_stable_bounded_and_owner_scoped(research):
    for index in range(6):
        version = research.put_dataset("alice", chart(str(index)))
        research.create_run("alice", version["id"], {"result": {"number": index}})
    research.put_dataset("bob", chart("other"))
    for listing in (research.list_datasets, research.list_runs):
        expected = listing("alice", limit=100)
        pages = listing("alice", limit=2) + listing("alice", offset=2, limit=4)
        assert pages == expected and len(expected) == 6
        assert listing("alice", offset=100) == []


def test_nonexistent_account_and_nonstandard_json_are_rejected(research):
    with pytest.raises(ResearchStoreError, match="账户不存在"):
        research.put_dataset("missing", chart())
    malformed = chart()
    malformed["bars"][0]["close"] = float("nan")
    with pytest.raises(ResearchStoreError, match="JSON"):
        research.put_dataset("alice", malformed)
    with pytest.raises(ResearchStoreError, match="哈希"):
        research.put_dataset("alice", {"dataset_hash": "not-a-digest"})
    version = research.put_dataset("alice", chart())
    with pytest.raises(ResearchStoreError, match="JSON"):
        research.create_run("alice", version["id"], {"result": float("inf")})
    assert module.MAX_DATASETS == 20 and module.MAX_RUNS == 100
