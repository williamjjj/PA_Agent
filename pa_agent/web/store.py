"""Persistent tenant-scoped storage. SQLite for local use; PostgreSQL on Vercel."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from sqlalchemy import (
    JSON, Column, Float, Index, Integer, MetaData, String, Table, Text,
    create_engine, delete, event, insert, select, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

metadata = MetaData()
users = Table("web_users", metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(40), unique=True, nullable=False),
    Column("password", Text, nullable=False),
    Column("settings", Text, nullable=False),
    Column("created", Float, nullable=False),
    Column("busy_until", Float, nullable=False, default=0),
    Column("lease", String(64), nullable=False, default=""),
)
sessions = Table("web_sessions", metadata,
    Column("token", String(64), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("expires", Float, nullable=False),
)
items = Table("web_items", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created", Float, nullable=False),
    Column("expires", Float, nullable=False, default=0),
)
Index("web_items_owner_kind_time", items.c.user_id, items.c.kind, items.c.created)
limits = Table("web_limits", metadata,
    Column("key", String(64), primary_key=True),
    Column("count", Integer, nullable=False),
    Column("expires", Float, nullable=False),
)
hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


class ConfigurationError(RuntimeError):
    pass


class Store:
    def __init__(self, url: str, key: str):
        self.cipher = Fernet(key.encode())
        if url.startswith(("postgres://", "postgresql://")):
            url = "postgresql+psycopg://" + url.split("://", 1)[1]
        if not url.startswith(("sqlite:///", "postgresql+psycopg://")):
            raise ConfigurationError("DATABASE_URL 须使用 PostgreSQL，或本地 SQLite。")
        kw = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 20}
        else:
            kw["poolclass"] = NullPool
        self.engine = create_engine(url, **kw)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def sqlite_pragmas(dbapi, _):
                dbapi.execute("PRAGMA journal_mode=WAL")
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                from sqlalchemy import text
                conn.execute(text("SELECT pg_advisory_xact_lock(731940122)"))
            metadata.create_all(conn)

    def seal(self, data):
        return self.cipher.encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()

    def unseal(self, data):
        return json.loads(self.cipher.decrypt(data.encode()))

    def register(self, username, password):
        uid = str(uuid.uuid4())
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(users).values(
                    id=uid, username=username.casefold(), password=hasher.hash(password),
                    settings=self.seal({}), created=time.time(), busy_until=0, lease=""))
        except IntegrityError:
            return None
        return {"id": uid, "username": username.casefold()}

    def login(self, username, password):
        with self.engine.connect() as conn:
            user = conn.execute(select(users).where(users.c.username == username.casefold())).mappings().first()
        try:
            hasher.verify(user["password"] if user else DUMMY_HASH, password)
        except VerificationError:
            return None
        return {"id": user["id"], "username": user["username"]} if user else None

    def create_session(self, uid):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.expires < now))
            conn.execute(insert(sessions).values(
                token=hashlib.sha256(token.encode()).hexdigest(), user_id=uid, expires=now+28800))
        return token

    def user_for_session(self, token):
        if not token or len(token) > 100:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(select(users.c.id, users.c.username).join(
                sessions, sessions.c.user_id == users.c.id).where(
                sessions.c.token == hashlib.sha256(token.encode()).hexdigest(),
                sessions.c.expires > time.time())).mappings().first()
        return dict(row) if row else None

    def logout(self, token):
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(
                sessions.c.token == hashlib.sha256(token.encode()).hexdigest()))

    def change_password(self, uid, old, new):
        with self.engine.begin() as conn:
            row = conn.execute(select(users).where(users.c.id == uid)).mappings().one()
            try:
                hasher.verify(row["password"], old)
            except VerificationError:
                return False
            conn.execute(update(users).where(users.c.id == uid).values(password=hasher.hash(new)))
            conn.execute(delete(sessions).where(sessions.c.user_id == uid))
        return True

    def settings(self, uid):
        with self.engine.connect() as conn:
            blob = conn.execute(select(users.c.settings).where(users.c.id == uid)).scalar_one()
        return self.unseal(blob)

    def save_settings(self, uid, data):
        with self.engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == uid).values(settings=self.seal(data)))

    def rate_limit(self, name, maximum, window=900):
        now = time.time()
        key = hashlib.sha256(f"{name}:{int(now // window)}".encode()).hexdigest()
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        builder = sqlite_insert if self.engine.dialect.name == "sqlite" else pg_insert
        with self.engine.begin() as conn:
            conn.execute(delete(limits).where(limits.c.expires < now))
            statement = builder(limits).values(key=key, count=1, expires=now+window)
            count = conn.execute(statement.on_conflict_do_update(
                index_elements=[limits.c.key], set_={"count": limits.c.count+1}
            ).returning(limits.c.count)).scalar_one()
        return count <= maximum

    def acquire(self, uid):
        lease = secrets.token_hex(16)
        with self.engine.begin() as conn:
            result = conn.execute(update(users).where(
                users.c.id == uid, users.c.busy_until < time.time()
            ).values(busy_until=time.time()+270, lease=lease))
        return lease if result.rowcount else None

    def release(self, uid, lease):
        with self.engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == uid, users.c.lease == lease)
                         .values(busy_until=0, lease=""))

    def put(self, uid, kind, payload, ttl=0):
        ident = str(uuid.uuid4())
        now = time.time()
        with self.engine.begin() as conn:
            conn.execute(delete(items).where(items.c.expires > 0, items.c.expires < now))
            conn.execute(insert(items).values(id=ident, user_id=uid, kind=kind, payload=payload,
                         created=now, expires=now+ttl if ttl else 0))
        return ident

    def get(self, uid, kind, ident):
        with self.engine.connect() as conn:
            row = conn.execute(select(items).where(
                items.c.id == ident, items.c.user_id == uid, items.c.kind == kind,
                (items.c.expires == 0) | (items.c.expires > time.time())
            )).mappings().first()
        return dict(row) if row else None

    def list(self, uid, kind, offset=0, limit=50):
        with self.engine.connect() as conn:
            rows = conn.execute(select(items).where(items.c.user_id == uid, items.c.kind == kind)
                .order_by(items.c.created.desc(), items.c.id).offset(offset).limit(limit)).mappings().all()
        return [dict(row) for row in rows]

    def replace(self, uid, kind, ident, payload):
        with self.engine.begin() as conn:
            return bool(conn.execute(update(items).where(
                items.c.id == ident, items.c.user_id == uid, items.c.kind == kind
            ).values(payload=payload)).rowcount)

    def remove(self, uid, kind, ident):
        with self.engine.begin() as conn:
            return bool(conn.execute(delete(items).where(
                items.c.id == ident, items.c.user_id == uid, items.c.kind == kind
            )).rowcount)


@lru_cache(maxsize=1)
def get_store():
    url = os.getenv("DATABASE_URL", "")
    key = os.getenv("PA_WEB_ENCRYPTION_KEY", "")
    if os.getenv("VERCEL"):
        if not url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")) or not key:
            raise ConfigurationError("请配置 PostgreSQL DATABASE_URL 和 PA_WEB_ENCRYPTION_KEY。")
    else:
        root = Path(os.getenv("PA_WEB_DATA_DIR", ".pa-agent-web"))
        root.mkdir(parents=True, exist_ok=True)
        url = url or "sqlite:///" + str((root / "accounts.sqlite3").resolve())
        if not key:
            keyfile = root / "encryption.key"
            try:
                with keyfile.open("xb") as fp:
                    fp.write(Fernet.generate_key())
                keyfile.chmod(0o600)
            except FileExistsError:
                pass
            key = keyfile.read_text().strip()
    try:
        return Store(url, key)
    except (ValueError, TypeError):
        raise ConfigurationError("PA_WEB_ENCRYPTION_KEY 必须为有效 Fernet 密钥。") from None
