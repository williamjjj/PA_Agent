"""Authenticated historical research using owned, immutable datasets."""
from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Literal

from fastapi import Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pa_agent.web.birdeye import BirdeyeError
from pa_agent.web.research import ResearchConfig, run_research
from pa_agent.web.research_data import DatasetImport, HistoryRequest, ResearchDataError, fetch_history, parse_dataset_csv
from pa_agent.web.research_store import DatasetInUse, ResearchLimit, ResearchStore, ResearchStoreError


class DatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: DatasetImport | None = None
    history: HistoryRequest | None = None
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    demo: Literal[True] | None = None
    market_kind: Literal["continuous_spot"] | None = None

    @model_validator(mode="after")
    def one_source(self):
        if sum(value is not None for value in (self.data, self.history, self.snapshot_id, self.demo)) != 1:
            raise ValueError("请选择一种研究数据来源。")
        if self.snapshot_id and self.market_kind != "continuous_spot":
            raise ValueError("从快照创建研究须明确声明连续现货模型。")
        return self


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1, max_length=36)
    config: ResearchConfig = Field(default_factory=ResearchConfig)


def research_demo_csv():
    """Fixed synthetic input for reproducible offline workflow demonstrations."""
    rows = ["time,open,high,low,close,volume"]
    previous = 100.0
    for index in range(600):
        closing = 100 + index * 0.025 + 8 * math.sin(index / 13) + 2 * math.sin(index / 3.7)
        opening = previous * (1 + 0.001 * math.sin(index / 2.3))
        high, low = max(opening, closing) * 1.004, min(opening, closing) * 0.996
        volume = 1000 + 250 * math.sin(index / 7.3) + index % 17 * 12
        rows.append(f"{1704067200 + index * 3600},{opening:.8f},{high:.8f},{low:.8f},{closing:.8f},{volume:.8f}")
        previous = closing
    return "\n".join(rows)


def dataset_summary(row):
    return {**row["metadata"], "id": row["id"], "created": row["created"], "dataset_hash": row["digest"]}


def run_payload(row):
    return {**row["payload"], "id": row["id"], "dataset_id": row["dataset_id"], "created": row["created"]}


@contextmanager
def research_errors():
    try:
        yield
    except HTTPException:
        raise
    except (DatasetInUse, ResearchLimit) as exc:
        raise HTTPException(409, str(exc)) from None
    except ResearchStoreError as exc:
        raise HTTPException(422, str(exc)) from None
    except (ResearchDataError, BirdeyeError) as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        # Never expose uploaded rows, provider payloads, credentials or DB URLs.
        raise HTTPException(503, "历史研究暂时不可用，请稍后重试。") from None


@contextmanager
def research_lease(store, uid):
    lease = store.acquire(uid)
    if not lease:
        raise HTTPException(409, "账户已有分析或研究正在进行，请等待结束。")
    try:
        yield
    finally:
        store.release(uid, lease)


def attach_research_routes(app, user, db, settings_for, owned):
    @app.get("/api/research/demo")
    def demo():
        return {"csv": research_demo_csv(), "source": "demo", "symbol": "DEMO", "timeframe": "1h"}

    @app.post("/api/research/datasets")
    def create_dataset(data: DatasetRequest, current=Depends(user)):
        uid, store = current["id"], db()
        if not store.rate_limit("research-dataset:" + uid, 30):
            raise HTTPException(429, "研究数据集创建过于频繁，请 15 分钟后重试。")
        with research_errors(), research_lease(store, uid):
            if data.data:
                chart = parse_dataset_csv(data.data)
            elif data.history:
                if not store.rate_limit("research-history:" + uid, 10):
                    raise HTTPException(429, "历史行情请求过于频繁，请 15 分钟后重试。")
                chart = fetch_history(data.history, settings_for(uid))
            elif data.snapshot_id:
                from pa_agent.web.research_data import dataset_from_snapshot
                chart = dataset_from_snapshot(owned(uid, "snapshot", data.snapshot_id)["payload"])
            else:
                chart = parse_dataset_csv(DatasetImport(csv=research_demo_csv(), symbol="DEMO", timeframe="1h"), source="demo")
            return dataset_summary(ResearchStore(store.engine).put_dataset(uid, chart))

    @app.get("/api/research/datasets")
    def datasets(offset: int = Query(0, ge=0), current=Depends(user)):
        with research_errors():
            return [dataset_summary(row) for row in ResearchStore(db().engine).list_datasets(current["id"], offset, 25)]

    @app.delete("/api/research/datasets/{ident}")
    def delete_dataset(ident: str, current=Depends(user)):
        with research_errors():
            if not ResearchStore(db().engine).delete_dataset(current["id"], ident):
                raise HTTPException(404, "研究数据集不存在。")
        return {"ok": True}

    @app.post("/api/research/runs")
    def create_run(data: RunRequest, current=Depends(user)):
        uid, store = current["id"], db()
        research = ResearchStore(store.engine)
        with research_errors():
            dataset = research.get_dataset(uid, data.dataset_id)
            if not dataset:
                raise HTTPException(404, "研究数据集不存在。")
            if not store.rate_limit("research-run:" + uid, 20):
                raise HTTPException(429, "研究运行过于频繁，请 15 分钟后重试。")
            with research_lease(store, uid):
                try:
                    result = run_research(dataset["chart"], data.config)
                except ValueError as exc:
                    # The engine raises fixed validation messages without input values.
                    raise HTTPException(422, str(exc)) from None
                payload = {"dataset": dataset_summary(dataset), "result": result}
                return run_payload(research.create_run(uid, data.dataset_id, payload))

    @app.get("/api/research/runs")
    def runs(offset: int = Query(0, ge=0), current=Depends(user)):
        with research_errors():
            output = []
            for row in ResearchStore(db().engine).list_runs(current["id"], offset, 25):
                summary = row["summary"]
                dataset = summary.get("dataset", {})
                metrics = summary.get("result", {}).get("segments", {}).get("test", {}).get("metrics") or {}
                output.append({"id": row["id"], "created": row["created"], "dataset_id": row["dataset_id"],
                               "symbol": dataset.get("symbol"), "timeframe": dataset.get("timeframe"),
                               "factor": summary.get("config", {}).get("factor"),
                               "test_return": summary.get("test_return", metrics.get("total_return")),
                               "benchmark_return": summary.get("benchmark_return", metrics.get("benchmark_return"))})
            return output

    def owned_run(uid, ident):
        row = ResearchStore(db().engine).get_run(uid, ident)
        if not row:
            raise HTTPException(404, "研究记录不存在。")
        return row

    @app.get("/api/research/runs/{ident}")
    def run_detail(ident: str, current=Depends(user)):
        with research_errors():
            return run_payload(owned_run(current["id"], ident))

    @app.get("/api/research/runs/{ident}/export")
    def export(ident: str, current=Depends(user)):
        with research_errors():
            uid = current["id"]
            row = owned_run(uid, ident)
            dataset = ResearchStore(db().engine).get_dataset(uid, row["dataset_id"])
            # Include the original OHLCV, so an export can be independently replayed.
            payload = {**run_payload(row), "data": dataset["chart"]}
            return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="pa-research-{row["id"]}.json"'})

    @app.delete("/api/research/runs/{ident}")
    def delete_run(ident: str, current=Depends(user)):
        with research_errors():
            if not ResearchStore(db().engine).delete_run(current["id"], ident):
                raise HTTPException(404, "研究记录不存在。")
        return {"ok": True}
