"""Research input contracts use synthetic candles and mocked provider HTTP."""
import copy
import json

import httpx
import pytest

from pa_agent.web import birdeye, research_data
from pa_agent.web.data import ImportRequest, parse_csv
from pa_agent.web.research_data import (
    DatasetImport,
    HistoryRequest,
    ResearchDataError,
    dataset_from_snapshot,
    fetch_history,
    parse_dataset_csv,
    research_dataset_hash,
)
from pa_agent.web.settings import WebSettings

START = 1_700_000_040
NOW = 1_800_000_030
KEY = "fake-research-key"
REAL_CLIENT = httpx.Client


def csv_text(count=600):
    rows = ["time,open,high,low,close,volume"]
    for index in range(count):
        price = 100 + index / 10
        rows.append(f"{START + index * 60},{price},{price + 2},{price - 1},{price + .5},{100 + index % 7}")
    return "\n".join(rows)


def csv_request(**changes):
    return DatasetImport(**{"csv": csv_text(), "symbol": "BTCUSD", "timeframe": "1m", **changes})


def history_request(count=600, **changes):
    return HistoryRequest(**{"instrument_id": birdeye.WRAPPED_SOL, "symbol": "SOL", "timeframe": "1m",
                             "start": START, "end": START + count * 60, **changes})


def items(req):
    params = req.url.params
    step = next(seconds for api_type, seconds in birdeye.TIMEFRAMES.values()
                if api_type == params["type"])
    return [{"unixTime": ts, "o": 100, "h": 102, "l": 99, "c": 101, "v": 20}
            for ts in range(int(params["time_from"]), int(params["time_to"]) + 1, step)]


def response(rows):
    return httpx.Response(200, json={"success": True, "data": {"items": rows}})


@pytest.fixture
def mock_http(monkeypatch):
    monkeypatch.setattr(research_data.time, "time", lambda: NOW)

    def install(handler):
        def client(**kwargs):
            assert kwargs["trust_env"] is True and kwargs["follow_redirects"] is False
            return REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
        monkeypatch.setattr(research_data.httpx, "Client", client)
    return install


@pytest.mark.parametrize("count", [300, 5000])
def test_dataset_accepts_research_size_and_preserves_normal_chart_limit(count):
    data = csv_request(csv=csv_text(count))
    chart = parse_dataset_csv(data)
    assert len(chart["bars"]) == chart["quality"]["bar_count"] == count
    assert chart["bars"][0]["ts_open"] == START * 1000
    assert all(bar["closed"] for bar in chart["bars"])
    assert chart["market_kind"] == "continuous_spot"
    assert chart["instrument"]["asset_class"] == "unspecified"
    assert chart["instrument"]["verified"] is False
    assert chart["quality"]["start"] == START
    assert chart["quality"]["end"] == START + count * 60
    assert "quant_evidence" not in chart
    json.dumps(chart, allow_nan=False)
    if count > 500:
        with pytest.raises(ValueError, match="500"):
            parse_csv(ImportRequest(csv=data.csv, symbol="BTCUSD", timeframe="1m"))


@pytest.mark.parametrize("count", [299, 5001])
def test_dataset_rejects_too_few_or_many_unique_bars(count):
    with pytest.raises(ResearchDataError, match="300|5000"):
        parse_dataset_csv(csv_request(csv=csv_text(count)))


def test_csv_exact_duplicates_deduplicate_but_conflicts_reject():
    lines = csv_text().splitlines()
    duplicated = "\n".join(lines + [lines[100], lines[300]])
    chart = parse_dataset_csv(csv_request(csv=duplicated))
    clean = parse_dataset_csv(csv_request())
    assert chart["quality"]["duplicate_count"] == 2
    assert chart["dataset_hash"] == clean["dataset_hash"]
    conflicting = lines[100].rsplit(",", 1)[0] + ",999"
    with pytest.raises(ResearchDataError, match="冲突"):
        parse_dataset_csv(csv_request(csv="\n".join([*lines, conflicting])))


def test_sorting_and_capture_time_do_not_change_dataset_hash(monkeypatch):
    clean = parse_dataset_csv(csv_request())
    lines = csv_text().splitlines()
    monkeypatch.setattr(research_data.time, "time", lambda: NOW)
    reordered = parse_dataset_csv(csv_request(csv="\n".join([lines[0], *reversed(lines[1:])])))
    assert reordered["quality"]["reordered"]
    assert reordered["dataset_hash"] == clean["dataset_hash"]
    assert reordered["captured_at"] != clean["captured_at"]
    for key, value in (("source", "demo"), ("timeframe", "5m")):
        changed = {**clean, key: value}
        assert research_dataset_hash(changed) != clean["dataset_hash"]
    changed = copy.deepcopy(clean)
    changed["bars"][0]["volume"] += 1
    assert research_dataset_hash(changed) != clean["dataset_hash"]
    changed = copy.deepcopy(clean)
    changed["instrument"]["id"] = "csv:ANOTHER"
    assert research_dataset_hash(changed) != clean["dataset_hash"]


