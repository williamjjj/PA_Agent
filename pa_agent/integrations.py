"""Install a small local skill without changing an agent's global configuration."""
from __future__ import annotations
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys


def install_skills(target: str = "all") -> list[dict]:
    from pa_agent.config.paths import DATA_ROOT, PROJECT_ROOT
    from pa_agent.config.settings import load_settings
    provider = load_settings().provider
    buddy_profile = provider.harness_profiles.get("workbuddy", {})
    buddy_config = (provider.harness_config_dir if provider.backend == "workbuddy"
                    else buddy_profile.get("config_dir", ""))
    homes = {
        "codex": Path(os.environ.get("CODEX_HOME", Path.home()/".codex")),
        "workbuddy": Path(os.environ.get("WORKBUDDY_CONFIG_DIR", Path.home()/".workbuddy")),
        "hermes": Path(os.environ.get("HERMES_HOME", "C:/hermes" if Path("C:/hermes").is_dir() else Path.home()/".hermes")),
    }
    destinations = list(homes.items())
    # WorkBuddy desktop and the independently authenticated full CodeBuddy CLI
    # can use different skill directories. Register both existing homes.
    for extra in (os.environ.get("CODEBUDDY_CONFIG_DIR", ""), buddy_config):
        if extra:
            path = Path(extra).expanduser().resolve()
            if not any(name == "workbuddy" and home.resolve() == path for name, home in destinations):
                destinations.append(("workbuddy", path))
    executable = Path(sys.executable).resolve()
    command = ([str(executable.with_name("pa-agent-cli.exe"))] if getattr(sys,"frozen",False)
               else [str(executable),"-m","pa_agent.cli"])
    working_directory = DATA_ROOT if getattr(sys, "frozen", False) else PROJECT_ROOT
    results = []
    for name,home in destinations:
        if target not in ("all",name): continue
        if not home.is_dir():
            results.append({"target":name,"installed":False,"reason":"harness home not found"})
            continue
        destination = home/"skills/pa-agent"
        skill = (PROJECT_ROOT/"integrations/pa-agent/SKILL.md").read_bytes()
        location = json.dumps({"command":command,"working_directory":str(working_directory)},
                              ensure_ascii=False,indent=2)
        unchanged = ((destination/"SKILL.md").is_file()
                     and (destination/"SKILL.md").read_bytes() == skill
                     and (destination/"location.json").is_file()
                     and (destination/"location.json").read_text(encoding="utf-8") == location)
        if destination.exists() and not unchanged:
            backup = home/"pa-agent-integration-backups"/datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copytree(destination,backup)
        if not unchanged:
            destination.mkdir(parents=True,exist_ok=True)
            (destination/"SKILL.md").write_bytes(skill)
            (destination/"location.json").write_text(location,encoding="utf-8")
        results.append({"target":name,"installed":True,"skill":str(destination/"SKILL.md")})
    return results
