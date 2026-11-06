import json
from pathlib import Path
import sys

import pytest

from pa_agent.ai import local_harness as h
from pa_agent.ai.deepseek_client import AIUsage


def events(usage):
    return [
        {"type":"item.completed","item":{"type":"agent_message","text":"done"}},
        {"type":"turn.completed","usage":usage},
    ]


@pytest.mark.parametrize("cached", [0, 12416, None])
def test_explicit_zero_is_distinct_from_unreported_and_audited(cached):
    raw={"input_tokens":22754,"output_tokens":70,"reasoning_output_tokens":57,
         "cache_write_input_tokens":4096}
    if cached is not None: raw["cached_input_tokens"]=cached
    audit={}
    content,usage=h._extract_result("codex","\n".join(map(json.dumps,events(raw))),usage_audit=audit)
    assert content=="done" and usage.cached_prompt_tokens==(cached or 0)
    assert usage.cache_usage_reported is (cached is not None)
    assert audit["events"][0]=={"type":"turn.completed","usage":raw}
    assert audit["cache_usage_reported"] is (cached is not None)


def test_later_partial_event_does_not_erase_completed_usage():
    raw={"input_tokens":100,"output_tokens":20,"cached_input_tokens":80}
    lines=events(raw)+[{"type":"diagnostic","usage":{"output_tokens":1},"secret":"do-not-save"}]
    audit={}
    _,usage=h._extract_result("codex",json.dumps(lines),usage_audit=audit)
    assert usage.prompt_tokens==100 and usage.cached_prompt_tokens==80
    assert len(audit["events"])==1 and "do-not-save" not in json.dumps(audit)


def test_missing_cache_field_remains_unknown_across_stages_and_followups():
    from pa_agent.orchestrator.two_stage import _accumulate_usage_calls
    from pa_agent.ai.session_ledger import SessionTokenLedger
    reported=AIUsage(100,80,20,120,cache_usage_reported=True)
    missing=AIUsage(100,0,20,120,cache_usage_reported=False)
    total=_accumulate_usage_calls({},[reported,missing,reported])
    assert total["cached_prompt_tokens"]==160 and total["cache_usage_reported"] is False
    ledger=SessionTokenLedger()
    for usage in (reported,missing,reported):ledger.add(usage)
    assert ledger.breakdown()["cache_usage_reported"] is False
    ledger.reset()
    ledger.add(AIUsage(100,0,20,120,cache_usage_reported=True))
    assert ledger.breakdown()["cache_usage_reported"] is True


def test_ui_distinguishes_zero_and_missing(qtbot):
    from pa_agent.config.settings import Settings
    from pa_agent.gui.ai_stream_window import AIStreamPanel
    panel=AIStreamPanel(); qtbot.addWidget(panel)
    panel.bind_settings(Settings(provider={"backend":"codex"}))
    panel.update_token_display({"total_cached_input":0,"cache_usage_reported":False})
    assert "缓存 未报告" in panel._token_label.text()
    panel.update_token_display({"total_cached_input":0,"cache_usage_reported":True})
    assert "缓存 0" in panel._token_label.text() and "未报告" not in panel._token_label.text()
    panel.update_token_display({"total_cached_input":80,"cache_usage_reported":False})
    assert "至少 80" in panel._token_label.text()


def test_codex_uses_stable_cwd_and_keeps_raw_counters(tmp_path,monkeypatch):
    from pa_agent.config.settings import AIProviderSettings
    import pa_agent.config.paths as paths
    script=tmp_path/'fake.py'
    script.write_text("import os,json\nprint(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':os.getcwd()}}))\nprint(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':0,'cache_write_input_tokens':80,'output_tokens':20}}))")
    monkeypatch.setattr(paths,'DATA_ROOT',tmp_path)
    monkeypatch.setattr(h,'discover_harness',lambda *_:Path(sys.executable))
    monkeypatch.setattr(h,'_command',lambda *_:[sys.executable,str(script)])
    client=h.LocalHarnessClient(AIProviderSettings(backend='codex'))
    for _ in range(2):
        reply=client.chat([{"role":"user","content":"same"}])
        assert Path(reply.content)==tmp_path/'harness-work'
        assert reply.raw['usage_audit']['events'][0]['usage']['cache_write_input_tokens']==80
        assert reply.usage.cache_usage_reported is True
        assert not list((tmp_path/'harness-work').iterdir())
