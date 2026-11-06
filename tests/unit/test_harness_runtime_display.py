from pathlib import Path
import json
import sys

from pa_agent.config.settings import Settings, AIProviderSettings


def test_local_model_and_cumulative_usage_do_not_use_api_fields(qtbot):
    from pa_agent.gui.ai_stream_window import AIStreamPanel
    panel = AIStreamPanel(); qtbot.addWidget(panel)
    panel.bind_settings(Settings(provider={"backend":"codex", "model":"deepseek-v4-pro"}))
    assert "deepseek" not in panel._mode_label.text().lower()
    panel.set_runtime_metadata({"backend":"codex", "model":"gpt-6-astra", "reasoning_effort":"max",
                                "context_window":500000, "service_tier":"priority"})
    panel.update_token_display({"total_input":1000000, "total_output":10000, "context_window":2000000})
    assert "gpt-6-astra" in panel._mode_label.text() and "max" in panel._mode_label.text()
    assert "Fast" in panel._mode_label.text()
    assert "500,000" in panel._token_label.text() and "累计输入" in panel._token_label.text()
    assert "%" not in panel._progress_bar.format()


def test_speed_selector_persists_without_changing_model_or_effort(qtbot, monkeypatch):
    from pa_agent.gui import ai_model_settings_dialog as ui
    from pa_agent.ai import local_harness as h
    monkeypatch.setattr(h, "discover_harness", lambda *_: Path("agent.exe"))
    monkeypatch.setattr(ui, "save_settings", lambda *args: None)
    settings = Settings(provider={"backend":"codex", "harness_model":"gpt-6-astra"})
    dialog = ui.AIModelSettingsDialog(settings); qtbot.addWidget(dialog)
    assert dialog._codex_speed.currentData() == "follow"
    dialog._codex_speed.setCurrentIndex(dialog._codex_speed.findData("fast"))
    dialog._on_save()
    assert settings.provider.codex_speed == "fast"
    assert settings.provider.harness_model == "gpt-6-astra"


def test_scoped_codex_command_preserves_identity_and_speed(tmp_path, monkeypatch):
    from pa_agent.ai import local_harness as h, harness_identity as identity
    import pa_agent.config.paths as paths
    script = tmp_path / "fake.py"
    script.write_text("import json,sys\nprint(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(sys.argv[1:])}}))")
    monkeypatch.setattr(paths, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(h, "discover_harness", lambda *_: Path(sys.executable))
    monkeypatch.setattr(h, "_command", lambda selected, _: [sys.executable, str(script), "--model", selected.harness_model, "-"])
    monkeypatch.setattr(identity, "codex_identity", lambda *a, **k: {
        "backend":"codex", "model":"gpt-6-astra", "reasoning_effort":"max",
        "context_window":500000, "model_provider":"openai", "resolved":True,
        "isolated_config_supported":True, "service_tiers":["priority"],
        "generation_config":{"model_context_window":500000, "service_tier":"default"}})
    client = h.LocalHarnessClient(AIProviderSettings(backend="codex", codex_speed="fast", harness_model="gpt-6-astra"))
    reply = client.chat([{"role":"user","content":"test"}])
    args = json.loads(reply.content)
    assert "--ignore-user-config" in args
    assert args[args.index("--model")+1] == "gpt-6-astra"
    assert args.count("--model") == 1
    assert 'model_reasoning_effort="max"' in args
    assert 'service_tier="priority"' in args and 'model_context_window=500000' in args
    assert reply.raw["runtime_metadata"]["service_tier"] == "priority"
    assert not list((tmp_path/"harness-work").iterdir())
