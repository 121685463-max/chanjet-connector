"""token / appTicket 持久化存储。

使用 SQLite（单文件、零运维），存 token / appTicket / 到期时间 / 企业信息。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Optional

from .logger import get_logger

log = get_logger("chanjet.token_store")

# appTicket 有效期（秒），平台每 10 分钟推一次，每个 ticket 有效 30 分钟
APP_TICKET_TTL_SECONDS = 1800


class TokenStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chanjet_token (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _get(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM chanjet_token WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def _set(self, key: str, value: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO chanjet_token (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, int(time.time())),
            )

    # ---- token ----

    def save_generated_token(self, value: dict) -> None:
        """保存 generateToken 返回值（camelCase 字段，映射到 snake_case 存储）。"""
        mapping = {
            "accessToken": "access_token",
            "refreshToken": "refresh_token",
            "expiresIn": "expires_in",
            "refreshExpiresIn": "refresh_expires_in",
            "userId": "user_id",
            "orgId": "org_id",
            "appName": "app_name",
            "scope": "scope",
        }
        for src, dst in mapping.items():
            if src in value and value[src] is not None:
                self._set(dst, str(value[src]))
        # 记录拿到 token 的时间戳，用于计算过期
        self._set("obtained_at", str(int(time.time())))

    def get_access_token(self) -> Optional[str]:
        return self._get("access_token")

    def is_access_expiring(self, ahead_seconds: int) -> bool:
        obtained = self._get("obtained_at")
        expires = self._get("expires_in")
        if not obtained or not expires:
            return True
        obtained_ts = int(obtained)
        expires_s = int(expires)
        remaining = (obtained_ts + expires_s) - int(time.time())
        return remaining < ahead_seconds

    # ---- appTicket ----

    def save_app_ticket(self, app_ticket: str) -> None:
        self._set("app_ticket", app_ticket)
        self._set("app_ticket_obtained_at", str(int(time.time())))

    def get_app_ticket(self) -> Optional[str]:
        return self._get("app_ticket")

    def is_app_ticket_expiring(self, ahead_seconds: int) -> bool:
        obtained = self._get("app_ticket_obtained_at")
        if not obtained:
            return True
        remaining = (int(obtained) + APP_TICKET_TTL_SECONDS) - int(time.time())
        return remaining < ahead_seconds
