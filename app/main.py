"""畅捷通易代账连接器 - FastAPI 入口。

提供：
- 健康检查（验证回调可达，不依赖凭证）
- OAuth 授权入口 + 回调
- 帐套列表 / 资产负债表 / 明细账

鉴权：服务内部持有 org 级 token，接口以内部调用为主；
所有可写/敏感操作不做公网暴露，仅提供只读拉取。
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse

from .chanjet_client import ChanjetClient, ChanjetError
from .config import get_settings
from .logger import get_logger
from .schemas import (
    AccountBooksResponse,
    BalanceSheetResponse,
    CallbackResponse,
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


def _valid_token() -> str:
    """取可用 token，必要时刷新。"""
    token = store.get_access_token()
    if not token:
        raise HTTPException(status_code=401, detail="尚未授权，请先走 OAuth 授权流程")
    if store.is_access_expiring(settings.token_refresh_ahead_seconds):
        refresh_token = store.get_refresh_token()
        if refresh_token:
            try:
                result = client.refresh(refresh_token)
                store.save_token(result)
                token = store.get_access_token()
                log.info("token refreshed")
            except ChanjetError as exc:
                log.warning("token refresh failed: %s", exc)
                # 刷新失败但 token 可能仍可用，继续用旧 token 尝试
    return token


def _period(period: str) -> str:
    if not _PERIOD_RE.match(period):
        raise HTTPException(status_code=422, detail="period 必须为 yyyyMM 格式，如 202610")
    return period


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("chanjet-connector started, configured=%s", settings.configured)
    yield
    try:
        client._http.close()
    except Exception:
        pass


app = FastAPI(title="畅捷通易代账连接器", version="1.0.0", lifespan=lifespan)


@app.get("/healthz", response_model=HealthResponse)
async def healthz():
    return HealthResponse(configured=settings.configured)


@app.get("/api/v1/auth/authorize/chanjet")
async def authorize():
    """跳转畅捷通授权页。"""
    _require_configured()
    url = client.build_authorize_url()
    return RedirectResponse(url=url)


@app.get("/api/v1/auth/callback/chanjet", response_model=CallbackResponse)
async def callback(
    code: Optional[str] = Query(default=None),
    orgId: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    """授权回调：用 code 换 token 并落库。"""
    _require_configured()
    if error:
        raise HTTPException(status_code=400, detail=f"授权被拒绝: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="缺少 code 参数")
    try:
        result = client.exchange_code(code)
    except ChanjetError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # 回调可能带 orgId，合并进结果，便于后续企业维度区分
    if orgId:
        result.setdefault("org_id", orgId)
    store.save_token(result)
    log.info("oauth callback success, user_id=%s org_id=%s",
             result.get("user_id"), result.get("org_id"))
    return CallbackResponse(
        ok=True,
        user_id=result.get("user_id"),
        org_id=result.get("org_id"),
        message="授权成功",
    )


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
