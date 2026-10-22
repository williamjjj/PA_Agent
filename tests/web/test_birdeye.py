"""Birdeye contract tests use fake HTTP and never spend a user's API quota."""
import math

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from pa_agent.web import birdeye
from pa_agent.web.app import app
from pa_agent.web.data import chart_data
from pa_agent.web.settings import WebSettings
from pa_agent.web.sources import (
    SourceRequest,
    catalog,
    fetch_frame,
    instrument_metadata,
    restore_frame,
)
from pa_agent.web.store import get_store, users

NOW = 1_800_000_030
KEY = "fake-birdeye-key-only"
HEADERS = {"x-pa-request": "1"}
REAL_CLIENT = httpx.Client


def request(**changes):
    return SourceRequest(**{"source": "birdeye", "symbol": "SOL", "timeframe": "1m",
                            "count": 50, "instrument_id": birdeye.WRAPPED_SOL, **changes})


def candles(req):
    params = req.url.params
    step = next(seconds for api_type, seconds in birdeye.TIMEFRAMES.values()
                if api_type == params["type"])
    return [{"unixTime": ts, "o": 100, "h": 102, "l": 99, "c": 101, "v": 20}
            for ts in range(int(params["time_from"]), int(params["time_to"]) + 1, step)]


def response(items):
    return httpx.Response(200, json={"success": True, "data": {"items": items}})


@pytest.fixture
def mock_http(monkeypatch):
    monkeypatch.setattr(birdeye.time, "time", lambda: NOW)

    def install(handler):
        def client(**kwargs):
            assert kwargs["trust_env"] is True
            assert kwargs["follow_redirects"] is False
            return REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
        monkeypatch.setattr(birdeye.httpx, "Client", client)
    return install


@pytest.mark.parametrize("address", ["SOL", "0" * 44, "1" * 31, "1" * 33,
                                     "z" * 44, "So11111111111111111111111111111111111111112/"])
def test_mint_must_decode_to_32_base58_bytes(address):
    with pytest.raises(ValueError):
        request(instrument_id=address)


def test_mint_is_separate_from_display_label_and_other_provider_symbol():
    a, b = request(), request(symbol="MY_SOL_LABEL")
    assert instrument_metadata(a)["id"] == instrument_metadata(b)["id"]
    assert instrument_metadata(a)["address"] == birdeye.WRAPPED_SOL
    assert instrument_metadata(a)["volume_unit"] == "provider_reported"
    assert SourceRequest(source="yfinance", symbol="GC=F", timeframe="1h").instrument_id == ""
    with pytest.raises(ValueError):
        request(source="yfinance")
    with pytest.raises(ValueError):
        request(chain="ethereum")
    assert next(s for s in catalog() if s["id"] == "birdeye")["instrument_id"] == birdeye.WRAPPED_SOL


def test_missing_personal_key_does_not_use_environment(monkeypatch, mock_http):
    monkeypatch.setenv("BIRDEYE_API_KEY", "environment-key-must-not-be-used")
    mock_http(lambda req: pytest.fail("Missing personal key must fail before HTTP"))
    with pytest.raises(ValueError, match="个人设置"):
        fetch_frame(request(), WebSettings())


@pytest.mark.parametrize("value", ["key with space", "key\r\nx-evil: yes", "密钥"])
def test_key_cannot_inject_http_headers(value):
    with pytest.raises(ValueError):
        WebSettings(birdeye_api_key=value)


