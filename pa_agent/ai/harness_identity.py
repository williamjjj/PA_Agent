"""Read effective Codex model settings through its local protocol, never auth files."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


def codex_identity(executable: Path, directory: Path, *, requested_model="", timeout=12):
    from pa_agent.ai.local_harness import _spawn_external, _WindowsJob, _terminate

    directory.mkdir(parents=True, exist_ok=True)
    command = [str(executable), "app-server", "--listen", "stdio://"]
    if requested_model:
        command += ["-c", "model=" + json.dumps(requested_model)]
    process = _spawn_external(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", cwd=directory,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    job = None
    inbox = queue.Queue()
    deadline = time.monotonic() + timeout

    def read():
        for line in process.stdout:
            if len(line) > 4_000_000:
                continue
            try:
                inbox.put(json.loads(line))
            except ValueError:
                pass
        inbox.put({"eof": True})

    def request(ident, method, params):
        process.stdin.write(json.dumps({"id": ident, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        while time.monotonic() < deadline:
            event = inbox.get(timeout=max(.01, deadline - time.monotonic()))
            if event.get("eof"):
                raise RuntimeError("Codex 模型识别进程已退出")
            if event.get("id") == ident:
                if "error" in event:
                    raise RuntimeError("Codex 未提供模型配置")
                return event["result"]
        raise TimeoutError("Codex 模型识别超时")

    try:
        job = _WindowsJob(process)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        request(1, "initialize", {"clientInfo": {"name": "pa_agent", "version": "1"},
                                  "capabilities": {"experimentalApi": True}})
        process.stdin.write('{"method":"initialized"}\n')
        process.stdin.flush()
        config = request(2, "config/read", {"includeLayers": False}).get("config", {})
        models = request(3, "model/list", {"includeHidden": True}).get("data", [])
        model = requested_model or config.get("model")
        selected = next((row for row in models if row.get("model") == model), {})
        if not model:
            selected = next((row for row in models if row.get("isDefault")), {})
            model = selected.get("model")
        effort = config.get("model_reasoning_effort") or selected.get("defaultReasoningEffort")
        help_result = _spawn_external(
            [str(executable), "exec", "--help"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            help_text = help_result.communicate(timeout=3)[0]
        except subprocess.TimeoutExpired:
            help_result.kill()
            help_result.communicate()
            help_text = ""
        return {"backend": "codex", "model": model, "reasoning_effort": effort,
                "service_tiers": [row["id"] for row in selected.get("serviceTiers", [])],
                "service_tier": config.get("service_tier") or "default",
                "context_window": config.get("model_context_window"),
                "model_provider": config.get("model_provider") or "openai",
                "generation_config": {key: config[key] for key in (
                    "model_context_window", "model_auto_compact_token_limit",
                    "model_reasoning_summary", "model_verbosity", "service_tier")
                    if config.get(key) is not None},
                "resolved": bool(model), "identity_source": "codex_config_rpc",
                "isolated_config_supported": "--ignore-user-config" in help_text}
    finally:
        _terminate(process, job)
        if process.stdin:
            process.stdin.close()
        if 'reader' in locals():
            reader.join(timeout=1)
        if process.stdout:
            process.stdout.close()
