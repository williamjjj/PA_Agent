import socket
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest

from pa_agent.data.kline_adjust import get_kline_adjust, set_kline_adjust
from pa_agent.util.threading import CancelToken
from pa_agent.web.analysis import AccountPrompts
from pa_agent.web.data import ImportRequest, chart_data, demo_csv, parse_csv
from pa_agent.web.model import PublicTransport
from pa_agent.web.settings import WebSettings
from pa_agent.web.sources import incremental_count, restore_frame


def test_csv_roundtrip_yahoo_symbol_and_duplicate_headers():
    request = ImportRequest(csv=demo_csv(), symbol="GC=F")
    frame = parse_csv(request)
    assert restore_frame(chart_data(frame)).bars == frame.bars
    duplicate = demo_csv().replace("time,open,high,low,close,volume", "time,open,high,low,close,volume,volume")
    with pytest.raises(ValueError):
        parse_csv(ImportRequest(csv=duplicate))
    extra = demo_csv().splitlines()
    extra[1] += ",extra"
    with pytest.raises(ValueError):
        parse_csv(ImportRequest(csv="\n".join(extra)))


@pytest.mark.parametrize("bad", ["nan", "inf", "-1"])
def test_invalid_price_rejected(bad):
    rows = demo_csv().splitlines()
    cells = rows[10].split(",")
    cells[1] = bad
    rows[10] = ",".join(cells)
    with pytest.raises(ValueError):
        parse_csv(ImportRequest(csv="\n".join(rows)))


def test_incremental_requires_unchanged_overlap():
    frame = parse_csv(ImportRequest(csv=demo_csv()))
    before = chart_data(replace(frame, bars=frame.bars[1:]))
    assert incremental_count(frame, before, 10) == 1
    before["bars"][-1]["close"] += 1
    assert incremental_count(frame, before, 10) is None


def test_prompt_cache_does_not_cross_accounts():
    name = "提示词大纲_人设与思维方式.txt"
    a = AccountPrompts(WebSettings(prompt_overrides={name: "ALICE_ONLY_PROMPT"}))
    b = AccountPrompts(WebSettings(prompt_overrides={name: "BOB_ONLY_PROMPT"}))
    assert "ALICE_ONLY_PROMPT" in a._get_shared_system_prompt()
    assert "ALICE_ONLY_PROMPT" not in b._get_shared_system_prompt()
    assert "BOB_ONLY_PROMPT" in b._get_shared_system_prompt()


def test_adjustment_is_context_local():
    from contextvars import copy_context
    set_kline_adjust("qfq")
    other = copy_context()
    other.run(set_kline_adjust, "hfq")
    assert get_kline_adjust() == "qfq"
    assert other.run(get_kline_adjust) == "hfq"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"])
def test_model_blocks_private_dns(ip):
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]):
        with httpx.Client(transport=PublicTransport()) as client:
            with pytest.raises(ValueError):
                client.get("https://public-looking.example/v1")


def test_model_pins_public_address_with_original_sni():
    captured = []
    def send(self, request):
        captured.append((str(request.url), request.headers["host"], request.extensions["sni_hostname"]))
        return httpx.Response(200, content=b"ok")
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]), patch.object(httpx.HTTPTransport, "handle_request", send):
        with httpx.Client(transport=PublicTransport()) as client:
            assert client.get("https://api.example/v1").status_code == 200
    assert captured == [("https://1.1.1.1/v1", "api.example", "api.example")]
