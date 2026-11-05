"""Charts, research evidence and PA records must share an owned frozen input."""
import json
import math
import time
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from pa_agent.data.base import IndicatorBundle
from pa_agent.web.app import app
from pa_agent.web.data import ImportRequest, chart_data, demo_csv, parse_csv
from pa_agent.web.snapshots import dataset_hash, same_market
from pa_agent.web.sources import SourceRequest, instrument_metadata
from pa_agent.web.store import get_store
from tests.web.test_accounts import HEADERS, clients  # noqa: F401


def freeze(client, **changes):
    data = {"csv": demo_csv(), "source": "demo", **changes}
    response = client.post("/api/snapshots", json={"data": data})
    assert response.status_code == 200, response.text
    return response.json()


def fake_analysis(captured):
    def analyze(frame, cfg, store, uid, cancel, emit, previous, count):
        captured.append((frame, uid, cfg.api_key, previous, count))
        return {"status": "failed", "meta": {}, "stage1": None, "stage2": None,
                "error": "test-only incomplete analysis", "usage": {}, "strategies": []}, {}
    return analyze


def analyze_record(client, snapshot):
    response = client.post("/api/analyze", json={"snapshot_id": snapshot["snapshot_id"]})
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    result = next(item["data"] for item in events if item["event"] == "result")
    return result, client.get("/api/records/" + result["id"]).json()


def test_factors_without_llm_key_are_private_and_explicit_about_missing_data(clients):
    alice, bob = clients
    chart = freeze(alice)
    assert not alice.get("/api/settings").json()["api_key_configured"]
    request = {"snapshot_id": chart["snapshot_id"]}
    result = alice.post("/api/quant/signals", json=request)
    assert result.status_code == 200
    evidence = result.json()
    assert evidence == chart["quant_evidence"]
    assert evidence["dataset_hash"] == dataset_hash(chart)
    assert evidence["bar_count"] == len(chart["bars"])
    assert evidence["as_of"] == chart["bars"][-1]["ts_open"] + 900_000
    assert evidence["status"] == "partial"
    assert len(evidence["factors"]) == 6
    liquidity = next(f for f in evidence["factors"] if f["id"] == "LIQ")
    assert liquidity["status"] == "unavailable" and liquidity["value"] is None
    assert all(row["value"] is None for row in liquidity["series"])
    assert "simulated_data" in [flag["code"] for flag in evidence["quality_flags"]]
    assert bob.post("/api/quant/signals", json=request).status_code == 404
    visitor = TestClient(app, headers=HEADERS)
    assert visitor.post("/api/quant/signals", json=request).status_code == 401
    assert visitor.post("/api/snapshots", json={"data": {"csv": demo_csv()}}).status_code == 401
    alice_id = alice.get("/api/status").json()["user"]["id"]
    assert bob.post("/api/quant/signals", json=request, headers={"x-pa-user": alice_id}).status_code == 409
    assert alice.post("/api/quant/signals", json={**request, "owner_id": alice_id}).status_code == 422
    assert alice.post("/api/quant/signals", json={**request, "bars": []}).status_code == 422


def test_pa_and_quant_share_frozen_input_and_saved_evidence_survives_expiry(clients, monkeypatch):
    alice, bob = clients
    chart = freeze(alice)
    evidence = chart["quant_evidence"]
    captured = []
    monkeypatch.setattr("pa_agent.web.analysis.analyze", fake_analysis(captured))
    # Even a new/broken calculation implementation must not change saved evidence.
    monkeypatch.setattr("pa_agent.web.quant.compute_factors", lambda frame: (_ for _ in ()).throw(AssertionError("unexpected recalculation")))
    assert alice.post("/api/quant/signals", json={"snapshot_id": chart["snapshot_id"]}).json() == evidence
    alice.put("/api/settings", json={"settings": {"api_key": "alice-fake-model-key"}})
    result, saved = analyze_record(alice, chart)
    assert result["quant_evidence"] == saved["result"]["quant_evidence"] == evidence
    assert result["meta"]["dataset_hash"] == chart["dataset_hash"]
    assert saved["chart"]["bars"] == chart["bars"]
    assert saved["chart"]["snapshot_id"] == chart["snapshot_id"]
    frame = captured[0][0]
    assert chart_data(frame)["bars"] == chart["bars"]
    assert frame.snapshot_ts_local_ms == chart["captured_at"]
    assert captured[0][2] == "alice-fake-model-key"
    now = time.time()
    monkeypatch.setattr("pa_agent.web.store.time.time", lambda: now + 3601)
    assert alice.post("/api/quant/signals", json={"snapshot_id": chart["snapshot_id"]}).status_code == 404
    assert alice.get("/api/records/" + result["id"]).json()["result"]["quant_evidence"] == evidence
    assert bob.get("/api/records/" + result["id"]).status_code == 404


