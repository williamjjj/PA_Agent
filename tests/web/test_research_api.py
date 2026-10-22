"""Exercise owned research ingestion, immutable records and bounded inputs."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from pa_agent.web.app import app
from pa_agent.web.data import demo_csv
from pa_agent.web.research import ResearchConfig, run_research
from pa_agent.web.research_api import research_demo_csv
from pa_agent.web.research_data import DatasetImport, parse_dataset_csv
from pa_agent.web.research_store import ResearchStore
from pa_agent.web.store import get_store
from tests.web.test_accounts import HEADERS, clients  # noqa: F401


def dataset(client, **request):
    response = client.post("/api/research/datasets", json=request or {"demo": True})
    assert response.status_code == 200, response.text
    return response.json()


def run(client, ident, config=None):
    response = client.post("/api/research/runs", json={"dataset_id": ident, "config": config or {}})
    assert response.status_code == 200, response.text
    return response.json()


def test_demo_complete_workflow_is_reproducible_private_and_persistent(clients, monkeypatch):
    alice, bob = clients
    demo = alice.get("/api/research/demo").json()
    assert demo["source"] == "demo" and len(demo["csv"].splitlines()) == 601
    data = dataset(alice)
    assert data["source"] == "demo" and data["bar_count"] == 600
    assert data["provenance"]["kind"] == "simulated"
    assert dataset(alice)["id"] == data["id"]
    other = dataset(bob)
    assert other["id"] != data["id"] and other["dataset_hash"] == data["dataset_hash"]
    result = run(alice, data["id"])
    ident = result["id"]
    assert result["result"]["dataset_hash"] == data["dataset_hash"]
    assert result["result"]["split"]["train"]["end_exclusive"] == 360
    summary = alice.get("/api/research/runs").json()[0]
    assert summary["test_return"] == result["result"]["segments"]["test"]["metrics"]["total_return"]
    assert summary["factor"] == "DEV" and "equity_curve" not in str(summary)
    assert bob.get("/api/research/runs").json() == []
    for suffix in ("", "/export"):
        assert bob.get(f"/api/research/runs/{ident}{suffix}").status_code == 404
    assert bob.delete(f"/api/research/runs/{ident}").status_code == 404
    assert bob.delete(f"/api/research/datasets/{data['id']}").status_code == 404
    assert bob.post("/api/research/runs", json={"dataset_id": data["id"]}).status_code == 404
    exported = alice.get(f"/api/research/runs/{ident}/export")
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    archive = exported.json()
    assert len(archive["data"]["bars"]) == 600
    assert run_research(archive["data"], ResearchConfig.model_validate(archive["result"]["config"])) == archive["result"]
    monkeypatch.setattr("pa_agent.web.research_api.run_research", lambda *_: pytest.fail("must not recompute saved evidence"))
    assert alice.get(f"/api/research/runs/{ident}").json() == result
    assert alice.get(f"/api/research/runs/{ident}/export").json() == archive
    assert alice.delete(f"/api/research/datasets/{data['id']}").status_code == 409
    assert alice.delete(f"/api/research/runs/{ident}").status_code == 200
    assert alice.delete(f"/api/research/datasets/{data['id']}").status_code == 200


def test_research_auth_csrf_and_stale_tab_guards(clients):
    alice, bob = clients
    visitor = TestClient(app, headers=HEADERS)
    for path in ("/api/research/datasets", "/api/research/runs", "/api/research/runs/unknown/export"):
        assert visitor.get(path).status_code == 401
    assert visitor.post("/api/research/datasets", json={"demo": True}).status_code == 401
    assert alice.post("/api/research/datasets", json={"demo": True}, headers={"x-pa-request": ""}).status_code == 403
    alice_id = alice.get("/api/status").json()["user"]["id"]
    assert bob.post("/api/research/datasets", json={"demo": True}, headers={"x-pa-user": alice_id}).status_code == 409
    assert bob.get("/api/research/runs", headers={"x-pa-user": alice_id}).status_code == 409
    for body in ({}, {"demo": True, "snapshot_id": "id"}, {"demo": True, "owner_id": alice_id}, {"snapshot_id": "id"}):
        assert alice.post("/api/research/datasets", json=body).status_code == 422


def test_data_errors_do_not_leak_input_and_release_account_lease(clients, monkeypatch):
    alice, _ = clients
    request = {"data": {"csv": "private-provider-key", "timeframe": "1h"}}
    response = alice.post("/api/research/datasets", json=request)
    assert response.status_code == 422 and "private-provider-key" not in response.text
    chart = dataset(alice)
    monkeypatch.setattr("pa_agent.web.research_api.run_research", lambda *_: (_ for _ in ()).throw(RuntimeError("private-secret")))
    response = alice.post("/api/research/runs", json={"dataset_id": chart["id"]})
    assert response.status_code == 503 and "private-secret" not in response.text
    uid = alice.get("/api/status").json()["user"]["id"]
    lease = get_store().acquire(uid)
    assert lease
    assert alice.post("/api/research/runs", json={"dataset_id": chart["id"]}).status_code == 409
    get_store().release(uid, lease)
    assert alice.get("/api/research/runs").json() == []


def test_research_body_limit_is_scoped_and_accepts_valid_large_csv(clients):
    alice, _ = clients
    # 600 valid rows with long but finite decimal volume; >600KB, under2MB.
    rows = research_demo_csv().splitlines()
    padded = [rows[0]] + [row.rsplit(",", 1)[0] + ",1000." + "0" * 1100 for row in rows[1:]]
    text = "\n".join(padded)
    assert 600_000 < len(text) < 2_000_000
    created = dataset(alice, data={"csv": text, "timeframe": "1h"})
    assert created["bar_count"] == 600
    assert alice.post("/api/import", json={"csv": text}).status_code == 413
    assert alice.post("/api/research/datasets", content=b" " * 2_500_001).status_code == 413


def test_snapshots_require_owned_supported_market_and_copy_immutable_data(clients):
    alice, bob = clients
    uid = alice.get("/api/status").json()["user"]["id"]
    chart = parse_dataset_csv(DatasetImport(csv=research_demo_csv(), timeframe="1h", symbol="CUSTOM"))
    ident = get_store().put(uid, "snapshot", chart, ttl=3600)
    request = {"snapshot_id": ident, "market_kind": "continuous_spot"}
    assert bob.post("/api/research/datasets", json=request).status_code == 404
    data = dataset(alice, **request)
    assert data["source"] == "csv" and data["symbol"] == "CUSTOM"
    saved = ResearchStore(get_store().engine).get_dataset(uid, data["id"])
    assert saved["chart"]["bars"] == chart["bars"]
    bad = deepcopy(chart)
    bad["source"] = "yfinance"
    invalid_id = get_store().put(uid, "snapshot", bad, ttl=3600)
    assert alice.post("/api/research/datasets", json={**request, "snapshot_id": invalid_id}).status_code == 422
    get_store().remove(uid, "snapshot", ident)
    assert alice.post("/api/research/datasets", json=request).status_code == 404
    assert run(alice, data["id"])["dataset"]["id"] == data["id"]


def test_insufficient_or_gapped_history_never_becomes_a_backtest(clients):
    alice, _ = clients
    response = alice.post("/api/research/datasets", json={"data": {"csv": demo_csv()}})
    assert response.status_code == 422
    rows = research_demo_csv().splitlines()
    del rows[400]
    data = dataset(alice, data={"csv": "\n".join(rows), "timeframe": "1h"})
    assert data["quality"]["missing_intervals"] == 1
    response = alice.post("/api/research/runs", json={"dataset_id": data["id"]})
    assert response.status_code == 422
    assert alice.get("/api/research/runs").json() == []


def test_birdeye_history_uses_only_current_users_settings(clients, monkeypatch):
    alice, bob = clients
    alice.put("/api/settings", json={"settings": {"birdeye_api_key": "alice-history-key"}})
    seen = []
    chart = parse_dataset_csv(DatasetImport(csv=research_demo_csv(), timeframe="1h"))
    def history(data, cfg):
        seen.append(cfg.birdeye_api_key)
        return chart
    monkeypatch.setattr("pa_agent.web.research_api.fetch_history", history)
    request = {"history": {"instrument_id": "So11111111111111111111111111111111111111112", "symbol": "SOL",
                           "timeframe": "1h", "start": 1704067200, "end": 1704067200 + 600 * 3600}}
    assert "alice-history-key" not in str(dataset(alice, **request))
    dataset(bob, **request)
    assert seen == ["alice-history-key", ""]
