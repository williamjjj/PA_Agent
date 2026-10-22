"""Real authenticated mining submission and isolated worker lifecycle."""
import subprocess
import sys

from fastapi.testclient import TestClient

from pa_agent.web.app import app
from pa_agent.web.mining_versions import contract_versions
from pa_agent.web.store import get_store
from tests.web.test_accounts import HEADERS, clients  # noqa: F401


def data(client):
    response = client.post("/api/research/datasets", json={"demo": True})
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, dataset_id, key="mining-api-idempotency-0001", **changes):
    body = {"dataset_id": dataset_id, "config": {"trials": 8, "max_seconds": 30}, "idempotency_key": key, **changes}
    response = client.post("/api/mining/jobs", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def work_once():
    result = subprocess.run([sys.executable, "-m", "pa_agent.web.mining_worker", "--once"],
                            capture_output=True, timeout=40)
    assert result.returncode == 0, result.stderr.decode()


def test_queue_worker_and_export_are_persistent_owned_and_idempotent(clients):
    alice, bob = clients
    dataset = data(alice)
    assert alice.get("/api/mining/status").json()["worker_available"] is False
    job = submit(alice, dataset["id"])
    assert job["status"] == "queued" and job["result"] is None
    assert job["provenance"]["code_revision"] == contract_versions()["code_revision"]
    assert submit(alice, dataset["id"])["id"] == job["id"]
    changed = alice.post("/api/mining/jobs", json={"dataset_id": dataset["id"],
        "config": {"trials": 16, "max_seconds": 30}, "idempotency_key": "mining-api-idempotency-0001"})
    assert changed.status_code == 409
    for suffix in ("", "/export"):
        assert bob.get(f"/api/mining/jobs/{job['id']}{suffix}").status_code == 404
    assert bob.post(f"/api/mining/jobs/{job['id']}/cancel", json={}).status_code == 404
    assert bob.post(f"/api/mining/jobs/{job['id']}/retry", json={"idempotency_key": "mining-retry-other-user"}).status_code == 404
    assert bob.delete(f"/api/mining/jobs/{job['id']}").status_code == 404
    assert bob.get("/api/mining/jobs").json() == []
    assert alice.delete(f"/api/research/datasets/{dataset['id']}").status_code == 409
    assert alice.delete(f"/api/mining/jobs/{job['id']}").status_code == 409
    # Real independent process, same durable store; no in-request work or network.
    work_once()
    finished = alice.get(f"/api/mining/jobs/{job['id']}").json()
    assert finished["status"] == "succeeded", finished
    assert finished["result"]["formula"]["tokens"]
    assert set(finished["result"]["research_result"]["segments"]) == {"train", "validation", "test"}
    assert submit(alice, dataset["id"])["id"] == job["id"]
    summary = alice.get("/api/mining/jobs").json()[0]
    assert "result" not in summary and "checkpoint" not in summary and "lease_token" not in summary
    export = alice.get(f"/api/mining/jobs/{job['id']}/export")
    assert "attachment" in export.headers["content-disposition"]
    assert export.json()["job"]["result"] == finished["result"]
    assert len(export.json()["data"]["bars"]) == 600
    assert "lease_token" not in export.text and "user_id" not in export.json()["job"]
    # Terminal artifacts survive a worker restart without recomputation.
    work_once()
    assert alice.get(f"/api/mining/jobs/{job['id']}").json()["result"] == finished["result"]
    assert alice.delete(f"/api/mining/jobs/{job['id']}").status_code == 200
    assert alice.delete(f"/api/research/datasets/{dataset['id']}").status_code == 200


def test_cancel_retry_and_pa_lease_are_independent(clients):
    alice, _ = clients
    dataset = data(alice)
    uid = alice.get("/api/status").json()["user"]["id"]
    lease = get_store().acquire(uid)
    try:
        job = submit(alice, dataset["id"])
        cancelled = alice.post(f"/api/mining/jobs/{job['id']}/cancel", json={}).json()
        assert cancelled["status"] == "cancelled"
        retry = alice.post(f"/api/mining/jobs/{job['id']}/retry", json={"idempotency_key": "mining-api-explicit-retry"})
        assert retry.status_code == 200, retry.text
        new = retry.json()
        assert new["id"] != job["id"] and new["status"] == "queued"
        assert alice.post(f"/api/mining/jobs/{job['id']}/retry", json={"idempotency_key": "mining-api-explicit-retry"}).json()["id"] == new["id"]
        work_once()
        assert alice.get(f"/api/mining/jobs/{new['id']}").json()["status"] == "succeeded"
    finally:
        get_store().release(uid, lease)


def test_mining_auth_stale_identity_and_input_boundaries(clients):
    alice, bob = clients
    visitor = TestClient(app, headers=HEADERS)
    for path in ("/api/mining/status", "/api/mining/jobs", "/api/mining/jobs/unknown/export"):
        assert visitor.get(path).status_code == 401
    dataset = data(alice)
    body = {"dataset_id": dataset["id"], "idempotency_key": "valid-test-idempotency"}
    assert visitor.post("/api/mining/jobs", json=body).status_code == 401
    assert bob.post("/api/mining/jobs", json=body).status_code == 404
    assert alice.post("/api/mining/jobs", json=body, headers={"x-pa-request": ""}).status_code == 403
    alice_id = alice.get("/api/status").json()["user"]["id"]
    assert bob.get("/api/mining/status", headers={"x-pa-user": alice_id}).status_code == 409
    for config in ({"trials": 129}, {"max_seconds": 121}, {"tokens": ["__import__"]}, {"seed": -1}):
        response = alice.post("/api/mining/jobs", json={**body, "config": config})
        assert response.status_code == 422
    assert alice.post("/api/mining/jobs", json={**body, "owner_id": alice_id}).status_code == 422
    assert alice.get("/api/mining/jobs").json() == []


def test_changed_worker_contract_does_not_run_queued_old_code(clients, monkeypatch):
    alice, _ = clients
    dataset = data(alice)
    versions = {**contract_versions(), "code_revision": "incompatible-old-revision"}
    monkeypatch.setattr("pa_agent.web.mining_api.contract_versions", lambda: versions)
    job = submit(alice, dataset["id"])
    work_once()
    response = alice.get(f"/api/mining/jobs/{job['id']}").json()
    assert response["status"] == "failed" and response["result"] is None
    assert response["error"]
