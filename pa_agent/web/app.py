from __future__ import annotations

import asyncio
import hmac
import json
import os
import queue
import time
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from pa_agent.util.threading import CancelToken
from pa_agent.web.data import MAX_BODY, ImportRequest, chart_data, demo_csv, parse_csv
from pa_agent.web.settings import PROMPTS, WebSettings, merge_settings
from pa_agent.web.sources import SourceRequest, catalog, fetch_frame, incremental_count, restore_frame
from pa_agent.web.store import ConfigurationError, get_store

ROOT = Path(__file__).resolve().parents[2]
COOKIE = "pa_session"
app = FastAPI(title="PA Agent Web", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security(request: Request, call_next):
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if request.headers.get("x-pa-request") != "1" or (origin and origin != expected):
            return JSONResponse({"detail": "请求来源无效，请刷新页面重试。"}, status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return JSONResponse({"detail": "请求超过 600 KB 上限。"}, status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers.update({
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
        "X-Frame-Options": "SAMEORIGIN", "Cache-Control": "no-store",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    })
    return response


@app.exception_handler(RequestValidationError)
async def invalid_input(request, exc):
    # Validation errors can contain password/API-key input. Never serialize exc.errors().
    return JSONResponse({"detail": "输入格式无效，请检查必填项、长度和取值范围。"}, status_code=422)


@app.exception_handler(ConfigurationError)
async def configuration_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


def db():
    try:
        return get_store()
    except ConfigurationError:
        raise
    except Exception:
        raise HTTPException(503, "账户数据库暂时不可用，请检查数据库连接。") from None


def user(request: Request):
    current = db().user_for_session(request.cookies.get(COOKIE, ""))
    if not current:
        raise HTTPException(401, "请先登录自己的账户。")
    return current


def settings_for(uid):
    return WebSettings.model_validate(db().settings(uid))


def owned(uid, kind, ident):
    row = db().get(uid, kind, ident)
    if not row:
        raise HTTPException(404, "记录不存在或已过期。")
    return row


def registration_open():
    return bool(os.getenv("PA_WEB_INVITE_CODE")) or os.getenv("PA_WEB_REGISTRATION", "0" if os.getenv("VERCEL") else "1") == "1"


def session_response(request, current):
    response = JSONResponse({"user": current})
    response.set_cookie(COOKIE, db().create_session(current["id"]), max_age=28800,
                        httponly=True, secure=bool(os.getenv("VERCEL")) or request.url.scheme == "https",
                        samesite="lax", path="/")
    return response


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,40}$")
    password: str = Field(min_length=12, max_length=128)
    invite_code: str = Field(default="", max_length=256)


def login_limit(request, username):
    store = db()
    ip = request.client.host if request.client else "unknown"
    if not store.rate_limit("auth-ip:"+ip, 40) or not store.rate_limit("auth-user:"+username.casefold(), 12):
        raise HTTPException(429, "登录或注册过于频繁，请 15 分钟后重试。")


@app.get("/")
def index():
    return FileResponse(ROOT / "public" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "app": "PA Agent Web"}


@app.get("/api/status")
def status(request: Request):
    try:
        store = db()
        current = store.user_for_session(request.cookies.get(COOKIE, ""))
        return {"configured": True, "authenticated": bool(current), "user": current,
                "registration_open": registration_open()}
    except (HTTPException, ConfigurationError):
        return {"configured": False, "authenticated": False, "user": None,
                "registration_open": False, "message": "请先配置持久数据库和加密密钥。"}


@app.post("/api/register")
def register(data: Credentials, request: Request):
    if not registration_open():
        raise HTTPException(403, "注册尚未开放，请联系管理员。")
    login_limit(request, data.username)
    invite = os.getenv("PA_WEB_INVITE_CODE", "")
    if invite and not hmac.compare_digest(invite.encode(), data.invite_code.encode()):
        raise HTTPException(403, "邀请码不正确。")
    current = db().register(data.username, data.password)
    if not current:
        raise HTTPException(409, "用户名不可用。")
    return session_response(request, current)


@app.post("/api/session")
def login(data: Credentials, request: Request):
    login_limit(request, data.username)
    current = db().login(data.username, data.password)
    if not current:
        raise HTTPException(401, "用户名或密码不正确。")
    return session_response(request, current)


@app.post("/api/logout")
def logout(request: Request):
    db().logout(request.cookies.get(COOKIE, ""))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


@app.post("/api/password")
def password(data: PasswordChange, request: Request, current=Depends(user)):
    if not db().rate_limit("password:"+current["id"], 10):
        raise HTTPException(429, "修改密码过于频繁，请稍后重试。")
    if not db().change_password(current["id"], data.old_password, data.new_password):
        raise HTTPException(401, "原密码不正确。")
    return session_response(request, current)


@app.get("/api/settings")
def read_settings(current=Depends(user)):
    return settings_for(current["id"]).public()


class SettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: dict
    clear_secrets: list[str] = Field(default_factory=list, max_length=6)


@app.put("/api/settings")
def save_settings(data: SettingsChange, current=Depends(user)):
    try:
        updated = merge_settings(db().settings(current["id"]), data.settings, data.clear_secrets)
    except ValueError:
        raise HTTPException(422, "设置无效，请检查 HTTPS 模型地址、提示词和取值范围。") from None
    db().save_settings(current["id"], updated.model_dump())
    return updated.public()


@app.get("/api/prompts")
def prompts(current=Depends(user)):
    overrides = settings_for(current["id"]).prompt_overrides
    return [{"name": p.name, "content": overrides.get(p.name, p.read_text(encoding="utf-8")),
             "modified": p.name in overrides} for p in sorted(PROMPTS.glob("*.txt"))]


@app.get("/api/sources")
def sources():
    return {"sources": catalog()}


def checked_frame(data):
    try:
        return parse_csv(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/api/demo")
def demo():
    csv_text = demo_csv()
    return {**chart_data(parse_csv(ImportRequest(csv=csv_text, source="demo"))),
            "csv": csv_text, "source": "demo"}


@app.post("/api/import")
def import_csv(data: ImportRequest):
    return {**chart_data(checked_frame(data)), "source": data.source}


@app.post("/api/market")
def market(data: SourceRequest, current=Depends(user)):
    store, uid = db(), current["id"]
    if not store.rate_limit("market:"+uid, 60):
        raise HTTPException(429, "数据请求过于频繁，请稍后重试。")
    try:
        if data.source == "mt5":
            rows = store.list(uid, "bridge", limit=1)
            if not rows or rows[0]["expires"] <= time.time():
                raise HTTPException(422, "尚未收到 MT5 数据或桥接已离线，请运行本地 MT5 桥接程序。")
            bridge = rows[0]["payload"]
            if (bridge["symbol"], bridge["timeframe"]) != (data.symbol, data.timeframe) or len(bridge["bars"]) < data.count:
                raise HTTPException(422, "MT5 桥接品种、周期或 K 线数量与当前选择不一致。")
            frame = restore_frame({**bridge, "bars": bridge["bars"][-data.count:]})
        else:
            frame = fetch_frame(data, settings_for(uid))
    except HTTPException:
        raise
    except (ValueError, ImportError):
        raise HTTPException(422, "数据不可用，请检查数据源、品种、周期及个人凭据。") from None
    except Exception:
        raise HTTPException(502, "数据源暂时不可用或无权访问该行情，请稍后重试。") from None
    chart = {**chart_data(frame), "source": data.source, "request": data.model_dump()}
    ident = store.put(uid, "snapshot", chart, ttl=3600)
    return {**chart, "snapshot_id": ident}




@app.put("/api/bridge")
def bridge_upload(data: ImportRequest, current=Depends(user)):
    uid, store = current["id"], db()
    if not store.rate_limit("bridge:"+uid, 60):
        raise HTTPException(429, "桥接更新过于频繁，请稍后重试。")
    chart = {**chart_data(checked_frame(data)), "source": "mt5"}
    for row in store.list(uid, "bridge"):
        store.remove(uid, "bridge", row["id"])
    ident = store.put(uid, "bridge", chart, ttl=180)
    return {"id": ident, "bars": len(chart["bars"])}


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str | None = Field(default=None, max_length=36)
    data: ImportRequest | None = None
    previous_id: str | None = Field(default=None, max_length=36)


def streaming_job(request, current, action):
    store, uid = db(), current["id"]
    lease = store.acquire(uid)
    if not lease:
        raise HTTPException(409, "账户已有分析或追问正在进行，请等待结束。")

    async def events():
        channel = queue.Queue(maxsize=512)
        cancel = CancelToken()
        def emit(value):
            if cancel.is_set():
                return
            try:
                channel.put(value, timeout=2)
            except queue.Full:
                cancel.set()
                raise RuntimeError("客户端读取速度过慢。") from None
        def run():
            try:
                action(cancel, emit)
            except Exception:
                emit({"event": "error", "message": "请求未完成，请检查模型配置、额度、上下文长度或稍后重试。"})
            finally:
                store.release(uid, lease)
        worker = asyncio.create_task(asyncio.to_thread(run))
        last_heartbeat = time.monotonic()
        try:
            while not worker.done() or not channel.empty():
                if await request.is_disconnected():
                    break
                try:
                    item = channel.get_nowait()
                except queue.Empty:
                    if time.monotonic() - last_heartbeat >= 5:
                        yield ": heartbeat\n\n"
                        last_heartbeat = time.monotonic()
                    await asyncio.sleep(0.1)
                    continue
                yield "data: " + json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n\n"
        finally:
            cancel.set()
            # All paid work is tied to this response; do not spawn post-response jobs.
            await asyncio.shield(worker)
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})


@app.post("/api/analyze")
def run_analysis(data: AnalysisRequest, request: Request, current=Depends(user)):
    uid, store = current["id"], db()
    cfg = settings_for(uid)
    if not cfg.api_key:
        raise HTTPException(422, "请先在个人设置中保存模型 API Key。")
    if bool(data.snapshot_id) == bool(data.data):
        raise HTTPException(422, "请选择一份行情快照或导入 CSV。")
    if data.snapshot_id:
        chart = owned(uid, "snapshot", data.snapshot_id)["payload"]
        frame = restore_frame(chart)
    else:
        frame = checked_frame(data.data)
        chart = {**chart_data(frame), "source": data.data.source}
    previous, count = None, None
    if data.previous_id:
        saved = owned(uid, "record", data.previous_id)["payload"]
        old_chart = saved["chart"]
        same_market = (old_chart["source"] == chart["source"] and
                       old_chart.get("request", {}).get("exchange") == chart.get("request", {}).get("exchange"))
        if same_market and saved["result"]["status"] == "complete":
            count = incremental_count(frame, old_chart, cfg.incremental_max_new_bars)
            previous = saved["record"] if count else None
    def action(cancel, emit):
        from pa_agent.web.analysis import analyze
        result, raw = analyze(frame, cfg, store, uid, cancel, emit, previous, count)
        result["meta"]["source"] = chart["source"]
        payload = {"result": result, "record": raw, "chart": chart, "chat": []}
        ident = store.put(uid, "record", payload)
        emit({"event": "result", "data": {**result, "id": ident}})
        if cfg.notify_enabled and result["status"] == "complete" and not cancel.is_set():
            from pa_agent.web.notifications import notify
            notify(cfg, result)
    return streaming_job(request, current, action)


@app.get("/api/records")
def records(offset: int = Query(0, ge=0), current=Depends(user)):
    return [{"id": row["id"], "created": row["created"],
             **{key: row["payload"]["result"].get(key) for key in ("meta", "status", "usage", "error")}}
            for row in db().list(current["id"], "record", offset, 25)]


@app.get("/api/records/{ident}")
def record(ident: str, current=Depends(user)):
    row = owned(current["id"], "record", ident)
    return {"id": ident, **row["payload"]}


@app.delete("/api/records/{ident}")
def delete_record(ident: str, current=Depends(user)):
    # Prevent a concurrent follow-up from writing a deleted record back.
    store, uid = db(), current["id"]
    if not store.remove(uid, "record", ident):
        raise HTTPException(404, "记录不存在。")
    return {"ok": True}


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=6000)


