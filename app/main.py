"""畅捷通易代账连接器 - FastAPI 入口。

提供：
- 健康检查（验证服务可达，不依赖凭证）
- 消息接收（解密平台推送的 appTicket，落库）
- 触发 appTicket 推送（联调辅助）
- 帐套列表 / 资产负债表 / 明细账

鉴权：自建应用模式。服务内部持有 openToken（appTicket + 软证书换得），
接口以内部调用为主；消息接收地址由 openresty 反代暴露给畅捷通平台。
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .chanjet_client import ChanjetClient, ChanjetError
from .config import get_settings
from .logger import get_logger
from .schemas import (
    AccountBooksResponse,
    BalanceSheetResponse,
    DetailLedgerResponse,
    HealthResponse,
)
from .token_store import TokenStore

log = get_logger("chanjet.app")

settings = get_settings()
store = TokenStore(settings.db_path)
client = ChanjetClient(settings)

_PERIOD_RE = re.compile(r"^\d{4}(0[1-9]|1[0-2])$")


def _require_configured() -> None:
    if not settings.configured:
        raise HTTPException(status_code=503, detail="未配置 CHANJET_APP_KEY/CHANJET_APP_SECRET")


def _generate_token() -> str:
    """用最新 appTicket + 软证书生成 openToken 并落库。"""
    if not settings.certificate_configured:
        raise HTTPException(status_code=503, detail="尚未配置软证书 CHANJET_CERTIFICATE")
    app_ticket = store.get_app_ticket()
    if not app_ticket:
        raise HTTPException(
            status_code=503,
            detail="尚未收到 appTicket，请确认消息接收地址已配置并触发推送",
        )
    try:
        value = client.generate_token(app_ticket, settings.certificate)
        store.save_generated_token(value)
        log.info("self-built token generated, user_id=%s org_id=%s",
                 value.get("userId"), value.get("orgId"))
        return value["accessToken"]
    except ChanjetError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _valid_token() -> str:
    """取可用 token，必要时用最新 appTicket 重新生成。"""
    token = store.get_access_token()
    if not token or store.is_access_expiring(settings.token_refresh_ahead_seconds):
        token = _generate_token()
    return token


def _period(period: str) -> str:
    if not _PERIOD_RE.match(period):
        raise HTTPException(status_code=422, detail="period 必须为 yyyyMM 格式，如 202610")
    return period


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "chanjet-connector started, configured=%s certificate=%s message_secret=%s",
        settings.configured, settings.certificate_configured, settings.message_secret_configured,
    )
    yield
    try:
        client._http.close()
    except Exception:
        pass


app = FastAPI(title="畅捷通易代账连接器", version="1.0.0", lifespan=lifespan)


@app.get("/healthz", response_model=HealthResponse)
async def healthz():
    return HealthResponse(
        configured=settings.configured,
        certificate_configured=settings.certificate_configured,
        message_secret_configured=settings.message_secret_configured,
    )


@app.post("/api/v1/chanjet/message")
async def receive_message(request: Request):
    """接收畅捷通平台推送（appTicket / 地址校验 APP_TEST），AES 解密后落库。

    平台要求 1s 内返回 {"result":"success"}，否则按阶梯重试（最多 20 次）。
    """
    try:
        body = await request.json()
    except Exception:
        # 非 JSON 直接 ack，避免重试风暴
        return JSONResponse({"result": "success"})

    if not settings.message_secret_configured:
        # 无法解密，返回失败让平台校验地址时发现密钥未就绪
        log.error("receive_message: CHANJET_MESSAGE_SECRET 未配置，无法解密")
        return JSONResponse({"result": "fail"}, status_code=500)

    encrypt_msg = body.get("encryptMsg") if isinstance(body, dict) else None
    if not encrypt_msg:
        return JSONResponse({"result": "success"})

    try:
        msg = client.decrypt_message(encrypt_msg, settings.message_secret)
    except ChanjetError as exc:
        log.error("receive_message decrypt failed: %s", exc)
        # 返回失败，暴露消息密钥不匹配，让平台重试/标记地址不可用
        return JSONResponse({"result": "fail"}, status_code=500)

    log.info("receive_message decrypted: %s", json.dumps(msg, ensure_ascii=False))
    app_ticket = client.extract_app_ticket(msg)
    if app_ticket:
        store.save_app_ticket(app_ticket)
        log.info("appTicket 已更新 (msgType=%s)", msg.get("msgType"))
    return JSONResponse({"result": "success"})


@app.post("/api/v1/chanjet/trigger-ticket")
async def trigger_ticket():
    """立即触发平台推送 appTicket（联调辅助，内部调用）。"""
    _require_configured()
    try:
        client.trigger_app_ticket_push()
    except ChanjetError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"result": "success", "message": "已触发 appTicket 推送"}


@app.get("/api/v1/chanjet/account-books", response_model=AccountBooksResponse)
async def account_books():
    """帐套列表。"""
    _require_configured()
    token = _valid_token()
    try:
        books = client.list_account_books(token)
    except ChanjetError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    normalized = [
        {
            "id": (b.get("id") or b.get("bookId") or b.get("accountBookId")),
            "name": (b.get("name") or b.get("bookName") or b.get("accountBookName")),
            "extra": b,
        }
        for b in books
    ] if isinstance(books, list) else []
    return AccountBooksResponse(total=len(normalized), books=normalized)


@app.get("/api/v1/chanjet/balance-sheet/{book_id}", response_model=BalanceSheetResponse)
async def balance_sheet(book_id: str, period: str = Query(...)):
    """资产负债表。"""
    _require_configured()
    _period(period)
    token = _valid_token()
    try:
        data = client.get_balance_sheet(token, book_id, period)
    except ChanjetError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return BalanceSheetResponse(book_id=book_id, period=period, data=data)


@app.get("/api/v1/chanjet/detail-ledger/{book_id}", response_model=DetailLedgerResponse)
async def detail_ledger(
    book_id: str,
    startPeriod: str = Query(...),
    endPeriod: str = Query(...),
    pageSize: int = Query(default=100, ge=1, le=500),
    page: int = Query(default=1, ge=1),
):
    """明细账（分页）。"""
    _require_configured()
    _period(startPeriod)
    _period(endPeriod)
    token = _valid_token()
    try:
        data = client.get_detail_ledger(
            token, book_id, startPeriod, endPeriod,
            page_size=pageSize, page=page,
        )
    except ChanjetError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DetailLedgerResponse(
        book_id=book_id, start_period=startPeriod, end_period=endPeriod, data=data
    )


@app.exception_handler(HTTPException)
async def http_exc_handler(request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
