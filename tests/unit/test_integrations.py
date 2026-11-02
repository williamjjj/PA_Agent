import json

from pa_agent.config.settings import Settings


def test_integration_preserves_old_skill_and_is_idempotent(tmp_path, monkeypatch):
    from pa_agent import integrations
    from pa_agent.config import paths, settings
    assets = tmp_path / "app"
    (assets / "integrations/pa-agent").mkdir(parents=True)
    (assets / "integrations/pa-agent/SKILL.md").write_text("new skill", encoding="utf-8")
    monkeypatch.setattr(paths, "PROJECT_ROOT", assets)
    for name in ("CODEX_HOME", "WORKBUDDY_CONFIG_DIR", "HERMES_HOME", "CODEBUDDY_CONFIG_DIR"):
        home = tmp_path / name
        home.mkdir()
        monkeypatch.setenv(name, str(home))
    monkeypatch.setattr(settings, "load_settings", lambda: Settings())
    original = tmp_path / "CODEX_HOME/skills/pa-agent"
    original.mkdir(parents=True)
    (original / "SKILL.md").write_text("old skill", encoding="utf-8")
    (original / "notes.txt").write_text("keep", encoding="utf-8")
    results = integrations.install_skills()
    assert len(results) == 4 and all(item["installed"] for item in results)
    backups = list((tmp_path / "CODEX_HOME/pa-agent-integration-backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "SKILL.md").read_text() == "old skill"
    assert (original / "notes.txt").read_text() == "keep"
    assert json.loads((original / "location.json").read_text())["command"][-2:] == ["-m", "pa_agent.cli"]
    integrations.install_skills()
    assert len(list((tmp_path / "CODEX_HOME/pa-agent-integration-backups").iterdir())) == 1
