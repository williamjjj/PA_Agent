"""Centralised path constants for PA Agent.

All runtime directories are rooted at PROJECT_ROOT.
Import this module everywhere instead of hard-coding paths.
"""
from __future__ import annotations
from pathlib import Path
import os
import sys

# ── Root ──────────────────────────────────────────────────────────────────────
# Resolve dynamically: this file is pa_agent/config/paths.py, so go up 3 levels.
PROJECT_ROOT: Path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))


def resolve_data_root() -> Path:
    """Installed apps use user data; portable apps keep data beside the exe."""
    if override := os.environ.get("PA_AGENT_DATA_DIR", "").strip():
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        if (executable_dir / "portable.flag").is_file():
            return executable_dir / "data"
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PA Agent"
    return PROJECT_ROOT


DATA_ROOT: Path = resolve_data_root()

# ── Prompt engineering assets (read-only at runtime) ─────────────────────────
PROMPT_DIR: Path = PROJECT_ROOT / "prompt_engineering"

# Alias kept for backward compat with design doc
PA_AGENT_DIR: Path = PROJECT_ROOT

# ── Runtime write directories ─────────────────────────────────────────────────
RECORDS_PENDING_DIR: Path = DATA_ROOT / "records" / "pending"
EXPERIENCE_DIR: Path = DATA_ROOT / "experience"
CONFIG_DIR: Path = DATA_ROOT / "config"
LOGS_DIR: Path = DATA_ROOT / "logs"

# ── Individual file paths ─────────────────────────────────────────────────────
FEISHU_JSON_LEGACY_PATH: Path = CONFIG_DIR / "feishu.json"
SETTINGS_JSON_PATH: Path = CONFIG_DIR / "settings.json"
LOG_FILE_PATH: Path = LOGS_DIR / "pa_agent.log"
CRASH_LOG_PATH: Path = LOGS_DIR / "crash.log"
