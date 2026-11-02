"""Local persistence and bounded execution for the web branch's causal engines."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import time

from pa_agent.quant.research_data import DatasetImport, parse_dataset_csv
from pa_agent.quant.sources import restore_frame
from pa_agent.quant.snapshots import factor_evidence
from pa_agent.quant.mining import MiningConfig, MiningSession
from pa_agent.quant.research import ResearchConfig, run_research


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temp.replace(path)


def demo_dataset() -> dict:
    rows = ["time,open,high,low,close,volume"]
    previous = 100.0
    for index in range(600):
        close = 100 + index * .025 + 8 * math.sin(index / 13) + 2 * math.sin(index / 3.7)
        opening = previous * (1 + .001 * math.sin(index / 2.3))
        rows.append(f"{1704067200 + index * 3600},{opening:.8f},"
                    f"{max(opening,close)*1.004:.8f},{min(opening,close)*.996:.8f},"
                    f"{close:.8f},{1000 + 250 * math.sin(index / 7.3) + index % 17 * 12:.8f}")
        previous = close
    return parse_dataset_csv(DatasetImport(csv="\n".join(rows), symbol="DEMO", timeframe="1h"), source="demo")


def mine_local(chart: dict, config: MiningConfig, *, checkpoint_path: Path,
               cancel=None, progress=None) -> dict:
    """Checkpoint every candidate; retain cumulative time across restarts."""
    previous = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.is_file() else None
    # Include code identity, as in the web worker, to reject incompatible resumes.
    digest = hashlib.sha256()
    for name in ("mining.py", "formula.py", "research.py", "quant.py", "research_data.py", "local.py"):
        digest.update(Path(__file__).with_name(name).read_bytes())
    code_hash = digest.hexdigest()
    if previous and previous["code_hash"] != code_hash:
        raise ValueError("计算代码已更新，不能继续旧检查点；请创建新的挖掘任务。")
    elapsed = previous["elapsed_seconds"] if previous else 0.0
    session = MiningSession(chart, config, checkpoint=previous["checkpoint"] if previous else None)
    start = time.monotonic()
    while not session.done:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("公式搜索已取消；检查点已保留。")
        if elapsed + time.monotonic() - start >= config.max_seconds:
            raise TimeoutError("公式搜索已到累计时间上限；没有将部分搜索作为完成结果。")
        checkpoint = session.step()
        atomic_json(checkpoint_path, {"code_hash": code_hash, "checkpoint": checkpoint,
                                     "elapsed_seconds": elapsed + time.monotonic() - start})
        if progress:
            progress(session.next_index, config.trials)
    result = session.finish()
    result["code_hash"] = code_hash
    return result


def run_job(kind: str, chart: dict, config: dict, directory: Path, *, cancel=None, progress=None) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(json.dumps({"kind":kind, "data":chart["dataset_hash"], "config":config},
                                        sort_keys=True).encode()).hexdigest()
    atomic_json(directory / "datasets" / (chart["dataset_hash"] + ".json"), chart)
    if kind == "factors":
        result = factor_evidence(chart)
    elif kind == "research":
        result = run_research(chart, ResearchConfig.model_validate(config))
    elif kind == "mine":
        result = mine_local(chart, MiningConfig.model_validate(config),
                            checkpoint_path=directory / "checkpoints" / (identity + ".json"),
                            cancel=cancel, progress=progress)
    else:
        raise ValueError("未知量化任务")
    envelope = {"kind":kind, "dataset":chart, "result":result}
    atomic_json(directory / "results" / (identity + ".json"), envelope)
    return envelope
