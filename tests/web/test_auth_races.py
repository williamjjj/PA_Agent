"""Credential rotation must revoke even overlapping authentication attempts."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import event

import pa_agent.web.store as store_module
from pa_agent.web.app import app
from pa_agent.web.store import Store

PASSWORD = "original-password-test-only"
NEW_PASSWORD = "replacement-password-test-only"
HEADERS = {"x-pa-request": "1"}


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("PA_WEB_INVITE_CODE", raising=False)
    monkeypatch.delenv("PA_WEB_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("PA_WEB_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("PA_WEB_REGISTRATION", "1")
    store = Store("sqlite:///" + str(tmp_path / "accounts.sqlite3"), Fernet.generate_key().decode())
    monkeypatch.setattr("pa_agent.web.app.db", lambda: store)
    with TestClient(app, headers=HEADERS) as owner:
        result = owner.post("/api/register", json={"username": "alice", "password": PASSWORD})
        assert result.status_code == 200
        public = result.json()["user"]
        assert set(public) == {"id", "username", "is_admin"}
        assert public["is_admin"] is False
        owner.headers["x-pa-user"] = public["id"]
        yield store, owner, public["id"]
    store.engine.dispose()


def test_password_change_rejects_login_already_verified_with_old_password(account, monkeypatch):
    store, owner, uid = account
    verified, continue_login = Event(), Event()
    original_login = store.login

    def delayed_login(username, password):
        current = original_login(username, password)
        verified.set()
        assert continue_login.wait(10), "test did not release the verified login"
        return current

    monkeypatch.setattr(store, "login", delayed_login)
    old_cookie = owner.cookies.get("pa_session")
    with TestClient(app, headers=HEADERS) as other, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(other.post, "/api/session", json={"username": "alice", "password": PASSWORD})
        try:
            assert verified.wait(10), "login did not reach successful password verification"
            changed = owner.post("/api/password", json={"old_password": PASSWORD, "new_password": NEW_PASSWORD})
            assert changed.status_code == 200
            assert changed.json() == {"user": {"id": uid, "username": "alice", "is_admin": False}}
            assert store.user_for_session(old_cookie) is None
        finally:
            continue_login.set()
        response = pending.result(timeout=10)
        assert response.status_code == 401
        assert "pa_session" not in response.cookies
        assert other.get("/api/settings").status_code == 401
    assert owner.get("/api/settings").status_code == 200


def test_password_change_revokes_session_issued_while_change_waits(account):
    store, _, uid = account
    verified = store.login("alice", PASSWORD)
    inserting, change_attempted, allow_insert = Event(), Event(), Event()

    def pause_insertion(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO web_sessions"):
            inserting.set()
            assert allow_insert.wait(10), "test did not release session insertion"
        elif statement.startswith("UPDATE web_users SET password=") and "password=web_users.password" not in statement:
            change_attempted.set()

    event.listen(store.engine, "before_cursor_execute", pause_insertion)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            issuance = pool.submit(store.create_session, verified)
            try:
                assert inserting.wait(10), "session did not reach insertion"
                rotation = pool.submit(store.change_password, uid, PASSWORD, NEW_PASSWORD)
                assert change_attempted.wait(10), "password change did not reach its update"
            finally:
                allow_insert.set()
            token = issuance.result(timeout=10)
            changed = rotation.result(timeout=10)
    finally:
        allow_insert.set()
        event.remove(store.engine, "before_cursor_execute", pause_insertion)
    assert token and changed
    assert store.user_for_session(token) is None
    assert store.create_session(verified) is None
    fresh = store.create_session(changed)
    assert store.user_for_session(fresh) == {"id": uid, "username": "alice", "is_admin": False}


def test_two_password_changes_cannot_both_use_same_old_password(account, monkeypatch):
    store, _, uid = account
    barrier = Barrier(2)
    real_hasher = store_module.hasher

    class SimultaneousVerification:
        def verify(self, password_hash, password):
            result = real_hasher.verify(password_hash, password)
            barrier.wait(timeout=10)
            return result

        def hash(self, password):
            return real_hasher.hash(password)

    monkeypatch.setattr(store_module, "hasher", SimultaneousVerification())
    replacements = [NEW_PASSWORD + "-first", NEW_PASSWORD + "-second"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        changed = list(pool.map(lambda value: store.change_password(uid, PASSWORD, value), replacements))
    monkeypatch.setattr(store_module, "hasher", real_hasher)
    assert sum(current is not None for current in changed) == 1
    winner = next(i for i, current in enumerate(changed) if current is not None)
    assert store.login("alice", replacements[winner]) is not None
    assert store.login("alice", replacements[1 - winner]) is None
    assert store.login("alice", PASSWORD) is None


def test_rotating_back_to_same_password_does_not_revive_verified_login(account):
    store, _, uid = account
    old_verification = store.login("alice", PASSWORD)
    assert store.change_password(uid, PASSWORD, NEW_PASSWORD)
    assert store.change_password(uid, NEW_PASSWORD, PASSWORD)
    assert store.create_session(old_verification) is None
    fresh = store.login("alice", PASSWORD)
    assert store.user_for_session(store.create_session(fresh))["id"] == uid