def test_quant_failure_does_not_fabricate_values_or_block_pa(clients, monkeypatch):
    alice, _ = clients
    monkeypatch.setattr("pa_agent.web.quant.compute_factors", lambda frame: (_ for _ in ()).throw(RuntimeError("private-error-must-not-leak")))
    chart = freeze(alice)
    evidence = chart["quant_evidence"]
    assert evidence["status"] == "unavailable" and evidence["factors"] == []
    assert "private-error-must-not-leak" not in json.dumps(chart)
    assert evidence["quality_flags"][0]["code"] == "calculation_failed"
    captured = []
    monkeypatch.setattr("pa_agent.web.analysis.analyze", fake_analysis(captured))
    alice.put("/api/settings", json={"settings": {"api_key": "test-key-no-network"}})
    result, _ = analyze_record(alice, chart)
    assert captured and result["quant_evidence"] == evidence


def test_online_chart_preserves_warmed_indicators_in_pa(clients, monkeypatch):
    alice, _ = clients
    frame = parse_csv(ImportRequest(csv=demo_csv(), symbol="GC=F"))
    frame = replace(frame, bars=frame.bars[:50], indicators=IndicatorBundle(
        tuple(2100.0 + i for i in range(50)), tuple(5.5 + i / 10 for i in range(50))))
    monkeypatch.setattr("pa_agent.web.app.fetch_frame", lambda data, cfg: frame)
    response = alice.post("/api/market", json={"source": "yfinance", "symbol": "GC=F", "timeframe": "15m", "count": 50})
    assert response.status_code == 200
    chart = response.json()
    captured = []
    monkeypatch.setattr("pa_agent.web.analysis.analyze", fake_analysis(captured))
    alice.put("/api/settings", json={"settings": {"api_key": "test-key-no-network"}})
    analyze_record(alice, chart)
    assert captured[0][0].bars == frame.bars
    assert captured[0][0].indicators == frame.indicators
    assert not any(math.isnan(x) for x in captured[0][0].indicators.ema20)
    assert chart["quant_evidence"]["dataset_hash"] == dataset_hash(chart)


def test_market_identity_uses_full_address_period_source_and_adjustment():
    def chart(address):
        data = SourceRequest(source="birdeye", symbol="SAME", timeframe="15m", instrument_id=address)
        return {"source": data.source, "symbol": data.symbol, "timeframe": data.timeframe,
                "instrument": instrument_metadata(data)}
    first = chart("So11111111111111111111111111111111111111112")
    other = chart("11111111111111111111111111111111")
    assert not same_market(first, other)
    assert not same_market(first, {**first, "timeframe": "1h"})
    assert not same_market(first, {**first, "source": "csv"})
    assert not same_market(first, {**first, "adjustment": "qfq"})
    assert same_market(first, deepcopy(first))


def test_snapshot_hash_tracks_market_identity_values_and_indicators(clients):
    alice, _ = clients
    chart = freeze(alice)
    for change in ("volume", "ema"):
        changed = deepcopy(chart)
        changed["bars"][-1][change] += 0.5
        assert dataset_hash(changed) != chart["dataset_hash"]
    changed = deepcopy(chart)
    changed["instrument"]["id"] = "different-market"
    assert dataset_hash(changed) != chart["dataset_hash"]
    # User labels do not turn explicitly uploaded data into verified live data.
    uploaded = freeze(alice, source="csv")
    assert "user_supplied" in [flag["code"] for flag in uploaded["quant_evidence"]["quality_flags"]]


def test_legacy_csv_analysis_also_saves_snapshot_evidence(clients, monkeypatch):
    alice, _ = clients
    monkeypatch.setattr("pa_agent.web.analysis.analyze", fake_analysis([]))
    alice.put("/api/settings", json={"settings": {"api_key": "test-key-no-network"}})
    response = alice.post("/api/analyze", json={"data": {"csv": demo_csv()}})
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    result = next(item["data"] for item in events if item["event"] == "result")
    assert result["quant_evidence"]["snapshot_id"] == result["meta"]["snapshot_id"]
    uid = alice.get("/api/status").json()["user"]["id"]
    assert get_store().get(uid, "snapshot", result["meta"]["snapshot_id"]) is not None
