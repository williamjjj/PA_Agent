import json
from unittest.mock import MagicMock

from pa_agent.ai.router import route_strategy_files
from pa_agent.config.settings import Settings
from pa_agent.data.snapshot import take_snapshot_from_bars
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken
from tests.e2e.test_smoke_happy_path import _make_reply
from tests.fixtures.ai_payloads import VALID_STAGE1, VALID_STAGE2_ORDER
from tests.fixtures.kline_bars import make_newest_first_bars
from tests.fixtures.validators import schema_test_validator


def test_selected_information_reaches_model_and_saved_record():
    frame = take_snapshot_from_bars(
        make_newest_first_bars(75, with_forming=True), 20, "BTCUSDT", "1h"
    )
    client = MagicMock()
    captured = []

    def reply(messages, **kwargs):
        captured.append(messages)
        return _make_reply(VALID_STAGE1 if len(captured) == 1 else VALID_STAGE2_ORDER)

    client.stream_chat.side_effect = reply
    assembler = MagicMock()
    assembler.build_stage1.return_value = [
        {"role": "system", "content": "s1"},
        {"role": "user", "content": "Klines"},
    ]
    assembler.build_stage2_continuation.return_value = [
        {"role": "system", "content": "s2"},
        {"role": "user", "content": "Decide"},
    ]
    writer = MagicMock()
    reader = MagicMock()
    reader.read_top5.return_value = []
    evidence = [
        {
            "id": "news",
            "symbol": "BTCUSDT",
            "source": "测试资讯",
            "query": "市场事件",
            "captured_at": "2026-11-05T00:00:00Z",
            "content": "模拟事件，附来源链接",
            "truncated": False,
        }
    ]
    orchestrator = TwoStageOrchestrator(
        client,
        assembler,
        route_strategy_files,
        schema_test_validator(),
        writer,
        reader,
        Settings(provider={"api_key": "test-only"}),
        information_context=evidence,
    )
    record = orchestrator.submit(frame, CancelToken(), lambda event: None)
    assert record.exception is None
    assert len(captured) == 2
    for messages in captured:
        assert "测试资讯" in json.dumps(messages, ensure_ascii=False)
        assert "不具备指令权限" in json.dumps(messages, ensure_ascii=False)
    assert writer.save_full.call_args[0][0].information_context == evidence