@app.post("/api/records/{ident}/chat")
def chat(ident: str, data: ChatRequest, request: Request, current=Depends(user)):
    store, uid = db(), current["id"]
    owned(uid, "record", ident)
    cfg = settings_for(uid)
    if not cfg.api_key:
        raise HTTPException(422, "请先配置模型 API Key。")
    def action(cancel, emit):
        from pa_agent.web.analysis import followup
        payload = owned(uid, "record", ident)["payload"]
        if len(payload.get("chat", [])) >= 100 or sum(len(json.dumps(turn, ensure_ascii=False)) for turn in payload.get("chat", [])) > 500000:
            raise ValueError("本记录已达追问轮数或内容上限，请开始新分析。")
        turn = followup(payload, data.question, cfg, cancel, emit)
        payload.setdefault("chat", []).append(turn)
        if not store.replace(uid, "record", ident, payload):
            raise ValueError("记录已被删除。")
        emit({"event": "chat_result", "data": turn})
    return streaming_job(request, current, action)


class ExperienceRequest(BaseModel):
    record_id: str = Field(max_length=36)
    case_type: Literal["success", "failure"]
    cycle_position: Literal["spike", "micro_channel", "tight_channel", "normal_channel", "broad_channel", "trading_range", "trending_tr", "extreme_tr", "unknown"]
    notes: str = Field(min_length=1, max_length=6000)


