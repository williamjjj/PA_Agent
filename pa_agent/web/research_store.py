"""Immutable, tenant-scoped research datasets and reproducible run records."""
from __future__ import annotations

import json
import re
import time
import uuid

from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKeyConstraint,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
datasets = Table(
    "web_research_datasets", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("digest", String(64), nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("chart", JSON, nullable=False),
    Column("created", Float, nullable=False),
    UniqueConstraint("user_id", "digest", name="web_research_datasets_owner_digest"),
    UniqueConstraint("user_id", "id", name="web_research_datasets_owner_id"),
)
runs = Table(
    "web_research_runs", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("dataset_id", String(36), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("summary", JSON, nullable=False),
    Column("created", Float, nullable=False),
    ForeignKeyConstraint(
        ["user_id", "dataset_id"],
        ["web_research_datasets.user_id", "web_research_datasets.id"],
        name="web_research_runs_owned_dataset", ondelete="RESTRICT",
    ),
)
Index("web_research_datasets_owner_created", datasets.c.user_id, datasets.c.created)
Index("web_research_runs_owner_created", runs.c.user_id, runs.c.created)
Index("web_research_runs_owner_dataset", runs.c.user_id, runs.c.dataset_id)

MAX_DATASETS = 20
MAX_RUNS = 100
_DATASET_METADATA = (
    "source", "symbol", "timeframe", "market_kind", "instrument", "captured_at",
    "dataset_hash", "dataset_version", "quality", "provenance", "bar_count", "start", "end",
)


class ResearchStoreError(ValueError):
    """A fixed safe message about a research storage constraint."""


class DatasetInUse(ResearchStoreError):
    """An immutable run references this dataset, so it cannot be deleted."""


class ResearchLimit(ResearchStoreError):
    """An atomic per-account research storage limit has been reached."""


def create_tables(conn):
    """Called inside Store's existing PostgreSQL advisory-lock transaction."""
    metadata.create_all(conn)


def _copy_json(value):
    """Detach mutable caller objects and reject non-standard JSON values."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        raise ResearchStoreError("研究记录须为有效的 JSON 数据。") from None


def _small_summary(value, depth=0):
    """Keep bounded scalar/object metadata; omit every time-series/list value."""
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, dict):
        return {str(key)[:100]: _small_summary(item, depth + 1)
                for key, item in list(value.items())[:40]
                if not isinstance(item, (list, tuple))}
    return None


def _page(offset, limit):
    return max(0, int(offset)), min(100, max(1, int(limit)))


class ResearchStore:
    """Use the main Store's engine, authentication identities and transactions."""

    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _lock_user(conn, uid):
        # Acquire the writer lock before reading counts on SQLite and a row
        # lock on PostgreSQL. All research mutations use this same lock so
        # quota checks, deduplication and reference checks cannot race.
        from pa_agent.web.store import users

        matched = conn.execute(update(users).where(users.c.id == uid)
                               .values(settings=users.c.settings))
        if not matched.rowcount:
            raise ResearchStoreError("账户不存在，请重新登录。")

    @staticmethod
    def _count(conn, table, uid):
        return conn.execute(select(func.count()).select_from(table)
                            .where(table.c.user_id == uid)).scalar_one()

    def put_dataset(self, uid, chart):
        digest = chart.get("dataset_hash")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ResearchStoreError("研究数据集缺少有效的内容哈希。")
        frozen = _copy_json(chart)
        details = {key: frozen[key] for key in _DATASET_METADATA if key in frozen}
        for key in ("bar_count", "start", "end"):
            if key not in details and key in frozen.get("quality", {}):
                details[key] = frozen["quality"][key]
        with self.engine.begin() as conn:
            self._lock_user(conn, uid)
            existing = conn.execute(select(datasets.c.id, datasets.c.digest,
                                          datasets.c.metadata, datasets.c.created).where(
                datasets.c.user_id == uid, datasets.c.digest == digest)).mappings().first()
            if existing:
                return {**dict(existing), "created_new": False}
            if self._count(conn, datasets, uid) >= MAX_DATASETS:
                raise ResearchLimit("每个账户最多保存 20 个研究数据集，请先删除未使用的数据集。")
            row = {"id": str(uuid.uuid4()), "digest": digest,
                   "metadata": details, "created": time.time()}
            conn.execute(insert(datasets).values(**row, user_id=uid, chart=frozen))
        return {**row, "created_new": True}

    def get_dataset(self, uid, ident):
        with self.engine.connect() as conn:
            row = conn.execute(select(datasets.c.id, datasets.c.digest, datasets.c.metadata,
                                      datasets.c.chart, datasets.c.created).where(
                datasets.c.user_id == uid, datasets.c.id == ident)).mappings().first()
        return dict(row) if row else None

    def list_datasets(self, uid, offset=0, limit=20):
        offset, limit = _page(offset, limit)
        with self.engine.connect() as conn:
            rows = conn.execute(select(datasets.c.id, datasets.c.digest, datasets.c.metadata,
                                       datasets.c.created).where(datasets.c.user_id == uid)
                                .order_by(datasets.c.created.desc(), datasets.c.id)
                                .offset(offset).limit(limit)).mappings().all()
        return [dict(row) for row in rows]

    def delete_dataset(self, uid, ident):
        try:
            with self.engine.begin() as conn:
                self._lock_user(conn, uid)
                exists = conn.execute(select(datasets.c.id).where(
                    datasets.c.user_id == uid, datasets.c.id == ident)).first()
                if not exists:
                    return False
                referenced = conn.execute(select(runs.c.id).where(
                    runs.c.user_id == uid, runs.c.dataset_id == ident).limit(1)).first()
                if referenced:
                    raise DatasetInUse("数据集已有研究运行记录，不能删除。")
                from pa_agent.web.mining_store import jobs
                mining_reference = conn.execute(select(jobs.c.id).where(
                    jobs.c.user_id == uid, jobs.c.dataset_id == ident).limit(1)).first()
                if mining_reference:
                    raise DatasetInUse("数据集已有挖掘任务，不能删除；请先删除已结束的任务。")
                return bool(conn.execute(delete(datasets).where(
                    datasets.c.user_id == uid, datasets.c.id == ident)).rowcount)
        except IntegrityError:
            raise DatasetInUse("数据集已有研究运行记录，不能删除。") from None

    def create_run(self, uid, dataset_id, payload):
        frozen = _copy_json(payload)
        if not isinstance(frozen, dict):
            raise ResearchStoreError("研究运行记录须为 JSON 对象。")
        with self.engine.begin() as conn:
            self._lock_user(conn, uid)
            dataset = conn.execute(select(datasets.c.digest).where(
                datasets.c.user_id == uid, datasets.c.id == dataset_id)).first()
            if dataset is None:
                raise ResearchStoreError("研究数据集不存在。")
            if self._count(conn, runs, uid) >= MAX_RUNS:
                raise ResearchLimit("每个账户最多保存 100 个研究运行记录。")
            summary = _small_summary(frozen)
            result = frozen.get("result", {})
            if "config" not in summary and isinstance(result, dict):
                summary["config"] = _small_summary(result.get("config", {}))
            if isinstance(summary.get("config"), dict):
                summary["factor"] = summary["config"].get("factor")
            dataset_info = frozen.get("dataset", {})
            if isinstance(dataset_info, dict):
                for key in ("symbol", "timeframe"):
                    summary[key] = _small_summary(dataset_info.get(key))
            if isinstance(result, dict):
                metrics = result.get("segments", {}).get("test", {}).get("metrics", {})
                summary["test_return"] = _small_summary(metrics.get("total_return"))
                summary["benchmark_return"] = _small_summary(metrics.get("benchmark_return"))
            summary["dataset_hash"] = dataset.digest
            row = {"id": str(uuid.uuid4()), "dataset_id": dataset_id,
                   "payload": frozen, "summary": summary, "created": time.time()}
            conn.execute(insert(runs).values(**row, user_id=uid))
        return row

    def get_run(self, uid, ident):
        with self.engine.connect() as conn:
            row = conn.execute(select(runs.c.id, runs.c.dataset_id, runs.c.payload,
                                      runs.c.summary, runs.c.created).where(
                runs.c.user_id == uid, runs.c.id == ident)).mappings().first()
        return dict(row) if row else None

    def list_runs(self, uid, offset=0, limit=20):
        offset, limit = _page(offset, limit)
        with self.engine.connect() as conn:
            rows = conn.execute(select(runs.c.id, runs.c.dataset_id, runs.c.summary,
                                       runs.c.created).where(runs.c.user_id == uid)
                                .order_by(runs.c.created.desc(), runs.c.id)
                                .offset(offset).limit(limit)).mappings().all()
        return [dict(row) for row in rows]

    def delete_run(self, uid, ident):
        with self.engine.begin() as conn:
            self._lock_user(conn, uid)
            return bool(conn.execute(delete(runs).where(
                runs.c.user_id == uid, runs.c.id == ident)).rowcount)

    def count_datasets(self, uid):
        with self.engine.connect() as conn:
            return self._count(conn, datasets, uid)

    def count_runs(self, uid):
        with self.engine.connect() as conn:
            return self._count(conn, runs, uid)
