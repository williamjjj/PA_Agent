"""Persistent tenant-scoped storage. SQLite for local use; PostgreSQL on Vercel."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from sqlalchemy import (
    JSON, Boolean, Column, Float, ForeignKey, Index, Integer, MetaData, String, Table, Text,
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
admins = Table("web_admins", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(36), ForeignKey("web_users.id"), unique=True, nullable=False),
)
registration_policy = Table("web_registration_policy", metadata,
    Column("id", Integer, primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("code", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
    # A permanent marker: revoking the role must not recreate it on restart.
    Column("admin_initialized", Boolean, nullable=False, default=False),
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


class RegistrationDenied(ValueError):
    pass


@dataclass(frozen=True)
class AuthenticatedUser:
    """A verified credential generation, kept private until session issuance.

    Argon2 uses a fresh salt for each password change, so the stored hash also
    identifies the exact generation that was verified. Never return this object
    or its password hash from an API response.
    """

    id: str
    username: str
    password_hash: str = field(repr=False)
    is_admin: bool = False

    def public(self):
        return {"id": self.id, "username": self.username, "is_admin": self.is_admin}


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
                dbapi.execute("PRAGMA foreign_keys=ON")
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                from sqlalchemy import text
                conn.execute(text("SELECT pg_advisory_xact_lock(731940122)"))
            else:
                # SQLite's deferred transactions do not lock schema inspection.
                # Serialize cold starts before create_all and bootstrap reads.
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            metadata.create_all(conn)
            from pa_agent.web.research_store import create_tables
            create_tables(conn)
            from pa_agent.web.mining_store import create_tables as create_mining_tables
            create_mining_tables(conn)
            self._initialize_accounts(conn)

    def _initialize_accounts(self, conn):
        policy = conn.execute(select(registration_policy).where(
            registration_policy.c.id == 1)).mappings().first()
        if policy is None:
            code = os.getenv("PA_WEB_INVITE_CODE", "")
            enabled = bool(code) or os.getenv(
                "PA_WEB_REGISTRATION", "0" if os.getenv("VERCEL") else "1") == "1"
            conn.execute(insert(registration_policy).values(
                id=1, enabled=enabled, code=self.seal(code), updated_at=time.time(),
                admin_initialized=False))
        elif policy["admin_initialized"]:
            return

        username = os.getenv("PA_WEB_ADMIN_USERNAME", "")
        password = os.getenv("PA_WEB_ADMIN_PASSWORD", "")
        if not username and not password:
            return
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username) or not 12 <= len(password) <= 128:
            raise ConfigurationError("请同时配置有效的管理员用户名和 12–128 字符的初始密码。")
        username = username.casefold()
        if conn.execute(select(users.c.id).where(users.c.username == username)).first():
            raise ConfigurationError("管理员用户名已被使用，请为独立管理员配置其他用户名。")
        uid = str(uuid.uuid4())
        conn.execute(insert(users).values(
            id=uid, username=username, password=hasher.hash(password),
            settings=self.seal({}), created=time.time(), busy_until=0, lease=""))
        conn.execute(insert(admins).values(id=1, user_id=uid))
        conn.execute(update(registration_policy).where(registration_policy.c.id == 1)
                     .values(admin_initialized=True))

    @staticmethod
    def _admin_expression():
        return select(admins.c.user_id).where(admins.c.user_id == users.c.id).exists().label("is_admin")

    @staticmethod
    def _lock_registration(conn):
        # A write lock works on both PostgreSQL and SQLite. Registration and
        # rotation/revocation hold it until their whole transaction commits.
        conn.execute(update(registration_policy).where(registration_policy.c.id == 1)
                     .values(updated_at=registration_policy.c.updated_at))
        return conn.execute(select(registration_policy).where(
            registration_policy.c.id == 1)).mappings().one()

    def is_admin(self, uid):
        with self.engine.connect() as conn:
            return conn.execute(select(admins.c.user_id).where(admins.c.user_id == uid)).first() is not None

    def registration_open(self):
        with self.engine.connect() as conn:
            return bool(conn.execute(select(registration_policy.c.enabled).where(
                registration_policy.c.id == 1)).scalar_one())

    def invite_policy(self):
        with self.engine.connect() as conn:
            policy = conn.execute(select(registration_policy).where(
                registration_policy.c.id == 1)).mappings().one()
        return {"enabled": bool(policy["enabled"]), "code": self.unseal(policy["code"]),
                "updated_at": policy["updated_at"]}

    def set_invite_policy(self, enabled, code):
        if not isinstance(code, str) or len(code) > 256 or (enabled and len(code) < 12):
            raise ValueError("启用注册时，邀请码须为 12–256 个字符。")
        with self.engine.begin() as conn:
            self._lock_registration(conn)
            now = time.time()
            conn.execute(update(registration_policy).where(registration_policy.c.id == 1).values(
                enabled=enabled, code=self.seal(code), updated_at=now))
        return {"enabled": bool(enabled), "code": code, "updated_at": now}

    def seal(self, data):
        return self.cipher.encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()

    def unseal(self, data):
        return json.loads(self.cipher.decrypt(data.encode()))

    def register(self, username, password, invite_code=""):
        uid = str(uuid.uuid4())
        password_hash = hasher.hash(password)
        try:
            with self.engine.begin() as conn:
                policy = self._lock_registration(conn)
                if not policy["enabled"]:
                    raise RegistrationDenied("注册尚未开放，请联系管理员。")
                code = self.unseal(policy["code"])
                if code and not hmac.compare_digest(code.encode(), invite_code.encode()):
                    raise RegistrationDenied("邀请码不正确。")
                conn.execute(insert(users).values(
                    id=uid, username=username.casefold(), password=password_hash,
                    settings=self.seal({}), created=time.time(), busy_until=0, lease=""))
        except IntegrityError:
            return None
        return AuthenticatedUser(uid, username.casefold(), password_hash)

    def login(self, username, password):
        with self.engine.connect() as conn:
            user = conn.execute(select(users, self._admin_expression()).where(
                users.c.username == username.casefold())).mappings().first()
        try:
            hasher.verify(user["password"] if user else DUMMY_HASH, password)
        except VerificationError:
            return None
        return AuthenticatedUser(user["id"], user["username"], user["password"], user["is_admin"]) if user else None

    def create_session(self, current: AuthenticatedUser):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.engine.begin() as conn:
            # The conditional write locks this account until insertion commits.
            # A concurrent password change either invalidates this generation
            # first, or runs afterwards and deletes the newly issued session.
            matched = conn.execute(update(users).where(
                users.c.id == current.id, users.c.password == current.password_hash
            ).values(password=users.c.password))
            if not matched.rowcount:
                return None
            conn.execute(delete(sessions).where(sessions.c.expires < now))
            conn.execute(insert(sessions).values(
                token=hashlib.sha256(token.encode()).hexdigest(), user_id=current.id, expires=now+28800))
        return token

    def user_for_session(self, token):
        if not token or len(token) > 100:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(select(users.c.id, users.c.username, self._admin_expression()).join(
                sessions, sessions.c.user_id == users.c.id).where(
                sessions.c.token == hashlib.sha256(token.encode()).hexdigest(),
                sessions.c.expires > time.time())).mappings().first()
        return dict(row) if row else None

    def logout(self, token):
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(
                sessions.c.token == hashlib.sha256(token.encode()).hexdigest()))

    def change_password(self, uid, old, new):
        with self.engine.connect() as conn:
            row = conn.execute(select(users).where(users.c.id == uid)).mappings().one()
        try:
            hasher.verify(row["password"], old)
        except VerificationError:
            return None
        password_hash = hasher.hash(new)
        with self.engine.begin() as conn:
            changed = conn.execute(update(users).where(
                users.c.id == uid, users.c.password == row["password"]
            ).values(password=password_hash))
            if not changed.rowcount:
                return None
            conn.execute(delete(sessions).where(sessions.c.user_id == uid))
        return AuthenticatedUser(uid, row["username"], password_hash, self.is_admin(uid))

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


    def experiences_for_cycle(self, uid, cycle):
        with self.engine.connect() as conn:
            rows = conn.execute(select(items).where(
                items.c.user_id == uid, items.c.kind == "experience",
                items.c.payload["cycle_position"].as_string() == cycle
            ).order_by(items.c.created.desc()).limit(5)).mappings().all()
        return [dict(row) for row in rows]

    def count(self, uid, kind):
        from sqlalchemy import func
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(items).where(
                items.c.user_id == uid, items.c.kind == kind)).scalar_one()


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
