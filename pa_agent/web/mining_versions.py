"""The executable research contract recorded when a job is submitted."""
from __future__ import annotations

import hashlib
from pathlib import Path


def contract_versions():
    from pa_agent.web.mining import MINING_VERSION, VOCAB_VERSION
    from pa_agent.web.quant import FEATURE_VERSION
    from pa_agent.web.research import ENGINE_VERSION

    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for name in ("web/mining.py", "web/formula.py", "web/research.py", "web/quant.py",
                 "web/mining_versions.py", "web/research_data.py", "web/sources.py",
                 "web/snapshots.py", "web/data.py", "web/birdeye.py",
                 "data/base.py", "data/snapshot.py", "data/bar_close_wait.py",
                 "util/timefmt.py"):
        path = root / name
        if path.exists():
            digest.update(name.encode() + b"\0" + path.read_bytes())
    return {"mining_version": MINING_VERSION, "vocab_version": VOCAB_VERSION,
            "feature_version": FEATURE_VERSION, "research_version": ENGINE_VERSION,
            "code_revision": digest.hexdigest()}
