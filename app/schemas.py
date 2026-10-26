"""Pydantic 请求/响应模型。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    configured: bool = False
    certificate_configured: bool = False
    message_secret_configured: bool = False
    version: str = "1.0.0"


class AccountBook(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    # 原始字段透传
    extra: dict[str, Any] = Field(default_factory=dict)


class AccountBooksResponse(BaseModel):
    total: int
    books: list[AccountBook] = Field(default_factory=list)


class BalanceSheetResponse(BaseModel):
    book_id: str
    period: str
    data: Any = None


class DetailLedgerResponse(BaseModel):
    book_id: str
    start_period: str
    end_period: str
    data: Any = None
