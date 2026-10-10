"""Web adapter for the original validated two-stage analysis pipeline."""
from __future__ import annotations

import json
from dataclasses import asdict

from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.free_chat import FreeChatSession
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.records.schema import AnalysisRecord, ExperienceEntry
from pa_agent.web.model import ModelClient
from pa_agent.web.settings import PROMPTS, SECRET_FIELDS


class MemoryWriter:
    def save_partial(self, record, reason):
        pass

    def save_full(self, record):
        pass


class AccountExperience:
    def __init__(self, store, uid):
        self.entries = store.list(uid, "experience", limit=500)

    def read_top5(self, cycle_position):
        matches = []
        for row in self.entries:
            data = row["payload"]
            if data["cycle_position"] == cycle_position:
                matches.append(ExperienceEntry(
                    filename=row["id"], case_type=data["case_type"], cycle_position=cycle_position,
                    timestamp_ms=int(row["created"]*1000), content=data["content"]))
        return matches[:5]


class AccountPrompts(PromptAssembler):
    def __init__(self, settings):
        super().__init__(PROMPTS, prompt_settings=settings.prompt)
        self.overrides = settings.prompt_overrides

    def _get_shared_system_prompt(self):
        # The desktop cache is keyed only by directory. Never share it between tenants.
        return self._build_shared_system_prompt_inner()

    def _load(self, filename):
        return self.overrides[filename] if filename in self.overrides else super()._load(filename)


class WebOrchestrator(TwoStageOrchestrator):
    def _stream_chat_resilient(self, messages, *, stage_label, **kwargs):
        return self._client.stream_chat(messages, **kwargs)


def redact(value, settings):
    text = json.dumps(value, ensure_ascii=False)
    for field in SECRET_FIELDS:
        secret = getattr(settings, field)
        if secret and len(secret) >= 6:
            text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[redacted]")
    return json.loads(text)


def analyze(frame, settings, store, uid, cancel, emit, previous=None, new_count=None, client=None):
    core = settings.core()
    client = client or ModelClient(settings)
    pipeline = WebOrchestrator(
        client, AccountPrompts(settings), route_strategy_files, JsonValidator(core.validation),
        MemoryWriter(), AccountExperience(store, uid), core)
    def tokens(stage, kind):
        return lambda text: emit({"event": "token", "stage": stage, "kind": kind, "text": text})
    record = pipeline.submit(
        frame, cancel, lambda ev: emit({"event": "stage", "stage": ev.name}),
        on_stage1_content=tokens("stage1", "content"),
        on_stage1_reasoning=tokens("stage1", "reasoning"),
        on_stage2_content=tokens("stage2", "content"),
        on_stage2_reasoning=tokens("stage2", "reasoning"),
        previous_record=AnalysisRecord.model_validate(previous) if previous else None,
        incremental_new_bar_count=new_count,
    )
    raw = record.model_dump(mode="json")
    raw["meta"]["ai_provider"] = {"model": settings.model, "base_url": settings.base_url}
    raw = redact(raw, settings)
    complete = bool(record.stage1_diagnosis and record.stage2_decision and not record.exception and not cancel.is_set())
    error = None if complete else (
        "分析已取消，保留已完成的阶段。" if cancel.is_set() else
        "分析未完成或未通过校验，未生成有效决策。可在记录中查看具体校验结果。")
    result = {
        "meta": {"symbol": frame.symbol, "timeframe": frame.timeframe, "model": settings.model,
                 "incremental_bars": new_count or 0},
        "status": "complete" if complete else "failed",
        "stage1": raw["stage1_diagnosis"], "stage2": raw["stage2_decision"],
        "strategies": raw["strategy_files_used"], "usage": raw["usage_total"], "error": error,
    }
    from pa_agent.web.presentation.chart_decision_overlay import enrich_decision_for_chart_overlay
    from pa_agent.web.presentation.order_opportunity import has_order_opportunity
    decision = (record.stage2_decision or {}).get("decision") or {}
    result["opportunity"] = complete and has_order_opportunity(
        decision, confidence_threshold=settings.decision_confidence_threshold)
    result["chart_decision"] = enrich_decision_for_chart_overlay(
        decision, stage2_full=record.stage2_decision, frame=frame,
        stage1_json=record.stage1_diagnosis,
        previous_record=AnalysisRecord.model_validate(previous) if previous else None,
        cooldown_bars=settings.structure_flip_cooldown_bars)
    return result, raw


def followup(payload, question, settings, cancel, emit, client=None):
    if payload["result"]["status"] != "complete":
        raise ValueError("请先完成有效分析，再继续追问。")
    record = AnalysisRecord.model_validate(payload["record"])
    messages = FreeChatSession._build_prefix(record)
    for turn in payload.get("chat", [])[-20:]:
        messages.extend([{"role": "user", "content": turn["question"]},
                         {"role": "assistant", "content": turn["content"]}])
    messages.append({"role": "user", "content": question})
    reply = (client or ModelClient(settings)).stream_chat(
        messages, cancel_token=cancel,
        on_content_token=lambda text: emit({"event": "token", "stage": "chat", "kind": "content", "text": text}),
        on_reasoning_token=lambda text: emit({"event": "token", "stage": "chat", "kind": "reasoning", "text": text}),
    )
    if cancel.is_set():
        raise ValueError("追问已取消。")
    return redact({"question": question, "content": reply.content,
                   "reasoning": reply.reasoning_content, "usage": asdict(reply.usage)}, settings)
