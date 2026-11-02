import json
from pathlib import Path
import sys
import threading
import time

import pytest

from pa_agent.ai import local_harness as h
from pa_agent.ai.deepseek_client import CancelledError
from pa_agent.config.settings import AIProviderSettings, Settings, provider_api_key_configured
from pa_agent.util.threading import CancelToken


def fake_client(tmp_path, monkeypatch, code):
    script = tmp_path / "fake.py"
    script.write_text(code, encoding="utf-8")
    monkeypatch.setattr(h, "discover_harness", lambda *_: Path(sys.executable))
    monkeypatch.setattr(h, "_command", lambda *_: [sys.executable, str(script)])
    import pa_agent.config.paths as paths
    monkeypatch.setattr(paths, "DATA_ROOT", tmp_path)
    return h.LocalHarnessClient(AIProviderSettings(backend="codex"))


def test_stdin_preserves_arbitrary_prompt_and_cleans_spool(tmp_path, monkeypatch):
    client = fake_client(tmp_path,monkeypatch,
        "import sys,json\ntext=sys.stdin.read()\nprint(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':text}}))")
    text = '中文\n$(whoami) `x` %PATH% & | "quotes"'
    reply = client.chat([{"role":"user","content":text}])
    assert json.loads(reply.content.split("Conversation (JSON):\n")[1])[0]["content"] == text
    assert not list((tmp_path / "harness-work").iterdir())


def test_timeout_terminates_child_and_is_bounded(tmp_path,monkeypatch):
    client=fake_client(tmp_path,monkeypatch,"import time\ntime.sleep(60)")
    start=time.monotonic()
    with pytest.raises(TimeoutError): client.chat([],timeout_s=.3)
    assert time.monotonic()-start < 5
    assert not list((tmp_path/"harness-work").iterdir())


def test_cancel_terminates_child(tmp_path,monkeypatch):
    client=fake_client(tmp_path,monkeypatch,"import time\ntime.sleep(60)")
    cancel=CancelToken()
    timer=threading.Timer(.3,cancel.set); timer.start()
    try:
        with pytest.raises(CancelledError): client.chat([],cancel_token=cancel)
    finally: timer.join()


def test_parent_exit_cleans_descendant_inherited_file_handles(tmp_path, monkeypatch):
    client = fake_client(tmp_path, monkeypatch,
        "import subprocess,sys,json\n"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr)\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'done'}}),flush=True)\n")
    assert client.chat([]).content == "done"
    assert not list((tmp_path / 'harness-work').iterdir())


def test_nonzero_exit_never_echoes_secret_stderr(tmp_path,monkeypatch):
    client=fake_client(tmp_path,monkeypatch,"import sys\nsys.stderr.write('SECRET-TEST-VALUE')\nsys.exit(4)")
    with pytest.raises(RuntimeError) as err: client.chat([])
    assert "SECRET-TEST-VALUE" not in str(err.value)


@pytest.mark.parametrize("backend",["codex","workbuddy","hermes"])
def test_harness_needs_executable_but_no_api_key(monkeypatch,backend):
    settings=Settings(provider={"backend":backend,"api_key":""})
    monkeypatch.setattr(h,"discover_harness",lambda *_:Path("agent.exe"))
    assert provider_api_key_configured(settings)
    monkeypatch.setattr(h,"discover_harness",lambda *_:None)
    assert not provider_api_key_configured(settings)


def test_codex_error_after_message_not_misreported_as_success():
    with pytest.raises(RuntimeError):
        h._extract_result("codex",'\n'.join(map(json.dumps,[
            {"type":"item.completed","item":{"type":"agent_message","text":"partial"}},
            {"type":"turn.failed","error":{"message":"failed"}}])))


def test_workbuddy_result_usage():
    text,usage=h._extract_result("workbuddy",json.dumps({"type":"result","result":"done",
                         "usage":{"input_tokens":20,"output_tokens":7}}))
    assert text=="done" and usage.total_tokens==27


def test_workbuddy_current_cli_json_array():
    text,usage=h._extract_result("workbuddy",json.dumps([
        {"type":"reasoning","content":"not a final answer"},
        {"type":"result","subtype":"success","is_error":False,"result":'{"ok":true}',
         "usage":{"input_tokens":30,"output_tokens":4}}]))
    assert json.loads(text)=={"ok":True}
    assert usage.total_tokens==34