def test_gaps_and_zero_volume_are_reported_without_filling():
    lines = csv_text().splitlines()
    del lines[100]
    lines[200] = lines[200].rsplit(",", 1)[0] + ",0"
    chart = parse_dataset_csv(csv_request(csv="\n".join(lines)))
    quality = chart["quality"]
    assert quality["bar_count"] == 599
    assert quality["gap_count"] == quality["missing_intervals"] == 1
    assert quality["zero_volume_count"] == 1
    assert quality["gaps"][0]["end"] - quality["gaps"][0]["start"] == 60
    assert sum(bar["volume"] == 0 for bar in chart["bars"]) == 1
    assert "liquidity" not in json.dumps(chart).lower()


@pytest.mark.parametrize("column, value", [(0, str(START + 1)), (1, "nan"), (1, "500"),
                                           (4, "inf"), (5, "-1"), (5, "true")])
def test_csv_rejects_invalid_values_and_unaligned_times(column, value):
    lines = csv_text().splitlines()
    row = lines[1].split(",")
    row[column] = value
    lines[1] = ",".join(row)
    with pytest.raises(ResearchDataError, match="第 2 行无效"):
        parse_dataset_csv(csv_request(csv="\n".join(lines)))


def test_csv_rejects_unclosed_row_and_duplicate_headers(monkeypatch):
    monkeypatch.setattr(research_data.time, "time", lambda: START + 599 * 60)
    with pytest.raises(ResearchDataError, match="已收盘"):
        parse_dataset_csv(csv_request())
    with pytest.raises(ResearchDataError, match="六列"):
        parse_dataset_csv(csv_request(csv=csv_text().replace("volume", "close", 1)))


def test_csv_bytes_and_unverified_metadata_are_explicit():
    with pytest.raises(ValueError, match="2 MB"):
        csv_request(csv="汉" * 700_000)
    with pytest.raises(ValueError):
        csv_request(market_kind="stock")
    with pytest.raises(ValueError):
        csv_request(source="birdeye")
    chart = parse_dataset_csv(csv_request(instrument_id=birdeye.WRAPPED_SOL))
    assert chart["instrument"]["declared_instrument_id"] == birdeye.WRAPPED_SOL
    assert chart["instrument"]["chain"] is None and chart["instrument"]["address"] is None
    demo = parse_dataset_csv(csv_request(), source="demo")
    assert demo["source"] == "demo" and demo["provenance"]["kind"] == "simulated"


@pytest.mark.parametrize("body", ["1" * 131073, "time,open,high,low,close,volume\n" + "1" * 131073])
def test_csv_field_limit_returns_safe_validation_error(body):
    with pytest.raises(ResearchDataError, match="格式无效或单个字段过长"):
        parse_dataset_csv(csv_request(csv=body))


def test_snapshot_canonicalization_preserves_frozen_identity_and_source():
    original = parse_dataset_csv(csv_request())
    original["snapshot_id"] = "original-snapshot"
    frozen = dataset_from_snapshot(original)
    assert frozen["instrument"] == original["instrument"]
    assert frozen["source"] == original["source"]
    assert frozen["captured_at"] == original["captured_at"]
    assert frozen["dataset_hash"] == original["dataset_hash"]
    assert frozen["provenance"]["original_dataset_hash"] == original["dataset_hash"]
    assert frozen["provenance"]["original_snapshot_id"] == "original-snapshot"
    with pytest.raises(ResearchDataError, match="300 至 5000"):
        dataset_from_snapshot({**original, "bars": original["bars"][:299]})
    with pytest.raises(ResearchDataError, match="当前研究仅支持"):
        dataset_from_snapshot({**original, "source": "eastmoney"})
    changed = copy.deepcopy(original)
    changed["bars"][0]["closed"] = False
    with pytest.raises(ResearchDataError, match="未收盘"):
        dataset_from_snapshot(changed)


@pytest.mark.parametrize("changes", [{"instrument_id": "z" * 44}, {"chain": "ethereum"},
                                      {"start": START + 1}, {"end": START - 60},
                                      {"start": True}, {"timeframe": "1w"}])
def test_history_rejects_invalid_identity_interval_and_types(changes):
    with pytest.raises(ValueError):
        history_request(**changes)


@pytest.mark.parametrize("count", [299, 5001])
def test_history_bounds_before_any_http(count):
    with pytest.raises(ValueError, match="300 至 5000"):
        history_request(count)


def test_history_cannot_request_forming_intervals(monkeypatch):
    monkeypatch.setattr(research_data.time, "time", lambda: START + 599 * 60 + 30)
    with pytest.raises(ValueError, match="已收盘"):
        history_request()


