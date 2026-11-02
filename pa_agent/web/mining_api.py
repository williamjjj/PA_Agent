"""Queue-only web routes for account-owned background formula mining."""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from pa_agent.web.mining import MiningConfig
from pa_agent.web.mining_store import MiningConflict, MiningLimit, MiningStore, MiningStoreError
from pa_agent.web.mining_versions import contract_versions
from pa_agent.web.research import _validate_chart
from pa_agent.web.research_api import dataset_summary
from pa_agent.web.research_store import ResearchStore


class SubmitMining(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1, max_length=36)
    config: MiningConfig = Field(default_factory=MiningConfig)
    idempotency_key: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class RetryMining(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


@contextmanager
def mining_errors():
    try:
        yield
    except HTTPException:
        raise
    except (MiningConflict, MiningLimit) as exc:
        raise HTTPException(409, str(exc)) from None
    except MiningStoreError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(503, "后台挖掘暂时不可用，请稍后重试。") from None


_PUBLIC = ("id", "dataset_id", "config", "status", "progress", "attempts", "created", "updated",
           "started", "finished", "error", "consumed_seconds", "provenance")


def public_job(row, *, detail=False):
    result = {key: row.get(key) for key in _PUBLIC}
    if detail:
        result["result"] = row.get("result")
    return result


def attach_mining_routes(app, user, db):
    def stores():
        engine = db().engine
        return MiningStore(engine), ResearchStore(engine)

    def owned_job(store, uid, ident):
        row = store.get_job(uid, ident)
        if not row:
            raise HTTPException(404, "挖掘任务不存在。")
        return row

    def submit(uid, data, jobs, research):
        dataset = research.get_dataset(uid, data.dataset_id)
        if not dataset:
            raise HTTPException(404, "研究数据集不存在。")
        try:
            bars, _ = _validate_chart(dataset["chart"])
            train = int(len(bars) * data.config.research.train_fraction)
            validation = int(len(bars) * data.config.research.validation_fraction)
            if train < 120 or validation < 60 or len(bars) - train - validation < 60:
                raise ValueError("训练至少需 120 根，验证和测试各至少需 60 根，请调整切分比例。")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        row = jobs.create_job(uid, data.dataset_id, data.config.model_dump(), data.idempotency_key,
                              provenance=contract_versions())
        return {**public_job(row, detail=True), "dataset": dataset_summary(dataset)}

    @app.get("/api/mining/status")
    def status(current=Depends(user)):
        with mining_errors():
            jobs, _ = stores()
            online = jobs.worker_status()
            versions = contract_versions()
            compatible = sum(worker["versions"] == versions for worker in online["workers"])
            return {"worker_available": bool(compatible), "workers_online": compatible,
                    "limits": {"active_jobs": 3, "total_jobs": 50,
                                         "max_trials": 128, "max_seconds": 120},
                    "versions": versions}

    @app.post("/api/mining/jobs")
    def create(data: SubmitMining, current=Depends(user)):
        uid = current["id"]
        if not db().rate_limit("mining-submit:" + uid, 30):
            raise HTTPException(429, "挖掘任务提交过于频繁，请 15 分钟后重试。")
        with mining_errors():
            return submit(uid, data, *stores())

    @app.get("/api/mining/jobs")
    def list_jobs(offset: int = Query(0, ge=0), current=Depends(user)):
        with mining_errors():
            jobs, research = stores()
            # Dataset count is bounded to20; read metadata only, never OHLCV here.
            datasets = {row["id"]: dataset_summary(row) for row in research.list_datasets(current["id"], limit=100)}
            return [{**public_job(row), "dataset": datasets.get(row["dataset_id"])}
                    for row in jobs.list_jobs(current["id"], offset=offset, limit=25)]

    @app.get("/api/mining/jobs/{ident}")
    def detail(ident: str, current=Depends(user)):
        with mining_errors():
            jobs, research = stores()
            row = owned_job(jobs, current["id"], ident)
            dataset = next(item for item in research.list_datasets(current["id"], limit=100)
                           if item["id"] == row["dataset_id"])
            return {**public_job(row, detail=True), "dataset": dataset_summary(dataset)}

    @app.post("/api/mining/jobs/{ident}/cancel")
    def cancel(ident: str, current=Depends(user)):
        with mining_errors():
            jobs, _ = stores()
            owned_job(jobs, current["id"], ident)
            row = jobs.cancel_job(current["id"], ident)
            return public_job(row, detail=True)

    @app.post("/api/mining/jobs/{ident}/retry")
    def retry(ident: str, data: RetryMining, current=Depends(user)):
        with mining_errors():
            jobs, research = stores()
            row = owned_job(jobs, current["id"], ident)
            if row["status"] not in {"failed", "cancelled", "timed_out"}:
                raise HTTPException(409, "仅可重新提交失败、已取消或超时的任务。")
            if not db().rate_limit("mining-submit:" + current["id"], 30):
                raise HTTPException(429, "挖掘任务提交过于频繁，请 15 分钟后重试。")
            request = SubmitMining(dataset_id=row["dataset_id"], config=row["config"], idempotency_key=data.idempotency_key)
            return submit(current["id"], request, jobs, research)

    @app.delete("/api/mining/jobs/{ident}")
    def delete(ident: str, current=Depends(user)):
        with mining_errors():
            jobs, _ = stores()
            if not jobs.delete_job(current["id"], ident):
                raise HTTPException(404, "挖掘任务不存在。")
            return {"ok": True}

    @app.get("/api/mining/jobs/{ident}/export")
    def export(ident: str, current=Depends(user)):
        with mining_errors():
            jobs, research = stores()
            row = owned_job(jobs, current["id"], ident)
            dataset = research.get_dataset(current["id"], row["dataset_id"])
            return JSONResponse({"job": public_job(row, detail=True), "dataset": dataset_summary(dataset),
                                 "data": dataset["chart"]},
                                headers={"Content-Disposition": f'attachment; filename="pa-mining-{row["id"]}.json"'})