def test_fetch_is_bounded_sorted_closed_and_uses_fixed_endpoint(mock_http):
    seen = []
    def handle(req):
        seen.append(req)
        return response(list(reversed(candles(req))))
    mock_http(handle)
    frame = fetch_frame(request(count=500), WebSettings(birdeye_api_key=KEY))
    assert len(seen) == 3
    ranges = [(int(r.url.params["time_from"]), int(r.url.params["time_to"])) for r in seen]
    assert sum((end + 1 - start) // 60 for start, end in ranges) == 560
    assert max((end + 1 - start) // 60 for start, end in ranges) <= 200
    assert ranges[1][1] + 1 == ranges[0][0] and ranges[2][1] + 1 == ranges[1][0]
    for req in seen:
        assert str(req.url.copy_with(query=None)) == birdeye.ENDPOINT
        assert req.headers["x-api-key"] == KEY and req.headers["x-chain"] == "solana"
        assert req.url.params["address"] == birdeye.WRAPPED_SOL
    assert len(frame.bars) == 500
    assert all(bar.closed and bar.ts_open + 60_000 <= NOW * 1000 for bar in frame.bars)
    assert [bar.seq for bar in frame.bars] == list(range(1, 501))
    assert [bar.ts_open for bar in frame.bars] == sorted(
        {bar.ts_open for bar in frame.bars}, reverse=True)


@pytest.mark.parametrize("timeframe, expected", [("1h", "1H"), ("4h", "4H"), ("1d", "1D")])
def test_birdeye_timeframe_mapping(mock_http, timeframe, expected):
    seen = []
    def handle(req):
        seen.append(req.url.params["type"])
        return response(candles(req))
    mock_http(handle)
    fetch_frame(request(timeframe=timeframe), WebSettings(birdeye_api_key=KEY))
    assert seen == [expected]


def test_forming_bar_is_excluded_and_gaps_are_not_filled(mock_http):
    def handle(req):
        items = candles(req)
        items.pop(-20)
        items.append({**items[-1], "unixTime": NOW // 60 * 60})
        return response(items)
    mock_http(handle)
    frame = fetch_frame(request(), WebSettings(birdeye_api_key=KEY))
    assert len(frame.bars) == 50
    assert max(bar.ts_open for bar in frame.bars) == (NOW // 60 * 60 - 60) * 1000
    intervals = [a.ts_open - b.ts_open for a, b in zip(frame.bars, frame.bars[1:])]
    assert intervals.count(120_000) == 1


@pytest.mark.parametrize("mutation", [
    lambda xs: xs + [xs[0]],
    lambda xs: [{**xs[0], "o": 200}, *xs[1:]],
    lambda xs: [{**xs[0], "v": -1}, *xs[1:]],
    lambda xs: [{**xs[0], "c": "NaN"}, *xs[1:]],
    lambda xs: [{**xs[0], "unixTime": xs[0]["unixTime"] + 1}, *xs[1:]],
    lambda xs: [{**xs[0], "unixTime": xs[0]["unixTime"] - 60}, *xs[1:]],
    lambda xs: [{**xs[0], "v": True}, *xs[1:]],
    lambda xs: [None, *xs[1:]],
])
def test_invalid_ohlcv_rejected_before_normalization(mock_http, mutation):
    mock_http(lambda req: response(mutation(candles(req))))
    with pytest.raises(ValueError, match="无效或不完整"):
        fetch_frame(request(), WebSettings(birdeye_api_key=KEY))


def test_insufficient_history_and_explicit_truncation_fail(mock_http):
    mock_http(lambda req: response(candles(req)[-49:]))
    with pytest.raises(ValueError, match="49 根"):
        fetch_frame(request(), WebSettings(birdeye_api_key=KEY))
    mock_http(lambda req: httpx.Response(200, json={
        "success": True, "data": {"items": candles(req), "has_more": True}}))
    with pytest.raises(ValueError, match="无效或不完整"):
        fetch_frame(request(), WebSettings(birdeye_api_key=KEY))


@pytest.mark.parametrize("status", [301, 401, 403, 500])
def test_provider_error_body_never_exposes_key(mock_http, status):
    mock_http(lambda req: httpx.Response(status, text=KEY, headers={"location": "https://evil.example"}))
    with pytest.raises(ValueError) as exc:
        fetch_frame(request(), WebSettings(birdeye_api_key=KEY))
    assert KEY not in str(exc.value)


@pytest.mark.parametrize("payload", [{"success": False, "message": KEY},
                                      {"success": True, "data": {"items": KEY}}, [KEY]])
def test_invalid_success_responses_are_sanitized(mock_http, payload):
    mock_http(lambda req: httpx.Response(200, json=payload))
    with pytest.raises(ValueError) as exc:
        fetch_frame(request(), WebSettings(birdeye_api_key=KEY))
    assert KEY not in str(exc.value)


def test_rate_limit_retries_at_most_three_with_bounded_delay(mock_http, monkeypatch):
    seen, delays = [], []
    def handle(req):
        seen.append(req)
        return httpx.Response(429, text=KEY, headers={"retry-after": "99999"})
    mock_http(handle)
    monkeypatch.setattr(birdeye.time, "sleep", delays.append)
    with pytest.raises(ValueError, match="次数受限") as exc:
        fetch_frame(request(), WebSettings(birdeye_api_key=KEY))
    assert KEY not in str(exc.value)
    assert len(seen) == 3 and delays == [2, 2]


def test_rate_limit_can_recover_without_duplicate_rows(mock_http, monkeypatch):
    seen = []
    def handle(req):
        seen.append(req)
        return httpx.Response(429) if len(seen) == 1 else response(candles(req))
    mock_http(handle)
    monkeypatch.setattr(birdeye.time, "sleep", lambda delay: None)
    frame = fetch_frame(request(), WebSettings(birdeye_api_key=KEY))
    assert len(seen) == 2 and len(frame.bars) == 50


def test_network_error_does_not_echo_headers(mock_http):
    def handle(req):
        raise httpx.ConnectError(KEY, request=req)
    mock_http(handle)
    with pytest.raises(ValueError, match="无法连接") as exc:
        fetch_frame(request(), WebSettings(birdeye_api_key=KEY))
    assert KEY not in str(exc.value)


def test_restore_uses_saved_warmup_indicators(mock_http):
    mock_http(lambda req: response(candles(req)))
    frame = fetch_frame(request(), WebSettings(birdeye_api_key=KEY))
    chart = {**chart_data(frame), "captured_at": 1_700_000_000_000}
    restored = restore_frame(chart)
    assert restored.bars == frame.bars
    assert math.isfinite(frame.indicators.ema20[-1])
    assert math.isfinite(frame.indicators.atr14[-1])
    assert restored.indicators == frame.indicators
    assert restored.snapshot_ts_local_ms == chart["captured_at"]


def test_restore_null_indicators_and_legacy_snapshot_fallback(mock_http):
    mock_http(lambda req: response(candles(req)))
    frame = fetch_frame(request(), WebSettings(birdeye_api_key=KEY))
    chart = chart_data(frame)
    chart["bars"][0]["ema"] = chart["bars"][0]["atr"] = None
    restored = restore_frame(chart)
    assert math.isnan(restored.indicators.ema20[-1])
    assert math.isnan(restored.indicators.atr14[-1])
    for bar in chart["bars"]:
        del bar["ema"], bar["atr"]
    legacy = restore_frame(chart)
    assert legacy.bars == frame.bars
    assert math.isnan(legacy.indicators.ema20[-1])
    assert math.isfinite(legacy.indicators.ema20[0])


def test_two_accounts_encrypt_mask_and_use_only_their_own_birdeye_key(tmp_path, monkeypatch, mock_http):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PA_WEB_INVITE_CODE", raising=False)
    monkeypatch.setenv("PA_WEB_REGISTRATION", "1")
    monkeypatch.setenv("PA_WEB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PA_WEB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_store.cache_clear()
    clients = [TestClient(app, headers=HEADERS), TestClient(app, headers=HEADERS)]
    try:
        for client, name in zip(clients, ("alice", "bob")):
            assert client.post("/api/register", json={"username": name, "password": "correct-horse-test-only"}).status_code == 200
            secret = f"{name}-birdeye-key"
            result = client.put("/api/settings", json={"settings": {"birdeye_api_key": secret}})
            assert result.status_code == 200 and secret not in result.text
            public = client.get("/api/settings").json()
            assert public["birdeye_api_key_configured"] and "birdeye_api_key" not in public
            uid = client.get("/api/status").json()["user"]["id"]
            with get_store().engine.connect() as conn:
                encrypted = conn.execute(select(users.c.settings).where(users.c.id == uid)).scalar_one()
            assert secret not in encrypted
        seen = []
        def handle(req):
            seen.append(req.headers["x-api-key"])
            return response(candles(req))
        mock_http(handle)
        for client in clients:
            result = client.post("/api/market", json=request().model_dump())
            assert result.status_code == 200, result.text
            assert "birdeye-key" not in result.text
        assert seen == ["alice-birdeye-key", "bob-birdeye-key"]
        assert clients[0].put("/api/settings", json={"settings": {"birdeye_api_key": ""}}).status_code == 200
        assert clients[0].get("/api/settings").json()["birdeye_api_key_configured"]
        result = clients[0].put("/api/settings", json={"settings": {}, "clear_secrets": ["birdeye_api_key"]})
        assert result.status_code == 200
        assert not clients[0].get("/api/settings").json()["birdeye_api_key_configured"]
        assert clients[1].get("/api/settings").json()["birdeye_api_key_configured"]
        result = clients[0].post("/api/market", json=request().model_dump())
        assert result.status_code == 422
        assert "请在个人设置中填写 Birdeye API Key" in result.json()["detail"]
        monkeypatch.setattr(birdeye.time, "sleep", lambda delay: None)
        for status, message in ((401, "认证或套餐权限不足"), (403, "认证或套餐权限不足"),
                                (429, "请求次数受限")):
            mock_http(lambda req, status=status: httpx.Response(status, text="bob-birdeye-key"))
            result = clients[1].post("/api/market", json=request().model_dump())
            assert result.status_code == 422
            assert message in result.json()["detail"]
            assert "bob-birdeye-key" not in result.text
    finally:
        get_store().engine.dispose()
        get_store.cache_clear()
