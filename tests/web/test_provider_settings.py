from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from pa_agent.web.app import app
from pa_agent.web.settings import WebSettings, merge_settings
from pa_agent.web.store import get_store


OLD_KEY = "old-provider-private-key"
NEW_KEY = "new-provider-private-key"
NEW_URL = "https://new-provider.example/v1"


@pytest.mark.parametrize("changes", [
    {"base_url": NEW_URL},
    {"base_url": NEW_URL, "api_key": ""},
])
def test_provider_origin_change_requires_explicit_key_decision(changes):
    current = WebSettings(api_key=OLD_KEY).model_dump()
    with pytest.raises(ValueError, match="重新填写 API Key") as exc:
        merge_settings(current, changes, [])
    assert OLD_KEY not in str(exc.value)
    assert current["api_key"] == OLD_KEY
    assert current["base_url"] == WebSettings().base_url


@pytest.mark.parametrize("url", [
    "https://api.deepseek.com/v2",
    "https://API.DEEPSEEK.COM:443/custom/v1/",
    "https://api.deepseek.com",
])
def test_same_origin_preserves_stored_key_for_path_changes(url):
    updated = merge_settings(
        WebSettings(api_key=OLD_KEY).model_dump(), {"base_url": url, "api_key": ""}, [])
    assert updated.api_key == OLD_KEY
    assert updated.base_url == url.rstrip("/")


@pytest.mark.parametrize("changes, clear, expected_key", [
    ({"base_url": NEW_URL, "api_key": NEW_KEY}, [], NEW_KEY),
    ({"base_url": NEW_URL}, ["api_key"], ""),
    ({"base_url": NEW_URL, "api_key": ""}, ["api_key"], ""),
])
def test_provider_origin_change_accepts_replacement_or_explicit_removal(changes, clear, expected_key):
    updated = merge_settings(WebSettings(api_key=OLD_KEY).model_dump(), changes, clear)
    assert updated.base_url == NEW_URL
    assert updated.api_key == expected_key


@pytest.mark.parametrize("current, changes, expected_key", [
    ({}, {}, ""),
    ({}, {"api_key": ""}, ""),
    ({}, {"base_url": NEW_URL, "api_key": ""}, ""),
    ({}, {"base_url": NEW_URL, "api_key": NEW_KEY}, NEW_KEY),
    ({"api_key": ""}, {"base_url": NEW_URL}, ""),
])
def test_first_configuration_and_empty_credentials_remain_valid(current, changes, expected_key):
    updated = merge_settings(current, changes, [])
    assert updated.api_key == expected_key


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PA_WEB_INVITE_CODE", raising=False)
    monkeypatch.setenv("PA_WEB_REGISTRATION", "1")
    monkeypatch.setenv("PA_WEB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PA_WEB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_store.cache_clear()
    with TestClient(app, headers={"x-pa-request": "1"}) as client:
        response = client.post("/api/register", json={
            "username": "provider-owner", "password": "strong-test-account-password"})
        assert response.status_code == 200
        uid = response.json()["user"]["id"]
        assert client.put("/api/settings", json={"settings": {"api_key": OLD_KEY}}).status_code == 200
        yield client, uid
    get_store().engine.dispose()
    get_store.cache_clear()


@pytest.mark.parametrize("method, endpoint", [("put", "/api/settings"), ("post", "/api/models")])
def test_api_rejects_cross_origin_reuse_before_network_or_persistence(account, method, endpoint):
    client, uid = account
    before = get_store().settings(uid)
    with patch("pa_agent.web.model.list_models") as discover:
        result = client.request(method, endpoint, json={
            "settings": {"base_url": NEW_URL, "api_key": ""}})
        discover.assert_not_called()
    assert result.status_code == 422
    assert OLD_KEY not in result.text
    assert get_store().settings(uid) == before
    assert OLD_KEY not in client.get("/api/settings").text


def test_model_probe_uses_explicit_new_key_without_overwriting_saved_provider(account):
    client, uid = account
    before = get_store().settings(uid)
    with patch("pa_agent.web.model.list_models", return_value=["test-model"]) as discover:
        response = client.post("/api/models", json={
            "settings": {"base_url": NEW_URL, "api_key": NEW_KEY}})
    assert response.status_code == 200
    assert discover.call_args.args[0].api_key == NEW_KEY
    assert discover.call_args.args[0].base_url == NEW_URL
    assert response.json() == {"models": ["test-model"]}
    assert OLD_KEY not in response.text and NEW_KEY not in response.text
    assert get_store().settings(uid) == before


@pytest.mark.parametrize("replacement, clear, expected_key", [
    ({"api_key": NEW_KEY}, [], NEW_KEY),
    ({}, ["api_key"], ""),
])
def test_api_saves_new_provider_with_explicit_credential_choice(account, replacement, clear, expected_key):
    client, uid = account
    response = client.put("/api/settings", json={
        "settings": {"base_url": NEW_URL, **replacement}, "clear_secrets": clear})
    assert response.status_code == 200
    assert response.json()["api_key_configured"] == bool(expected_key)
    assert OLD_KEY not in response.text and NEW_KEY not in response.text
    saved = get_store().settings(uid)
    assert saved["base_url"] == NEW_URL and saved["api_key"] == expected_key


def test_explicit_clear_on_cross_origin_probe_does_not_send_or_delete_saved_key(account):
    client, uid = account
    with patch("pa_agent.web.model.list_models") as discover:
        response = client.post("/api/models", json={
            "settings": {"base_url": NEW_URL}, "clear_secrets": ["api_key"]})
        discover.assert_not_called()
    assert response.status_code == 422
    assert OLD_KEY not in response.text
    assert get_store().settings(uid)["api_key"] == OLD_KEY
