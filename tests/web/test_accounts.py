import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from pa_agent.web.app import app
from pa_agent.web.settings import WebSettings
from pa_agent.web.store import get_store, users

HEADERS = {"x-pa-request": "1"}
PASSWORD = "correct-horse-test-only"


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PA_WEB_INVITE_CODE", raising=False)
    monkeypatch.setenv("PA_WEB_REGISTRATION", "1")
    monkeypatch.setenv("PA_WEB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PA_WEB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_store.cache_clear()
    a, b = TestClient(app, headers=HEADERS), TestClient(app, headers=HEADERS)
    for client, name in ((a, "alice"), (b, "bob")):
        assert client.post("/api/register", json={"username": name, "password": PASSWORD}).status_code == 200
    yield a, b
    get_store().engine.dispose()
    get_store.cache_clear()


def test_secret_encryption_preservation_and_account_isolation(clients):
    a, b = clients
    secret = "sk-alice-personal-secret"
    assert a.put("/api/settings", json={"settings": {"api_key": secret, "model": "alice-model"}}).status_code == 200
    assert secret not in a.get("/api/settings").text
    assert a.get("/api/settings").json()["api_key_configured"] is True
    assert b.get("/api/settings").json()["api_key_configured"] is False
    assert b.get("/api/settings").json()["model"] != "alice-model"
    a.put("/api/settings", json={"settings": {"api_key": "", "thinking": True}})
    uid = a.get("/api/status").json()["user"]["id"]
    assert get_store().settings(uid)["api_key"] == secret
    with get_store().engine.connect() as conn:
        ciphertext = conn.execute(select(users.c.settings).where(users.c.id == uid)).scalar_one()
    assert secret not in ciphertext
    a.put("/api/settings", json={"settings": {}, "clear_secrets": ["api_key"]})
    assert not a.get("/api/settings").json()["api_key_configured"]


def test_tenant_objects_not_accessible_even_with_known_ids(clients):
    a, b = clients
    uid = a.get("/api/status").json()["user"]["id"]
    ident = get_store().put(uid, "record", {"result": {"status": "complete"}, "chat": []})
    experience = get_store().put(uid, "experience", {"notes": "private"})
    snapshot = get_store().put(uid, "snapshot", {"private": True}, ttl=3600)
    b.put("/api/settings", json={"settings": {"api_key": "sk-bob-test-key"}})
    for method, path, body in [
        ("get", f"/api/records/{ident}", None),
        ("delete", f"/api/records/{ident}", None),
        ("post", f"/api/records/{ident}/chat", {"question": "steal"}),
        ("delete", f"/api/experience/{experience}", None),
        ("post", "/api/analyze", {"snapshot_id": snapshot}),
    ]:
        result = b.request(method, path, **({"json": body} if body else {}))
        assert result.status_code == 404
    assert b.get("/api/records").json() == []
    assert b.get("/api/experience").json() == []


def test_logout_revokes_copied_session_and_password_revokes_all_sessions(clients):
    a, _ = clients
    stolen = a.cookies.get("pa_session")
    a.post("/api/logout")
    replay = TestClient(app, headers=HEADERS)
    replay.cookies.set("pa_session", stolen)
    assert replay.get("/api/settings").status_code == 401
    a.post("/api/session", json={"username": "alice", "password": PASSWORD})
    old = a.cookies.get("pa_session")
    assert a.post("/api/password", json={"old_password": PASSWORD, "new_password": PASSWORD+"-new"}).status_code == 200
    replay.cookies.set("pa_session", old)
    assert replay.get("/api/settings").status_code == 401
    assert a.get("/api/settings").status_code == 200


def test_csrf_and_validation_do_not_echo_secrets(clients):
    a, _ = clients
    result = a.put("/api/settings", headers={"origin": "https://evil.example"}, json={"settings": {}})
    assert result.status_code == 403
    invalid = a.post("/api/session", json={"username": "a", "password": "private-secret-value"})
    assert invalid.status_code == 422 and "private-secret-value" not in invalid.text
    assert TestClient(app).post("/api/logout").status_code == 403


def test_invite_and_case_normalization(clients, monkeypatch):
    a, _ = clients
    client = TestClient(app, headers=HEADERS)
    assert client.post("/api/session", json={"username": "ALICE", "password": PASSWORD}).status_code == 200
    monkeypatch.setenv("PA_WEB_INVITE_CODE", "invitation-test")
    assert client.post("/api/register", json={"username": "charlie", "password": PASSWORD}).status_code == 403
    assert client.post("/api/register", json={"username": "charlie", "password": PASSWORD, "invite_code": "invitation-test"}).status_code == 200


def test_database_lease_and_rate_limit_are_atomic(clients):
    a, _ = clients
    store = get_store()
    uid = a.get("/api/status").json()["user"]["id"]
    with ThreadPoolExecutor(max_workers=5) as pool:
        leases = list(pool.map(lambda _: store.acquire(uid), range(5)))
    active = [lease for lease in leases if lease]
    assert len(active) == 1
    store.release(uid, "wrong")
    assert store.acquire(uid) is None
    store.release(uid, active[0])
    assert store.acquire(uid)
    with ThreadPoolExecutor(max_workers=8) as pool:
        allowed = list(pool.map(lambda _: store.rate_limit("test-limit", 3), range(8)))
    assert sum(allowed) == 3


def test_failed_stream_is_saved_but_never_marked_complete(clients):
    a, _ = clients
    a.put("/api/settings", json={"settings": {"api_key": "test-key-never-sent"}})
    csv = a.get("/api/demo").json()["csv"]
    def fake(frame, cfg, store, uid, cancel, emit, previous, count):
        emit({"event": "stage", "stage": "Stage1Started"})
        return {"status": "failed", "meta": {}, "stage1": None, "stage2": None,
                "error": "validation failed", "usage": {}, "strategies": []}, {}
    with patch("pa_agent.web.analysis.analyze", fake):
        result = a.post("/api/analyze", json={"data": {"csv": csv}})
    events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
    assert [event["event"] for event in events] == ["stage", "result"]
    assert events[-1]["data"]["status"] == "failed"
    assert a.get("/api/records").json()[0]["status"] == "failed"


def test_production_never_falls_back_to_ephemeral_sqlite(monkeypatch):
    get_store.cache_clear()
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/status").json()["configured"] is False
        assert client.get("/api/settings").status_code == 503
    get_store.cache_clear()


def test_bridge_and_snapshot_remain_private_and_match_chart(clients, monkeypatch):
    from pa_agent.web.data import ImportRequest, parse_csv
    a, b = clients
    csv = a.get("/api/demo").json()["csv"]
    request = {"source": "mt5", "symbol": "XAUUSD", "timeframe": "15m", "count": 100}
    assert a.put("/api/bridge", json={"csv": csv}).status_code == 200
    assert b.post("/api/market", json=request).status_code == 422
    chart = a.post("/api/market", json=request).json()
    assert len(chart["bars"]) == 100
    assert chart["source"] == "mt5" and "snapshot_id" in chart
    assert a.post("/api/market", json={**request, "symbol": "BTCUSD"}).status_code == 422
    captured = []
    def fake(frame, cfg, store, uid, cancel, emit, previous, count):
        captured.append((frame, cfg.api_key))
        return {"status": "failed", "meta": {}, "stage1": None, "stage2": None,
                "error": "test fixture", "usage": {}, "strategies": []}, {}
    a.put("/api/settings", json={"settings": {"api_key": "account-a-test-key"}})
    monkeypatch.setattr("pa_agent.web.analysis.analyze", fake)
    response = a.post("/api/analyze", json={"snapshot_id": chart["snapshot_id"]})
    assert response.status_code == 200
    assert captured[0][1] == "account-a-test-key"
    assert captured[0][0].bars[0].ts_open == chart["bars"][-1]["ts_open"]
    assert captured[0][0].bars[-1].close == chart["bars"][0]["close"]


def test_followup_uses_grounded_record_and_persists_to_owner(clients, monkeypatch):
    from pa_agent.ai.deepseek_client import AIReply, AIUsage
    from pa_agent.orchestrator.two_stage import _build_empty_record
    from tests.integration.conftest import make_frame, VALID_STAGE1, VALID_STAGE2
    a, b = clients
    a.put("/api/settings", json={"settings": {"api_key": "account-a-key"}})
    uid = a.get("/api/status").json()["user"]["id"]
    record = _build_empty_record(make_frame(), WebSettings().core()).model_copy(
        update={"stage1_diagnosis": VALID_STAGE1, "stage2_decision": VALID_STAGE2})
    ident = get_store().put(uid, "record", {
        "result": {"status": "complete"}, "record": record.model_dump(mode="json"), "chat": []})
    calls = []
    class FakeClient:
        def stream_chat(self, messages, **kwargs):
            calls.append(messages)
            kwargs["on_content_token"]("Follow-up answer")
            return AIReply("Follow-up answer", "Reasoning", {}, AIUsage(10, 0, 5, 15), "test", 1)
    monkeypatch.setattr("pa_agent.web.analysis.ModelClient", lambda cfg: FakeClient())
    response = a.post(f"/api/records/{ident}/chat", json={"question": "What invalidates this plan?"})
    assert '"event": "chat_result"' in response.text
    assert calls[0][-1]["content"] == "What invalidates this plan?"
    assert "XAUUSD" in str(calls[0][:-1])
    saved = a.get(f"/api/records/{ident}").json()["chat"]
    assert saved[0]["content"] == "Follow-up answer"
    assert saved[0]["usage"]["total_tokens"] == 15
    assert b.get(f"/api/records/{ident}").status_code == 404


def test_experience_lookup_is_tenant_and_cycle_scoped(clients):
    from pa_agent.web.analysis import AccountExperience
    a, b = clients
    uid = a.get("/api/status").json()["user"]["id"]
    other = b.get("/api/status").json()["user"]["id"]
    store = get_store()
    payload = {"case_type": "success", "cycle_position": "spike", "content": {"notes": "private"}}
    own = store.put(uid, "experience", payload)
    store.put(other, "experience", {**payload, "content": {"notes": "other"}})
    store.put(uid, "experience", {**payload, "cycle_position": "trading_range"})
    entries = AccountExperience(store, uid).read_top5("spike")
    assert len(entries) == 1 and entries[0].filename == own
    assert b.get("/api/experience/" + own).status_code == 404
