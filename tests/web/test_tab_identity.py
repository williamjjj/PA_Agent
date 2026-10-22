"""A shared browser cookie must not retarget a stale tab's private actions."""

import pytest
from fastapi.testclient import TestClient

from pa_agent.web.app import app
from pa_agent.web.store import get_store
from tests.web.test_accounts import HEADERS, clients  # noqa: F401


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/settings", None),
    ("PUT", "/api/settings", {"settings": {"api_key": "alice-unsaved-key"}}),
    ("POST", "/api/models", {"settings": {"api_key": "alice-unsaved-key"}}),
    ("POST", "/api/analyze", {"snapshot_id": "alice-snapshot"}),
    ("GET", "/api/records", None),
    ("DELETE", "/api/records/record-id", None),
    ("POST", "/api/password", {"old_password": "old-test-password", "new_password": "new-test-password"}),
    ("POST", "/api/logout", {}),
])
def test_stale_tab_cannot_act_as_new_cookie_account(clients, method, path, payload):
    alice, bob = clients
    alice_id = alice.get("/api/status").json()["user"]["id"]
    bob_id = bob.get("/api/status").json()["user"]["id"]
    bob.put("/api/settings", json={"settings": {"api_key": "bob-owned-key"}})
    # This is Alice's old page after another tab replaced the shared Cookie.
    result = bob.request(method, path, headers={"x-pa-user": alice_id},
                         **({"json": payload} if payload is not None else {}))
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "account_changed"
    assert "alice-unsaved-key" not in result.text
    assert bob.get("/api/status").json()["user"]["id"] == bob_id
    assert get_store().settings(bob_id)["api_key"] == "bob-owned-key"
    assert bob.get("/api/settings", headers={"x-pa-user": bob_id}).status_code == 200


def test_expected_user_header_does_not_authenticate(clients):
    alice, _ = clients
    alice_id = alice.get("/api/status").json()["user"]["id"]
    visitor = TestClient(app, headers={**HEADERS, "x-pa-user": alice_id})
    assert visitor.get("/api/settings").status_code == 401
