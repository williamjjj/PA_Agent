from unittest.mock import MagicMock

from pa_agent.ai.deepseek_client import CancelledError
from pa_agent.ai.json_validator import Ok, ValidationError
from pa_agent.ai.router import route_strategy_files
from pa_agent.config.settings import Settings
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken
from tests.fixtures.validators import schema_test_validator
from .conftest import VALID_STAGE1, VALID_STAGE2, make_reply


def test_retry_responses_remain_available_for_export(frame, pending_writer, assembler, exp_reader):
    client = MagicMock()
    invalid = make_reply(VALID_STAGE1)
    invalid.content = "not-json"
    invalid.raw = {"content":"not-json"}
    client.stream_chat.side_effect = [invalid, make_reply(VALID_STAGE1), make_reply(VALID_STAGE2)]
    validator = MagicMock()
    validator.validate.side_effect = [ValidationError(category="a",stage="stage1",raw_text="not-json",message="bad JSON"),
                                      Ok(VALID_STAGE1), Ok(VALID_STAGE2)]
    engine = TwoStageOrchestrator(client,assembler,route_strategy_files,validator,pending_writer,exp_reader,Settings())
    record = engine.submit(frame,CancelToken(),lambda event:None)
    assert record.exception is None
    assert [(a["stage"],a["attempt"]) for a in record.stage_attempts] == [("stage1",1),("stage1",2),("stage2",1)]
    assert record.stage_attempts[0]["content"] == "not-json"
    assert record.stage_attempts[1]["messages"] != record.stage_attempts[0]["messages"]


def test_cancel_during_stage2_saves_completed_stage1(qtbot, frame, pending_writer, assembler, exp_reader):
    from pa_agent.gui.main_window import _AnalysisWorker
    client = MagicMock()
    client.stream_chat.side_effect = [make_reply(VALID_STAGE1), CancelledError("cancelled")]
    engine = TwoStageOrchestrator(client,assembler,route_strategy_files,schema_test_validator(),pending_writer,exp_reader,Settings())
    worker = _AnalysisWorker(engine,frame,CancelToken())
    captured = []
    worker.record_ready.connect(captured.append)
    worker.run()
    assert len(captured) == 1
    record = captured[0]
    assert record.exception["type"] == "user_cancelled"
    assert record.exception["stage"] == "stage2"
    assert record.stage1_diagnosis is not None
    assert record.stage2_decision is None
    assert len(record.stage_attempts) == 1
    pending_writer.save_partial.assert_called_once()
