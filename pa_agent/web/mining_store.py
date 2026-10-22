"""Persistent tenant-scoped mining jobs with short transactions and fenced leases."""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import time
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)

from pa_agent.web.research_store import datasets

metadata = MetaData()
jobs = Table(
    "web_mining_jobs", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("dataset_id", String(36), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_digest", String(64), nullable=False),
    Column("config", JSON, nullable=False),
    Column("provenance", JSON, nullable=False),
    Column("status", String(20), nullable=False),
    Column("checkpoint", JSON, nullable=False),
    Column("result", JSON, nullable=True),
    Column("created", Float, nullable=False),
    Column("updated", Float, nullable=False),
    Column("started", Float, nullable=True),
    Column("finished", Float, nullable=True),
    Column("attempts", Integer, nullable=False),
    Column("crash_recoveries", Integer, nullable=False),
    Column("worker_id", String(128), nullable=False),
    Column("lease_token", String(64), nullable=False),
    Column("lease_until", Float, nullable=False),
    Column("accounted_at", Float, nullable=False),
    Column("progress", Float, nullable=False),
    Column("error", Text, nullable=False),
    Column("consumed_seconds", Float, nullable=False),
    UniqueConstraint("user_id", "idempotency_key", name="web_mining_jobs_owner_key"),
    ForeignKeyConstraint(["user_id", "dataset_id"], [datasets.c.user_id, datasets.c.id],
                         name="web_mining_jobs_owned_dataset", ondelete="RESTRICT"),
)
workers = Table(
    "web_mining_workers", metadata,
    Column("id", String(128), primary_key=True),
    Column("versions", JSON, nullable=False),
    Column("started", Float, nullable=False),
    Column("heartbeat", Float, nullable=False),
    Column("stopped", Boolean, nullable=False),
)
Index("web_mining_jobs_owner_created", jobs.c.user_id, jobs.c.created)
Index("web_mining_jobs_queue", jobs.c.status, jobs.c.created)
Index("web_mining_jobs_owner_dataset", jobs.c.user_id, jobs.c.dataset_id)

ACTIVE = ("queued", "running", "cancel_requested")
LEASED = ("running", "cancel_requested")
TERMINAL = ("succeeded", "failed", "cancelled", "timed_out")
MAX_ACTIVE = 3
MAX_TOTAL = 50
MAX_CRASHES = 3
CLAIM_BATCH = 100
_SUMMARY = tuple(column for column in jobs.c
                 if column.name not in {"checkpoint", "result", "request_digest", "lease_token",
                                        "accounted_at", "idempotency_key"})


class MiningStoreError(ValueError):
    """A fixed safe storage error, suitable for an API response."""


class MiningConflict(MiningStoreError):
    """A request conflicts with an existing immutable identity or job state."""


class MiningLimit(MiningStoreError):
    """The account has reached its persistent mining quota."""


def create_tables(conn):
    metadata.create_all(conn)


def _json_copy(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
    except (ValueError, TypeError, OverflowError):
        raise MiningStoreError("挖掘任务须包含有效的 JSON 数据。") from None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MiningStoreError("挖掘任务计时或进度无效。")
    if value < 0:
        raise MiningStoreError("挖掘任务计时或进度无效。")
    return float(value)


def _budget(row):
    return float(row["config"].get("max_seconds", 60))


def _consumed(row, now, reported=None):
    # A crashed worker cannot own compute after its lease expires. Preserve
    # restartability by charging unreported leased time, never offline queue time.
    until = min(now, row["lease_until"])
    elapsed = min(_budget(row), row["consumed_seconds"] + max(0, until - row["accounted_at"]))
    if reported is not None:
        return max(row["consumed_seconds"], _number(reported), elapsed)
    return elapsed


def _full(row):
    return {**dict(row), "uid": row["user_id"]} if row else None


class MiningStore:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _lock_user(conn, uid):
        from pa_agent.web.store import users
        matched = conn.execute(update(users).where(users.c.id == uid)
                               .values(settings=users.c.settings))
        if not matched.rowcount:
            raise MiningStoreError("账户不存在，请重新登录。")

    @staticmethod
    def _terminal(conn, row, status, now, consumed, *, error="", result=None):
        values = {"status": status, "updated": now, "finished": now,
                  "lease_token": "", "lease_until": 0, "accounted_at": now,
                  "consumed_seconds": consumed, "error": error, "result": result}
        if status == "succeeded":
            values["progress"] = 1.0
        conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(**values))

    @staticmethod
    def _lease_seconds(value):
        value = _number(value)
        if not 0 < value <= 300:
            raise MiningStoreError("工作进程租约时长无效。")
        return value

    def create_job(self, uid, dataset_id, config, idempotency_key, *, provenance=None):
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise MiningStoreError("请提供有效的任务幂等标识。")
        config = _json_copy(config)
        provenance = _json_copy(provenance or {})
        if not isinstance(config, dict) or not isinstance(provenance, dict):
            raise MiningStoreError("挖掘配置和版本信息须为 JSON 对象。")
        budget = _number(config.get("max_seconds", 60))
        if not 10 <= budget <= 120:
            raise MiningStoreError("单个挖掘任务的时间预算须为 10 至 120 秒。")
        digest = hashlib.sha256(json.dumps({"dataset_id": dataset_id, "config": config},
                                          sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.engine.begin() as conn:
            self._lock_user(conn, uid)
            existing = conn.execute(select(jobs).where(jobs.c.user_id == uid,
                                   jobs.c.idempotency_key == idempotency_key)).mappings().first()
            if existing:
                if existing["request_digest"] != digest:
                    raise MiningConflict("同一幂等标识已用于不同的数据集或挖掘配置。")
                return {**_full(existing), "created_new": False}
            dataset = conn.execute(select(datasets.c.digest).where(
                datasets.c.user_id == uid, datasets.c.id == dataset_id)).first()
            if dataset is None:
                raise MiningStoreError("研究数据集不存在。")
            counts = conn.execute(select(jobs.c.status, func.count()).where(
                jobs.c.user_id == uid).group_by(jobs.c.status)).all()
            if sum(count for status, count in counts if status in ACTIVE) >= MAX_ACTIVE:
                raise MiningLimit("每个账户最多保留 3 个排队或运行中的挖掘任务。")
            if sum(count for _, count in counts) >= MAX_TOTAL:
                raise MiningLimit("每个账户最多保存 50 个挖掘任务，请先删除已结束任务。")
            now = time.time()
            row = {"id": str(uuid.uuid4()), "user_id": uid, "dataset_id": dataset_id,
                   "idempotency_key": idempotency_key, "request_digest": digest,
                   "config": config, "provenance": {**provenance, "dataset_hash": dataset.digest},
                   "status": "queued", "checkpoint": {}, "result": None, "created": now,
                   "updated": now, "started": None, "finished": None, "attempts": 0,
                   "crash_recoveries": 0, "worker_id": "", "lease_token": "", "lease_until": 0,
                   "accounted_at": now, "progress": 0.0, "error": "", "consumed_seconds": 0.0}
            conn.execute(insert(jobs).values(**row))
        return {**_full(row), "created_new": True}

    def get_job(self, uid, ident):
        with self.engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.user_id == uid,
                               jobs.c.id == ident)).mappings().first()
        return _full(row)

    def list_jobs(self, uid, offset=0, limit=20):
        with self.engine.connect() as conn:
            rows = conn.execute(select(*_SUMMARY).where(jobs.c.user_id == uid)
                                .order_by(jobs.c.created.desc(), jobs.c.id)
                                .offset(max(0, int(offset))).limit(min(100, max(1, int(limit)))))
            return [dict(row) for row in rows.mappings()]

    def cancel_job(self, uid, ident):
        with self.engine.begin() as conn:
            self._lock_user(conn, uid)
            row = conn.execute(select(jobs).where(jobs.c.user_id == uid,
                               jobs.c.id == ident)).mappings().first()
            if row is None:
                return None
            now = time.time()
            if row["status"] == "queued":
                self._terminal(conn, row, "cancelled", now, row["consumed_seconds"])
            elif row["status"] in LEASED:
                if row["lease_until"] <= now:
                    self._terminal(conn, row, "cancelled", now, _consumed(row, now))
                else:
                    conn.execute(update(jobs).where(jobs.c.id == ident).values(
                        status="cancel_requested", updated=now))
            return _full(conn.execute(select(jobs).where(jobs.c.id == ident)).mappings().one())

    def delete_job(self, uid, ident):
        with self.engine.begin() as conn:
            self._lock_user(conn, uid)
            status = conn.execute(select(jobs.c.status).where(
                jobs.c.user_id == uid, jobs.c.id == ident)).scalar_one_or_none()
            if status is None:
                return False
            if status not in TERMINAL:
                raise MiningConflict("只能删除已结束的挖掘任务。")
            return bool(conn.execute(delete(jobs).where(jobs.c.user_id == uid,
                                                       jobs.c.id == ident)).rowcount)

    def _recover(self, conn, uid, now):
        expired = conn.execute(select(jobs).where(jobs.c.user_id == uid,
                               jobs.c.status.in_(LEASED), jobs.c.lease_until <= now)).mappings()
        for row in expired:
            consumed = _consumed(row, now)
            crashes = row["crash_recoveries"] + 1
            conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(crash_recoveries=crashes))
            if row["status"] == "cancel_requested":
                self._terminal(conn, row, "cancelled", now, consumed)
            elif consumed >= _budget(row):
                self._terminal(conn, row, "timed_out", now, consumed, error="挖掘时间预算已耗尽。")
            elif crashes >= MAX_CRASHES:
                self._terminal(conn, row, "failed", now, consumed,
                               error="工作进程连续中断，已停止自动恢复。")
            else:
                conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(
                    status="queued", lease_token="", lease_until=0, updated=now,
                    accounted_at=now, consumed_seconds=consumed, crash_recoveries=crashes))

    def claim(self, worker_id, lease_seconds=30):
        lease_seconds = self._lease_seconds(lease_seconds)
        now = time.time()
        with self.engine.begin() as conn:
            busy = jobs.alias("busy_mining_jobs")
            busy_users = select(busy.c.user_id).where(busy.c.status.in_(LEASED),
                                                    busy.c.lease_until > now)
            candidates = conn.execute(select(jobs.c.user_id, func.min(jobs.c.created)).where(
                or_(jobs.c.status == "queued", jobs.c.status.in_(LEASED) &
                    (jobs.c.lease_until <= now)), jobs.c.user_id.not_in(busy_users))
                .group_by(jobs.c.user_id)
                .order_by(func.min(jobs.c.created), jobs.c.user_id).limit(CLAIM_BATCH)).all()
            for uid, _ in candidates:
                self._lock_user(conn, uid)
                now = time.time()
                self._recover(conn, uid, now)
                active = conn.execute(select(jobs.c.id).where(jobs.c.user_id == uid,
                                      jobs.c.status.in_(LEASED)).limit(1)).first()
                if active:
                    continue
                queued = conn.execute(select(jobs).where(jobs.c.user_id == uid,
                                      jobs.c.status == "queued")
                                      .order_by(jobs.c.created, jobs.c.id)).mappings().all()
                for row in queued:
                    if row["consumed_seconds"] >= _budget(row):
                        self._terminal(conn, row, "timed_out", now, row["consumed_seconds"],
                                       error="挖掘时间预算已耗尽。")
                        continue
                    values = {"status": "running", "worker_id": worker_id,
                              "lease_token": secrets.token_hex(24), "lease_until": now + lease_seconds,
                              "updated": now, "started": row["started"] or now,
                              "accounted_at": now, "attempts": row["attempts"] + 1}
                    conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(**values))
                    return _full({**dict(row), **values})
        return None

    def _leased(self, conn, ident, token, now):
        uid = conn.execute(select(jobs.c.user_id).where(jobs.c.id == ident)).scalar_one_or_none()
        if uid is None or not token:
            return None
        self._lock_user(conn, uid)
        now = time.time()
        return conn.execute(select(jobs).where(jobs.c.id == ident, jobs.c.lease_token == token,
                            jobs.c.status.in_(LEASED), jobs.c.lease_until > now)).mappings().first()

    def heartbeat(self, ident, token, lease_seconds=30, consumed_seconds=None):
        lease_seconds = self._lease_seconds(lease_seconds)
        with self.engine.begin() as conn:
            now = time.time()
            row = self._leased(conn, ident, token, now)
            if row is None:
                return None
            now = time.time()
            consumed = _consumed(row, now, consumed_seconds)
            status, until = row["status"], now + lease_seconds
            if consumed >= _budget(row) and status != "cancel_requested":
                status, until = "timed_out", 0
                self._terminal(conn, row, status, now, consumed, error="挖掘时间预算已耗尽。")
            else:
                conn.execute(update(jobs).where(jobs.c.id == ident).values(
                    updated=now, lease_until=until, accounted_at=now, consumed_seconds=consumed))
            return {"status": status, "cancel_requested": status == "cancel_requested",
                    "lease_until": until, "consumed_seconds": consumed}

    def save_checkpoint(self, ident, token, checkpoint, progress, consumed_seconds):
        checkpoint = _json_copy(checkpoint)
        progress = _number(progress)
        if progress > 1:
            raise MiningStoreError("挖掘任务进度须在 0 至 1 之间。")
        with self.engine.begin() as conn:
            now = time.time()
            row = self._leased(conn, ident, token, now)
            if row is None:
                return False
            now = time.time()
            consumed = _consumed(row, now, consumed_seconds)
            if row["status"] == "cancel_requested":
                self._terminal(conn, row, "cancelled", now, consumed)
                return False
            conn.execute(update(jobs).where(jobs.c.id == ident).values(
                checkpoint=checkpoint, progress=max(progress, row["progress"]),
                updated=now, accounted_at=now, consumed_seconds=consumed))
            if consumed >= _budget(row) and row["status"] != "cancel_requested":
                self._terminal(conn, row, "timed_out", now, consumed, error="挖掘时间预算已耗尽。")
                return False
            return True

    def complete(self, ident, token, result, consumed_seconds):
        result = _json_copy(result)
        with self.engine.begin() as conn:
            now = time.time()
            row = self._leased(conn, ident, token, now)
            if row is None:
                return False
            now = time.time()
            consumed = _consumed(row, now, consumed_seconds)
            status = ("cancelled" if row["status"] == "cancel_requested" else
                      "timed_out" if consumed >= _budget(row) else "succeeded")
            self._terminal(conn, row, status, now, consumed,
                           error="挖掘时间预算已耗尽。" if status == "timed_out" else "",
                           result=result if status == "succeeded" else None)
            return True

    def fail(self, ident, token, error, status="failed", consumed_seconds=None):
        if status not in ("failed", "cancelled", "timed_out"):
            raise MiningStoreError("挖掘任务结束状态无效。")
        error = (f"{str(error.get('code', 'worker_failed'))[:80]}: "
                 f"{str(error.get('message', '挖掘任务未完成。'))[:900]}"
                 if isinstance(error, dict) else str(error)[:1000])
        with self.engine.begin() as conn:
            now = time.time()
            row = self._leased(conn, ident, token, now)
            if row is None:
                return False
            now = time.time()
            consumed = _consumed(row, now, consumed_seconds)
            status = "cancelled" if row["status"] == "cancel_requested" else status
            if consumed >= _budget(row) and status != "cancelled":
                status = "timed_out"
            self._terminal(conn, row, status, now, consumed, error=error)
            return True

    def release(self, ident, token, consumed_seconds):
        with self.engine.begin() as conn:
            now = time.time()
            row = self._leased(conn, ident, token, now)
            if row is None:
                return False
            now = time.time()
            consumed = _consumed(row, now, consumed_seconds)
            if row["status"] == "cancel_requested" or consumed >= _budget(row):
                status = "cancelled" if row["status"] == "cancel_requested" else "timed_out"
                self._terminal(conn, row, status, now, consumed)
            else:
                conn.execute(update(jobs).where(jobs.c.id == ident).values(
                    status="queued", lease_token="", lease_until=0, updated=now,
                    accounted_at=now, consumed_seconds=consumed))
            return True

    def register_worker(self, worker_id, versions):
        if not isinstance(worker_id, str) or not 1 <= len(worker_id) <= 128:
            raise MiningStoreError("工作进程标识无效。")
        versions = _json_copy(versions)
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        builder = sqlite_insert if self.engine.dialect.name == "sqlite" else pg_insert
        now = time.time()
        with self.engine.begin() as conn:
            statement = builder(workers).values(id=worker_id, versions=versions,
                                               started=now, heartbeat=now, stopped=False)
            conn.execute(statement.on_conflict_do_update(index_elements=[workers.c.id],
                         set_={"versions": versions, "started": now, "heartbeat": now,
                               "stopped": False}))

    def heartbeat_worker(self, worker_id, versions=None):
        values = {"heartbeat": time.time()}
        if versions is not None:
            values["versions"] = _json_copy(versions)
        with self.engine.begin() as conn:
            return bool(conn.execute(update(workers).where(workers.c.id == worker_id,
                                     workers.c.stopped.is_(False)).values(**values)).rowcount)

    def stop_worker(self, worker_id):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(workers).where(workers.c.id == worker_id)
                                     .values(stopped=True, heartbeat=time.time())).rowcount)

    def worker_status(self, max_age=30):
        cutoff = time.time() - min(300, max(1, max_age))
        with self.engine.connect() as conn:
            rows = conn.execute(select(workers.c.id, workers.c.versions, workers.c.started,
                                       workers.c.heartbeat).where(workers.c.stopped.is_(False),
                                      workers.c.heartbeat >= cutoff).order_by(workers.c.id)).mappings()
            active = [dict(row) for row in rows]
        return {"available": bool(active), "workers": active}
