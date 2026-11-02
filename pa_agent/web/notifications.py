"""Optional per-account notifications, delivered before the request ends."""
import base64
import hashlib
import hmac
import json
import time

import httpx

from pa_agent.web.model import PublicTransport


def notify(settings, result):
    decision = (result.get("stage2") or {}).get("decision") or {}
    if settings.notify_on_order_only and not result.get("opportunity"):
        return
    text = "PA Agent · " + result["meta"]["symbol"] + "\n" + json.dumps(decision, ensure_ascii=False)[:3000]
    # Notification failures do not invalidate or duplicate a completed analysis.
    try:
        with httpx.Client(transport=PublicTransport(), timeout=8, trust_env=False, follow_redirects=False) as client:
            if settings.feishu_webhook:
                payload = {"msg_type": "text", "content": {"text": text}}
                if settings.feishu_secret:
                    stamp = str(int(time.time()))
                    key = f"{stamp}\n{settings.feishu_secret}".encode()
                    payload.update(timestamp=stamp, sign=base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode())
                client.post(settings.feishu_webhook, json=payload)
            if settings.pushplus_token:
                client.post("https://www.pushplus.plus/send", json={
                    "token": settings.pushplus_token, "title": "PA Agent 分析", "content": text, "template": "txt"})
    except Exception:
        pass
