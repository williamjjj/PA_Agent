"""Provider wire contracts; all requests terminate in an in-memory transport."""
import json
from datetime import datetime
from unittest.mock import patch

import httpx
import pytest

from pa_agent.ai.decision_continuity import build_continuity_context
from pa_agent.orchestrator.two_stage import _build_empty_record
from pa_agent.util.threading import CancelToken
from pa_agent.web.data import ImportRequest, demo_csv, parse_csv
from pa_agent.web.model import ModelClient, chat_parameters
from pa_agent.web.settings import WebSettings


@pytest.mark.parametrize("thinking", [True, False])
def test_deepseek_thinking_switch_and_effort(thinking):
    params = chat_parameters(WebSettings(thinking=thinking, reasoning_effort="medium"), [])
    assert params["extra_body"] == {"thinking": {"type": "enabled" if thinking else "disabled"}}
    assert params.get("reasoning_effort") == ("high" if thinking else None)
    assert params["max_tokens"] == 16000


def test_gateway_limits_and_claude_budget_respect_personal_output_limit():
    params = chat_parameters(WebSettings(base_url="https://api.kkone.vip/v1", model="claude-opus-4-5", thinking=True, max_tokens=6000), [])
    assert params["extra_body"]["thinking"]["budget_tokens"] == 5999
    assert params["max_tokens"] == 6000
    assert "reasoning_effort" not in params
    params = chat_parameters(WebSettings(base_url="https://api.b.ai/v1"), [])
    assert params["max_tokens"] == 8192


def test_openai_reasoning_models_use_completion_budget():
    params = chat_parameters(WebSettings(base_url="https://api.openai.com/v1", model="o3", thinking=True), [])
    assert params["max_completion_tokens"] == 16000
    assert "max_tokens" not in params


@pytest.mark.parametrize("finish", ["stop", "length", None])
def test_stream_requires_explicit_success_and_preserves_usage(finish):
    captured, output = [], []
    def chunk(delta, finish_reason=None):
        return {"id": "test", "object": "chat.completion.chunk", "created": 1,
                "model": "test-model", "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
    chunks = [chunk({"reasoning_content": "fixture reasoning"}), chunk({"content": "fixture answer"})]
    if finish:
        chunks.append(chunk({}, finish))
    chunks.append({"id": "test", "object": "chat.completion.chunk", "created": 1, "model": "test-model",
                   "choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})
    wire = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire.encode())
    with patch("pa_agent.web.model.PublicTransport", side_effect=lambda **kw: httpx.MockTransport(handle)), patch("tiktoken.get_encoding") as encoding:
        encoding.return_value.encode.return_value = [1]
        client = ModelClient(WebSettings(api_key="fixture-key"))
        if finish == "stop":
            reply = client.stream_chat([{"role": "user", "content": "hello"}], cancel_token=CancelToken(), on_content_token=output.append)
            assert reply.content == "fixture answer"
            assert reply.reasoning_content == "fixture reasoning"
            assert reply.usage.total_tokens == 30
            assert output == ["fixture answer"]
        else:
            with pytest.raises(ValueError, match="Token 上限" if finish else "未正常结束"):
                client.stream_chat([{"role": "user", "content": "hello"}], cancel_token=CancelToken())
    assert len(captured) == 1
    assert captured[0]["thinking"] == {"type": "disabled"}


def test_no_account_record_never_reads_shared_desktop_csv():
    frame = parse_csv(ImportRequest(csv=demo_csv()))
    with patch("pa_agent.ai.decision_continuity.load_last_trade_csv_row", side_effect=AssertionError("shared history accessed")):
        ctx = build_continuity_context(frame=frame, stage1_json={"direction": "bullish"})
    assert not ctx["has_previous_plan"]
    record = _build_empty_record(frame, None)
    timestamp = datetime.fromisoformat(record.meta.timestamp_local_iso)
    assert timestamp.tzinfo is not None
    assert abs(timestamp.timestamp() * 1000 - record.meta.timestamp_local_ms) < 1


def test_settings_reserve_context_for_input():
    with pytest.raises(ValueError):
        WebSettings(context_window=8192, max_tokens=8192)