@app.get("/api/experience")
def experiences(offset: int = Query(0, ge=0), current=Depends(user)):
    rows = db().list(current["id"], "experience", offset=offset, limit=25)
    return [{**row, "payload": {**row["payload"], "content": {"notes": row["payload"]["content"]["notes"]}}}
            for row in rows]


@app.get("/api/experience/{ident}")
def experience_detail(ident: str, current=Depends(user)):
    return owned(current["id"], "experience", ident)


@app.post("/api/experience")
def experience(data: ExperienceRequest, current=Depends(user)):
    uid, store = current["id"], db()
    if store.count(uid, "experience") >= 500:
        raise HTTPException(409, "经验库已达 500 条，请先删除旧案例。")
    payload = owned(uid, "record", data.record_id)["payload"]
    if payload["result"]["status"] != "complete":
        raise HTTPException(422, "只可将已完成的分析加入经验库。")
    ident = store.put(uid, "experience", {
        "record_id": data.record_id, "case_type": data.case_type, "cycle_position": data.cycle_position,
        "content": {"notes": data.notes, "diagnosis": payload["result"]["stage1"],
                    "decision": payload["result"]["stage2"]}})
    return {"id": ident}


@app.delete("/api/experience/{ident}")
def delete_experience(ident: str, current=Depends(user)):
    if not db().remove(current["id"], "experience", ident):
        raise HTTPException(404, "案例不存在。")
    return {"ok": True}


# Vercel serves public assets directly at /. This mount provides identical local URLs.
app.mount("/", StaticFiles(directory=ROOT / "public"), name="public")
