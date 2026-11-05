"""Administrator bootstrap and persistent invitation access boundaries."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select, text

import pa_agent.web.store as store_module
from pa_agent.web.app import app
from pa_agent.web.store import ConfigurationError, Store, get_store, users

HEADERS = {"x-pa-request": "1"}
ADMIN = "site-admin"
ADMIN_PASSWORD = "administrator-bootstrap-test-only"
PASSWORD = "ordinary-account-test-only"
INVITE = "legacy-invitation-test-only"


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    url = "sqlite:///" + str(tmp_path / "accounts.sqlite3")
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("PA_WEB_ENCRYPTION_KEY", key)
    monkeypatch.setenv("PA_WEB_REGISTRATION", "0")
    monkeypatch.setenv("PA_WEB_INVITE_CODE", INVITE)
    monkeypatch.setenv("PA_WEB_ADMIN_USERNAME", ADMIN)
    monkeypatch.setenv("PA_WEB_ADMIN_PASSWORD", ADMIN_PASSWORD)
    get_store.cache_clear()
    yield url, key
    if get_store.cache_info().currsize:
        get_store().engine.dispose()
    get_store.cache_clear()


def login_admin():
    client = TestClient(app, headers=HEADERS)
    result = client.post("/api/session", json={"username": ADMIN, "password": ADMIN_PASSWORD})
    assert result.status_code == 200, result.text
    assert result.json()["user"]["is_admin"] is True
    client.headers["x-pa-user"] = result.json()["user"]["id"]
    return client


def register(client, username, code=INVITE, **extra):
    return client.post("/api/register", json={
        "username": username, "password": PASSWORD, "invite_code": code, **extra,
    })


def restart():
    get_store().engine.dispose()
    get_store.cache_clear()


def test_bootstrap_admin_logs_in_without_invitation_and_remains_independent(configured):
    admin = login_admin()
    ordinary = TestClient(app, headers=HEADERS)
    created = register(ordinary, "alice")
    assert created.status_code == 200
    assert created.json()["user"]["is_admin"] is False
    assert created.json()["user"]["id"] != admin.get("/api/status").json()["user"]["id"]
    assert admin.get("/api/admin/invite").json()["code"] == INVITE
    assert ADMIN_PASSWORD not in admin.get("/api/status").text
    assert INVITE not in ordinary.get("/api/status").text
    assert INVITE not in TestClient(app).get("/api/status").text

    # Administrative privileges do not grant access to another tenant's keys.
    assert ordinary.put("/api/settings", json={"settings": {"api_key": "ordinary-private-key"}}).status_code == 200
    assert admin.get("/api/settings").json()["api_key_configured"] is False
    uid = created.json()["user"]["id"]
    private = get_store().put(uid, "record", {"result": {"status": "complete"}})
    assert admin.get("/api/records/" + private).status_code == 404


def test_invitation_endpoints_require_database_admin_role(configured):
    admin = login_admin()
    ordinary = TestClient(app, headers=HEADERS)
    assert register(ordinary, "alice").status_code == 200
    guest = TestClient(app, headers=HEADERS)
    before = admin.get("/api/admin/invite").json()
    for client, expected in ((guest, 401), (ordinary, 403)):
        for method, path, body in (
            ("GET", "/api/admin/invite", None),
            ("PUT", "/api/admin/invite", {"enabled": True, "code": "attacker-chosen-invitation"}),
            ("POST", "/api/admin/invite/rotate", None),
        ):
            response = client.request(method, path, **({"json": body} if body else {}))
            assert response.status_code == expected
            assert INVITE not in response.text
    assert admin.get("/api/admin/invite").json() == before


def test_revoked_admin_role_is_not_retained_in_session_or_recreated_on_restart(configured):
    admin = login_admin()
    with get_store().engine.begin() as conn:
        conn.execute(text("DELETE FROM web_admins"))
    assert admin.get("/api/status").json()["user"]["is_admin"] is False
    assert admin.get("/api/admin/invite").status_code == 403
    assert admin.post("/api/admin/invite/rotate").status_code == 403
    restart()
    assert admin.get("/api/admin/invite").status_code == 403
    assert get_store().login(ADMIN, ADMIN_PASSWORD).public()["is_admin"] is False


@pytest.mark.parametrize("injection", [{"is_admin": True}, {"role": "admin"}])
def test_registration_cannot_inject_an_administrator_role(configured, injection):
    client = TestClient(app, headers=HEADERS)
    response = register(client, "intruder", **injection)
    assert response.status_code == 422
    assert get_store().login("intruder", PASSWORD) is None
    assert register(client, "intruder").json()["user"]["is_admin"] is False
    assert client.get("/api/admin/invite").status_code == 403


def test_admin_actions_reject_the_previous_tabs_account_identity(configured):
    admin = login_admin()
    ordinary = TestClient(app, headers=HEADERS)
    ordinary_id = register(ordinary, "alice").json()["user"]["id"]
    admin_id = admin.headers["x-pa-user"]
    before = admin.get("/api/admin/invite").json()
    for client, stale_id in ((ordinary, admin_id), (admin, ordinary_id)):
        for method, path in (("GET", "/api/admin/invite"), ("POST", "/api/admin/invite/rotate")):
            result = client.request(method, path, headers={"x-pa-user": stale_id})
            assert result.status_code == 409
            assert result.json()["detail"]["code"] == "account_changed"
    assert admin.get("/api/admin/invite").json() == before


def test_admin_mutations_keep_csrf_and_validation_protection(configured):
    admin = login_admin()
    before = admin.get("/api/admin/invite").json()
    assert admin.post("/api/admin/invite/rotate", headers={"origin": "https://evil.example"}).status_code == 403
    no_header = TestClient(app)
    no_header.cookies.update(admin.cookies)
    assert no_header.post("/api/admin/invite/rotate").status_code == 403
    for invalid in ("", "short", "sensitive-invalid-code-" * 20):
        response = admin.put("/api/admin/invite", json={"enabled": True, "code": invalid})
        assert response.status_code == 422
        if invalid:
            assert invalid not in response.text
    assert admin.get("/api/admin/invite").json() == before


def test_invitation_rotation_and_revocation_persist_without_environment_fallback(configured, monkeypatch):
    admin = login_admin()
    custom = "administrator-chosen-invitation"
    changed = admin.put("/api/admin/invite", json={"enabled": True, "code": custom})
    assert changed.status_code == 200
    assert changed.json()["code"] == custom
    assert changed.json()["enabled"] is True
    assert isinstance(changed.json()["updated_at"], (int, float))
    assert register(TestClient(app, headers=HEADERS), "old-code", INVITE).status_code == 403
    assert register(TestClient(app, headers=HEADERS), "custom-code", custom).status_code == 200

    rotated = admin.post("/api/admin/invite/rotate")
    assert rotated.status_code == 200
    replacement = rotated.json()["code"]
    assert replacement != custom and len(replacement) >= 24
    assert rotated.json()["enabled"] is True
    assert register(TestClient(app, headers=HEADERS), "obsolete-code", custom).status_code == 403
    assert register(TestClient(app, headers=HEADERS), "new-code", replacement).status_code == 200
    with get_store().engine.connect() as conn:
        encrypted = conn.execute(text("SELECT code FROM web_registration_policy WHERE id = 1")).scalar_one()
    assert replacement not in encrypted

    assert admin.put("/api/admin/invite", json={"enabled": False, "code": replacement}).status_code == 200
    monkeypatch.setenv("PA_WEB_REGISTRATION", "1")
    monkeypatch.setenv("PA_WEB_INVITE_CODE", "environment-code-must-not-return")
    restart()
    assert admin.get("/api/admin/invite").json()["enabled"] is False
    assert TestClient(app).get("/api/status").json()["registration_open"] is False
    for code in (replacement, INVITE, "environment-code-must-not-return", ""):
        assert register(TestClient(app, headers=HEADERS), "closed-user", code).status_code == 403
    reopened = admin.post("/api/admin/invite/rotate").json()
    assert reopened["enabled"] is True and reopened["code"] != replacement
    assert register(TestClient(app, headers=HEADERS), "reopened-user", reopened["code"]).status_code == 200


def test_restart_never_resets_the_administrators_changed_password(configured, monkeypatch):
    admin = login_admin()
    old_cookie = admin.cookies.get("pa_session")
    replacement = "administrator-changed-password-test-only"
    changed = admin.post("/api/password", json={"old_password": ADMIN_PASSWORD, "new_password": replacement})
    assert changed.status_code == 200
    assert changed.json()["user"]["is_admin"] is True
    monkeypatch.setenv("PA_WEB_ADMIN_PASSWORD", "different-bootstrap-value-test-only")
    restart()
    assert get_store().user_for_session(old_cookie) is None
    for password in (ADMIN_PASSWORD, "different-bootstrap-value-test-only"):
        assert TestClient(app, headers=HEADERS).post("/api/session", json={"username": ADMIN, "password": password}).status_code == 401
    fresh = TestClient(app, headers=HEADERS)
    assert fresh.post("/api/session", json={"username": ADMIN, "password": replacement}).status_code == 200
    assert fresh.get("/api/admin/invite").status_code == 200


def test_bootstrap_refuses_to_promote_an_existing_ordinary_account(configured, monkeypatch):
    url, key = configured
    with monkeypatch.context() as no_bootstrap:
        no_bootstrap.delenv("PA_WEB_ADMIN_USERNAME")
        no_bootstrap.delenv("PA_WEB_ADMIN_PASSWORD")
        original = Store(url, key)
        ordinary = original.register(ADMIN.upper(), PASSWORD, INVITE)
    try:
        with pytest.raises(ConfigurationError):
            Store(url, key)
        assert original.login(ADMIN, PASSWORD).public()["is_admin"] is False
        assert original.login(ADMIN, ADMIN_PASSWORD) is None
        with original.engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM web_admins")).scalar_one() == 0
            assert conn.execute(select(users.c.id).where(users.c.username == ADMIN)).scalar_one() == ordinary.id
    finally:
        original.engine.dispose()


def test_concurrent_bootstrap_creates_exactly_one_separate_administrator(configured, monkeypatch):
    url, key = configured
    # Exercise an upgrade against an existing database, with no admin yet.
    with monkeypatch.context() as no_bootstrap:
        no_bootstrap.delenv("PA_WEB_ADMIN_USERNAME")
        no_bootstrap.delenv("PA_WEB_ADMIN_PASSWORD")
        baseline = Store(url, key)
        baseline.register("existing-user", PASSWORD, INVITE)
        baseline.engine.dispose()
    start = Barrier(2)

    def initialize(_):
        start.wait(timeout=10)
        return Store(url, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        stores = list(pool.map(initialize, range(2)))
    try:
        for store in stores:
            assert store.login(ADMIN, ADMIN_PASSWORD).public()["is_admin"] is True
            assert store.login("existing-user", PASSWORD).public()["is_admin"] is False
        with stores[0].engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM web_admins")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM web_users")).scalar_one() == 2
    finally:
        for store in stores:
            store.engine.dispose()


def test_registration_rechecks_invitation_after_overlapping_rotation(configured, monkeypatch):
    admin = login_admin()
    hashing, continue_registration = Event(), Event()
    real_hasher = store_module.hasher

    class DelayedRegistrationHash:
        def hash(self, password):
            if password == PASSWORD:
                hashing.set()
                assert continue_registration.wait(10), "registration was not released"
            return real_hasher.hash(password)

        def verify(self, *args, **kwargs):
            return real_hasher.verify(*args, **kwargs)

    monkeypatch.setattr(store_module, "hasher", DelayedRegistrationHash())
    newcomer = TestClient(app, headers=HEADERS)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(register, newcomer, "overlapping-user")
        try:
            assert hashing.wait(10), "registration did not reach password hashing"
            assert admin.post("/api/admin/invite/rotate").status_code == 200
        finally:
            continue_registration.set()
        result = pending.result(timeout=10)
    assert result.status_code == 403
    assert get_store().login("overlapping-user", PASSWORD) is None
    assert "pa_session" not in result.cookies
