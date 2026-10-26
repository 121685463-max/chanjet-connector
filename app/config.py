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
        # 畅捷通开放平台「自建应用」凭证（靠 appKey 区分应用）
        self.app_key: str = _env("CHANJET_APP_KEY")
        self.app_secret: str = _env("CHANJET_APP_SECRET")

        # 自建应用软证书（应用管理员授权后获得）
        self.certificate: str = _env("CHANJET_CERTIFICATE")

        # 消息密钥（环境密钥）：ISV 自配 16 位字母数字，用于 AES 解密平台推送的 appTicket
        self.message_secret: str = _env("CHANJET_MESSAGE_SECRET")

        # API 网关
        self.api_base: str = _env("CHANJET_API_BASE", "https://openapi.chanjet.com").rstrip("/")

        # token 持久化
        self.db_path: str = _env("CHANJET_DB_PATH", "/data/chanjet.db")

        # 提前多少秒刷新 token（自建应用 token 无独立刷新接口，到期用最新 appTicket 重新生成）
        self.token_refresh_ahead_seconds: int = int(
            _env("CHANJET_TOKEN_REFRESH_AHEAD_SECONDS", "600")
        )

        # appTicket 有效期为 30 分钟，剩余不足该秒数时视为需要新 ticket
        self.app_ticket_refresh_ahead_seconds: int = int(
            _env("CHANJET_APP_TICKET_REFRESH_AHEAD_SECONDS", "900")
        )

        # HTTP 超时
        self.http_timeout_seconds: int = int(_env("CHANJET_HTTP_TIMEOUT_SECONDS", "30"))

        self.log_level: str = _env("CHANJET_LOG_LEVEL", "INFO").upper()

    @property
    def configured(self) -> bool:
        """是否已配置应用凭证。未配置时仍可启动，便于先验消息地址可达，再注入凭证。"""
        return bool(self.app_key and self.app_secret)

    @property
    def certificate_configured(self) -> bool:
        """软证书是否就绪（拿 openToken 必需）。"""
        return bool(self.certificate)

    @property
    def message_secret_configured(self) -> bool:
        """消息密钥是否就绪（解密 appTicket 必需）。"""
        return bool(self.message_secret)