def test_history_5000_intervals_uses_exact_25_disjoint_windows(mock_http):
    seen = []
    def handle(req):
        seen.append(req)
        return response(list(reversed(items(req))))
    mock_http(handle)
    chart = fetch_history(history_request(5000), WebSettings(birdeye_api_key=KEY))
    assert len(seen) == 25 and chart["quality"]["bar_count"] == 5000
    assert chart["quality"]["missing_intervals"] == 0
    assert chart["instrument"]["id"] == f"solana:{birdeye.WRAPPED_SOL}"
    for index, req in enumerate(seen):
        assert str(req.url.copy_with(query=None)) == birdeye.ENDPOINT
        assert req.headers["x-api-key"] == KEY and req.headers["x-chain"] == "solana"
        assert int(req.url.params["time_from"]) == START + index * 200 * 60
        assert int(req.url.params["time_to"]) == START + (index + 1) * 200 * 60 - 1
    assert KEY not in json.dumps(chart)


def test_history_uses_only_personal_key_and_no_environment_fallback(mock_http, monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "global-key-never-used")
    seen = []
    def handle(req):
        seen.append(req.headers["x-api-key"])
        return response(items(req))
    mock_http(handle)
    with pytest.raises(birdeye.BirdeyeError, match="个人设置"):
        fetch_history(history_request(), WebSettings())
    a = fetch_history(history_request(300), WebSettings(birdeye_api_key="alice-key"))
    b = fetch_history(history_request(300), WebSettings(birdeye_api_key="bob-key"))
    assert seen == ["alice-key", "alice-key", "bob-key", "bob-key"]
    assert a["dataset_hash"] == b["dataset_hash"]


def test_history_preserves_internal_and_boundary_missing_intervals(mock_http):
    def handle(req):
        rows = items(req)
        rows.pop(5)
        return response(rows[1:-1])
    mock_http(handle)
    chart = fetch_history(history_request(), WebSettings(birdeye_api_key=KEY))
    assert chart["quality"]["bar_count"] == 591
    assert chart["quality"]["missing_intervals"] == 9
    assert chart["quality"]["requested_start"] == START


def test_requested_coverage_is_part_of_identity_even_when_actual_bars_match(mock_http):
    def handle(req):
        rows = items(req)
        return response([row for row in rows if row["unixTime"] >= START + 200 * 60])
    mock_http(handle)
    complete = fetch_history(history_request(start=START + 200 * 60), WebSettings(birdeye_api_key=KEY))
    incomplete = fetch_history(history_request(), WebSettings(birdeye_api_key=KEY))
    assert complete["bars"] == incomplete["bars"]
    assert complete["quality"]["missing_intervals"] == 0
    assert incomplete["quality"]["missing_intervals"] == 200
    assert incomplete["quality"]["gap_count"] == 1
    assert complete["dataset_hash"] != incomplete["dataset_hash"]
    frozen = dataset_from_snapshot(incomplete)
    assert frozen["dataset_hash"] == incomplete["dataset_hash"]
    assert frozen["quality"]["missing_intervals"] == 200


@pytest.mark.parametrize("mutation", [
    lambda rows: [*rows[:-1], rows[0]],
    lambda rows: [{**rows[0], "unixTime": START - 60}, *rows[1:]],
    lambda rows: [{**rows[0], "o": 500}, *rows[1:]],
    lambda rows: [{**rows[0], "v": "nan"}, *rows[1:]],
])
def test_history_rejects_bad_provider_candles(mock_http, mutation):
    mock_http(lambda req: response(mutation(items(req))))
    with pytest.raises(birdeye.BirdeyeError, match="历史 K 线"):
        fetch_history(history_request(), WebSettings(birdeye_api_key=KEY))


def test_history_retries_share_deadline_and_total_request_cap(mock_http, monkeypatch):
    seen = []
    monkeypatch.setattr(birdeye.time, "sleep", lambda delay: None)
    def handle(req):
        seen.append(req)
        return httpx.Response(429, headers={"retry-after": "0"}) if len(seen) == 1 else response(items(req))
    mock_http(handle)
    with pytest.raises(birdeye.BirdeyeError, match="25 次上限"):
        fetch_history(history_request(5000), WebSettings(birdeye_api_key=KEY))
    assert len(seen) == 25
    seen.clear()
    ticks = iter([0, 61])
    monkeypatch.setattr(research_data.time, "monotonic", lambda: next(ticks))
    with pytest.raises(birdeye.BirdeyeError, match="超时"):
        fetch_history(history_request(), WebSettings(birdeye_api_key=KEY))
    assert not seen


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_history_provider_errors_do_not_echo_secrets(mock_http, monkeypatch, status):
    monkeypatch.setattr(birdeye.time, "sleep", lambda delay: None)
    mock_http(lambda req: httpx.Response(status, text=KEY))
    with pytest.raises(birdeye.BirdeyeError) as error:
        fetch_history(history_request(), WebSettings(birdeye_api_key=KEY))
    assert KEY not in str(error.value)
