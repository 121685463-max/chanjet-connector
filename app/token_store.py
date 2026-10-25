"""token 持久化存储。

使用 SQLite（单文件、零运维），存 token / 到期时间 / 企业信息。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Optional

from .logger import get_logger

log = get_logger("chanjet.token_store")


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

    # ---- 业务字段 ----

    def save_token(self, data: dict) -> None:
        for key in (
            "access_token",
            "refresh_token",
            "expires_in",
            "refresh_expires_in",
            "user_id",
            "org_id",
            "user_auth_permanent_code",
            "sid",
            "app_name",
        ):
            if key in data and data[key] is not None:
                self._set(key, str(data[key]))
        # 记录拿到 token 的时间戳，用于计算过期
        self._set("obtained_at", str(int(time.time())))

    def load_token(self) -> dict:
        out: dict = {}
        for key in (
            "access_token",
            "refresh_token",
            "expires_in",
            "refresh_expires_in",
            "user_id",
            "org_id",
            "user_auth_permanent_code",
            "sid",
            "app_name",
            "obtained_at",
        ):
            val = self._get(key)
            if val is not None:
                out[key] = val
        return out

    def get_access_token(self) -> Optional[str]:
        return self._get("access_token")

    def get_refresh_token(self) -> Optional[str]:
        return self._get("refresh_token")

    def is_access_expiring(self, ahead_seconds: int) -> bool:
        obtained = self._get("obtained_at")
        expires = self._get("expires_in")
        if not obtained or not expires:
            return True
        obtained_ts = int(obtained)
        expires_s = int(expires)
        remaining = (obtained_ts + expires_s) - int(time.time())
        return remaining < ahead_seconds
