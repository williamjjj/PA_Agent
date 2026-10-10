"""Request-scoped, bounded OpenAI-compatible streaming client."""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import asdict

import httpx
from openai import OpenAI

from pa_agent.ai.deepseek_client import AIReply, AIUsage, CancelledError


class PublicTransport(httpx.HTTPTransport):
    """Validate DNS on every connection, then pin the resolved public IP.

    Keeping the original Host and TLS SNI prevents DNS rebinding between validation
    and connection. Redirects and environment proxies are disabled by the client.
    """
    def handle_request(self, request):
        host = request.url.host
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(row[4][0] for row in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError("模型地址必须解析为公共网络地址。")
        original = request.url
        request.headers["Host"] = host
        request.extensions["sni_hostname"] = host
        request.url = request.url.copy_with(host=ips[0])
        try:
            return super().handle_request(request)
        finally:
            request.url = original


class ModelClient:
    def __init__(self, settings):
        self.settings = settings
        self.deadline = time.monotonic() + 170

    def stream_chat(self, messages, *, cancel_token, on_content_token=None,
                    on_reasoning_token=None, thinking=None, reasoning_effort=None, **kwargs):
        cfg = self.settings
        if not cfg.api_key:
            raise ValueError("请先在个人设置中保存模型 API Key。")
        # Conservatively bound input before sending a potentially expensive request.
        import tiktoken
        encoder = tiktoken.get_encoding("cl100k_base")
        input_size = sum(len(encoder.encode(str(m.get("content", "")), disallowed_special=()))
                         + 8 for m in messages)
        if input_size + cfg.max_tokens > cfg.context_window:
            raise ValueError("上下文超过模型窗口，请减少 K 线、提示词或历史追问。")
        started = time.monotonic()
        remaining = self.deadline - started
        if remaining < 1:
            raise TimeoutError("分析超过请求时间限制。")
        if cancel_token.is_set():
            raise CancelledError("已取消")
        content, reasoning, usage, request_id = [], [], AIUsage(), ""
        params = {"model": cfg.model, "messages": messages, "stream": True,
                  "stream_options": {"include_usage": True}, "max_tokens": cfg.max_tokens}
        if thinking if thinking is not None else cfg.thinking:
            if "deepseek" in cfg.base_url:
                params["extra_body"] = {"thinking": {"type": "enabled"}}
            else:
                params["reasoning_effort"] = reasoning_effort or cfg.reasoning_effort
        transport = PublicTransport(retries=0)
        with httpx.Client(transport=transport, follow_redirects=False, trust_env=False,
                          timeout=httpx.Timeout(min(45, remaining), connect=10)) as http:
            with OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, max_retries=0, http_client=http) as client:
                with client.chat.completions.create(**params) as stream:
                    for chunk in stream:
                        if cancel_token.is_set():
                            raise CancelledError("已取消")
                        if time.monotonic() >= self.deadline:
                            raise TimeoutError("分析超过请求时间限制。")
                        request_id = chunk.id or request_id
                        if chunk.usage:
                            u = chunk.usage
                            details = getattr(u, "prompt_tokens_details", None)
                            cached = getattr(details, "cached_tokens", 0) or getattr(u, "prompt_cache_hit_tokens", 0) or 0
                            usage = AIUsage(u.prompt_tokens or 0, cached, u.completion_tokens or 0, u.total_tokens or 0)
                        for choice in chunk.choices:
                            delta = choice.delta
                            text = delta.content or ""
                            thought = getattr(delta, "reasoning_content", "") or ""
                            if text:
                                content.append(text)
                                if on_content_token:
                                    on_content_token(text)
                            if thought:
                                reasoning.append(thought)
                                if on_reasoning_token:
                                    on_reasoning_token(thought)
        text, thought = "".join(content), "".join(reasoning)
        return AIReply(text, thought, {"content": text, "reasoning_content": thought,
                       "usage": asdict(usage)}, usage, request_id, (time.monotonic()-started)*1000)
