from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pa_agent.util.threading import CancelToken
from pa_agent.web.data import MAX_BODY, ImportRequest, chart_data, demo_csv, parse_csv

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="PA Agent Web", docs_url=None, redoc_url=None, openapi_url=None)
COOKIE = "pa_session"
slots = asyncio.Semaphore(2)


def access_secret():
    value = os.environ.get("PA_WEB_ACCESS_SECRET", "")
    return value if len(value) >= 24 else ""


def signed(payload: str) -> str:
    return hmac.new(access_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def authenticated(request: Request) -> bool:
    if not access_secret():
        return False
    try:
        payload, signature = request.cookies.get(COOKIE, "").rsplit(".", 1)
        expires, nonce = payload.split(".")
        return time.time() < int(expires) <= time.time() + 8 * 3600 and len(nonce) == 32 and hmac.compare_digest(signature, signed(payload))
    except (ValueError, TypeError):
        return False


@app.middleware("http")
async def security(request: Request, call_next):
    if request.method == "POST":
        if request.headers.get("x-pa-request") != "1":
            return JSONResponse({"detail": "请求来源无效。"}, status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return JSONResponse({"detail": "文件过大，上限为 500 KB。"}, status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-Frame-Options": "SAMEORIGIN",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Strict-Transport-Security": "max-age=63072000",
        "Cache-Control": "no-store",
    })
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    return JSONResponse({"detail": "输入格式无效，请检查品种、周期及 CSV 内容。"}, status_code=422)


@app.get("/")
async def index():
    return FileResponse(ROOT / "public" / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "PA Agent Web"}


@app.get("/api/status")
async def status(request: Request):
    return {"configured": bool(access_secret()), "authenticated": authenticated(request),
            "model": "deepseek/deepseek-v4.1-flash", "gateway": "连接状态将在实际调用时验证"}


class LoginRequest(BaseModel):
    secret: str = Field(min_length=1, max_length=256)


@app.post("/api/session")
async def login(data: LoginRequest):
    secret = access_secret()
    if not secret:
        raise HTTPException(503, "管理员尚未配置访问口令；图表和 CSV 导入仍可使用。")
    if not hmac.compare_digest(hashlib.sha256(data.secret.encode()).digest(), hashlib.sha256(secret.encode()).digest()):
        await asyncio.sleep(0.5)
        raise HTTPException(401, "访问口令不正确。")
    payload = f"{int(time.time()) + 8 * 3600}.{secrets.token_hex(16)}"
    response = JSONResponse({"ok": True})
    response.set_cookie(COOKIE, f"{payload}.{signed(payload)}", max_age=8 * 3600, httponly=True, secure=True, samesite="none", path="/")
    return response


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="none")
    return response


def checked_frame(data):
    try:
        return parse_csv(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/api/demo")
async def demo():
    text = demo_csv()
    data = ImportRequest(csv=text, source="demo")
    return {**chart_data(parse_csv(data)), "csv": text, "source": "demo"}


@app.post("/api/import")
async def import_csv(data: ImportRequest):
    return {**chart_data(checked_frame(data)), "source": data.source}


@app.post("/api/analyze")
async def run_analysis(data: ImportRequest, request: Request):
    if not authenticated(request):
        raise HTTPException(401, "请先使用管理员访问口令解锁 AI 分析。")
    frame = checked_frame(data)
    if slots.locked():
        raise HTTPException(429, "当前实例分析繁忙，请稍后重试。")
    await slots.acquire()

    async def events():
        from pa_agent.web.analysis import analyze
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        cancel = CancelToken()

        def emit(event):
            loop.call_soon_threadsafe(queue.put_nowait, {"event": "stage", "stage": event.name})

        def execute():
            try:
                result = analyze(frame, cancel, emit)
                result["meta"]["source"] = data.source
                loop.call_soon_threadsafe(queue.put_nowait, {"event": "result", "data": result})
            except TimeoutError:
                loop.call_soon_threadsafe(queue.put_nowait, {"event": "error", "message": "模型响应超时，请稍后重试或减少 K 线数量。"})
            except Exception:
                loop.call_soon_threadsafe(queue.put_nowait, {"event": "error", "message": "模型服务不可用或响应无法处理，请检查 Gateway 配额与运行日志。"})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        worker = asyncio.create_task(asyncio.to_thread(execute))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=5)
                except TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": heartbeat\n\n"
                    continue
                if item is None:
                    break
                yield "data: " + json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n\n"
        finally:
            cancel.set()
            # Keep the worker tied to this request; no post-response background job.
            try:
                await asyncio.shield(worker)
            finally:
                slots.release()

    return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


app.mount("/static", StaticFiles(directory=ROOT / "public"), name="static")
