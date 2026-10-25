"""畅捷通易代账连接器 - 配置。

所有敏感/环境相关配置均从环境变量读取，禁止硬编码凭证。
"""
from __future__ import annotations

import os
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@lru_cache(maxsize=1)
def get_settings() -> "Settings":
    return Settings()


class Settings:
    """服务配置。"""

    def __init__(self) -> None:
        # 畅捷通开放平台应用凭证（沙箱/正式用同一域名，靠 appKey 区分）
        self.app_key: str = _env("CHANJET_APP_KEY")
        self.app_secret: str = _env("CHANJET_APP_SECRET")

        # OAuth 回调地址（必须与开放平台应用里配置的完全一致，且不能带 query）
        self.redirect_uri: str = _env(
            "CHANJET_REDIRECT_URI",
            "http://101.96.216.161/api/v1/auth/callback/chanjet",
        )

        # API 网关 / 授权页
        self.api_base: str = _env("CHANJET_API_BASE", "https://openapi.chanjet.com").rstrip("/")
        self.authorize_base: str = _env(
            "CHANJET_AUTHORIZE_BASE", "https://market.chanjet.com/user/v2/authorize"
        ).rstrip("/")

        # 易代账应用名固定 ydzee；授权范围固定 auth_all
        self.app_name: str = _env("CHANJET_APP_NAME", "ydzee")
        self.scope: str = _env("CHANJET_SCOPE", "auth_all")

        # 授权时需要的企业ID（orgId）。为空则走「获取工作台管理帐套ID」等已授权流程补齐；
        # 沙箱联调时由协调器/用户提供。
        self.org_id: str = _env("CHANJET_ORG_ID", "")

        # token 持久化
        self.db_path: str = _env("CHANJET_DB_PATH", "/data/chanjet.db")

        # 提前多少秒刷新 token（access_token 默认 6 天，这里预留 10 分钟安全窗）
        self.token_refresh_ahead_seconds: int = int(
            _env("CHANJET_TOKEN_REFRESH_AHEAD_SECONDS", "600")
        )

        # HTTP 超时
        self.http_timeout_seconds: int = int(_env("CHANJET_HTTP_TIMEOUT_SECONDS", "30"))

        self.log_level: str = _env("CHANJET_LOG_LEVEL", "INFO").upper()

    @property
    def configured(self) -> bool:
        """是否已配置应用凭证。未配置时仍可启动，便于先验回调可达，再注入凭证。"""
        return bool(self.app_key and self.app_secret)
