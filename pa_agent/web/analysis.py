from __future__ import annotations

import asyncio
import time
from pathlib import Path

import ai
from ai.models.core.params import OutputParams

from pa_agent.ai.deepseek_client import AIReply, AIUsage, CancelledError
from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.router import route_strategy_files
from pa_agent.config.settings import Settings
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator

MODEL = "deepseek/deepseek-v4.1-flash"
ROOT = Path(__file__).resolve().parents[2]


class GatewayClient:
    def __init__(self):
        self.deadline = time.monotonic() + 180

    def stream_chat(self, messages, *, cancel_token, on_content_token=None, **kwargs):
        async def generate():
            started = time.monotonic()
            converted = [ai.message(m["content"], role=m["role"]) for m in messages]
            remaining = min(85, self.deadline - started)
            if remaining <= 0:
                raise TimeoutError("分析已超时")
            async with asyncio.timeout(remaining):
                async with ai.stream(
                    ai.get_model(MODEL), converted,
                    params=ai.InferenceRequestParams(output=OutputParams(max_tokens=8000)),
                ) as stream:
                    iterator = stream.__aiter__()
                    pending = asyncio.create_task(anext(iterator))
                    try:
                        while True:
                            if cancel_token.is_set():
                                raise CancelledError("分析已取消")
                            done, _ = await asyncio.wait({pending}, timeout=0.25)
                            if not done:
                                continue
                            try:
                                event = pending.result()
                            except StopAsyncIteration:
                                break
                            if isinstance(event, ai.events.TextDelta) and on_content_token:
                                on_content_token(event.chunk)
                            pending = asyncio.create_task(anext(iterator))
                    finally:
                        if not pending.done():
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)
                text = stream.text
                usage = stream.usage
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                return AIReply(text, "", {"content": text}, AIUsage(
                    prompt_tokens=input_tokens, completion_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                ), "", (time.monotonic() - started) * 1000)
        return asyncio.run(generate())


class ResponseOnlyWriter:
    def save_partial(self, record, reason):
        return None

    def save_full(self, record):
        return None


class NoExperience:
    def read_top5(self, *args, **kwargs):
        return []


class WebOrchestrator(TwoStageOrchestrator):
    def _stream_chat_resilient(self, messages, *, stage_label, **kwargs):
        # Web requests must never fall back to a local desktop connector.
        return self._client.stream_chat(messages, **kwargs)


def analyze(frame, cancel_token, on_event, client=None):
    settings = Settings()
    settings.provider.model = MODEL
    settings.provider.base_url = "https://ai-gateway.vercel.sh"
    settings.provider.thinking = False
    settings.general.decision_stance = "conservative"
    settings.validation.retry_enabled = False
    settings.validation.retry_max = 0
    orchestrator = WebOrchestrator(
        client or GatewayClient(),
        PromptAssembler(ROOT / "prompt_engineering", prompt_settings=settings.prompt),
        route_strategy_files, JsonValidator(settings.validation),
        ResponseOnlyWriter(), NoExperience(), settings,
    )
    record = orchestrator.submit(frame, cancel_token, on_event)
    result = {
        "meta": {"symbol": frame.symbol, "timeframe": frame.timeframe, "model": MODEL},
        "stage1": record.stage1_diagnosis,
        "stage2": record.stage2_decision,
        "strategies": record.strategy_files_used,
        "usage": record.usage_total,
        "error": None,
    }
    if record.exception:
        result["error"] = "分析未通过校验或模型服务未完成响应；请稍后重试。未生成有效交易决策。"
        result["error_type"] = record.exception.get("type", "analysis_error")
    return result
