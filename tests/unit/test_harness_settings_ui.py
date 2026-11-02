from pathlib import Path

from pa_agent.config.settings import Settings


def test_profiles_survive_switching_and_local_mode_hides_api(qtbot, monkeypatch):
    from pa_agent.gui import ai_model_settings_dialog as ui
    from pa_agent.ai import local_harness
    monkeypatch.setattr(local_harness, "discover_harness", lambda *_: Path("agent.exe"))
    saved = []
    monkeypatch.setattr(ui, "save_settings", lambda settings, path: saved.append(settings.model_copy(deep=True)))
    settings = Settings(provider={"backend":"codex","harness_model":"codex-model",
                                  "harness_profiles":{"workbuddy":{"model":"buddy-model"}}})
    dialog = ui.AIModelSettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog._api_key_edit.isVisible()
    dialog._harness_model.setText("new-codex")
    dialog._backend_combo.setCurrentIndex(dialog._backend_combo.findData("workbuddy"))
    assert dialog._harness_model.text() == "buddy-model"
    dialog._harness_config_dir.setText("buddy-config")
    dialog._backend_combo.setCurrentIndex(dialog._backend_combo.findData("api"))
    assert dialog._api_key_edit.isVisible()
    dialog._backend_combo.setCurrentIndex(dialog._backend_combo.findData("codex"))
    assert dialog._harness_model.text() == "new-codex"
    dialog._on_save()
    assert saved[0].provider.api_key == ""
    assert saved[0].provider.harness_profiles["workbuddy"]["config_dir"] == "buddy-config"
