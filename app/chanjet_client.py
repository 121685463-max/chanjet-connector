"""畅捷通开放平台 HTTP 客户端封装。

统一鉴权头、错误归一化、超时、日志。

鉴权说明：自建应用不走 OAuth 授权码，而是
  - 应用管理员授权拿到软证书 certificate；
  - 平台每 10 分钟向「消息接收地址」推送 appTicket（AES 加密，需用消息密钥解密）；
  - 用 appTicket + certificate 调 generateToken 换 openToken；
  - 业务接口（账套/资产负债表/明细账）与商店应用同一套，仅 token 获取方式不同。
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional

import httpx

from .config import Settings
from .logger import get_logger

log = get_logger("chanjet.client")


class ChanjetError(Exception):
    """畅捷通接口调用失败（业务或网络）。"""

    def __init__(self, message: str, *, code: Optional[str] = None,
                 status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ChanjetClient:
    """对 openapi.chanjet.com 的统一封装。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._http = httpx.Client(
            timeout=settings.http_timeout_seconds,
            follow_redirects=False,
        )

    # ---- 内部工具 ----

    def _api_url(self, path: str) -> str:
        return f"{self.settings.api_base}{path}"

    def _auth_headers(self, token: Optional[str] = None) -> dict:
        headers = {
            "Content-Type": "application/json",
            "appKey": self.settings.app_key,
            "appSecret": self.settings.app_secret,
        }
        if token:
            headers["openToken"] = token
        return headers

    @staticmethod
    def _ok(data: Any) -> bool:
        """判定畅捷通返回是否成功。

        业务接口用 code=="000000" 表示成功；部分接口直接返回 result 字段。
        这里只做「无 code 或 code 为成功值」的宽松判定，具体由调用方解析。
        """
        if isinstance(data, dict):
            code = str(data.get("code", "") or "")
            if code in ("", "000000"):
                return True
            return False
        return True

    @staticmethod
    def _error_message(data: Any) -> str:
        if isinstance(data, dict):
            msg = (data.get("message") or data.get("msg") or data.get("hint")
                   or data.get("error") or data.get("data"))
            code = data.get("code")
            return f"code={code}, message={msg}"
        return str(data)

    # ---- 自建应用鉴权 ----

    def generate_token(self, app_ticket: str, certificate: str) -> dict:
        """用 appTicket + 软证书获取自建应用 openToken。

        参考: POST /v1/common/auth/selfBuiltApp/generateToken
        """
        url = self._api_url("/v1/common/auth/selfBuiltApp/generateToken")
        body = {"appTicket": app_ticket, "certificate": certificate}
        try:
            resp = self._http.post(url, json=body, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            log.error("generate_token http error: %s", exc)
            raise ChanjetError(f"网络异常: {exc}") from exc

        data = self._parse(resp)
        if resp.status_code >= 400:
            raise ChanjetError(
                f"获取自建应用 token HTTP {resp.status_code}: {self._error_message(data)}",
                status_code=resp.status_code,
            )
        if isinstance(data, dict) and not data.get("result"):
            raise ChanjetError(f"获取自建应用 token 失败: {self._error_message(data)}")
        value = data.get("value") if isinstance(data, dict) else None
        if not isinstance(value, dict) or not value.get("accessToken"):
            raise ChanjetError(f"获取自建应用 token 响应缺 accessToken: {self._error_message(data)}")
        return value

    def trigger_app_ticket_push(self) -> None:
        """立即触发平台向消息接收地址推送 appTicket（联调用，省去等 10 分钟）。

        参考: POST /auth/appTicket/resend
        """
        url = self._api_url("/auth/appTicket/resend")
        headers = {
            "appKey": self.settings.app_key,
            "appSecret": self.settings.app_secret,
        }
        try:
            resp = self._http.post(url, headers=headers)
        except httpx.HTTPError as exc:
            log.error("trigger_app_ticket_push http error: %s", exc)
            raise ChanjetError(f"网络异常: {exc}") from exc

        data = self._parse(resp)
        if isinstance(data, dict) and str(data.get("code", "")) != "200":
            raise ChanjetError(f"触发 appTicket 推送失败: {self._error_message(data)}")

    @staticmethod
    def decrypt_message(encrypt_msg: str, message_secret: str) -> dict:
        """AES-128-ECB 解密平台推送消息，返回 dict。

        平台推送 body 形如 {"encryptMsg": "<base64>"}，密钥为 16 位环境密钥。
        """
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad
        except ImportError as exc:  # pragma: no cover
            raise ChanjetError("缺少 pycryptodome 依赖，无法解密消息") from exc

        try:
            key = message_secret.encode("utf-8")
            if len(key) not in (16, 24, 32):
                raise ValueError(f"消息密钥长度 {len(key)} 非法，应为 16 位字母数字")
            raw = base64.b64decode(encrypt_msg)
            cipher = AES.new(key, AES.MODE_ECB)
            plain = unpad(cipher.decrypt(raw), AES.block_size).decode("utf-8")
            return json.loads(plain)
        except Exception as exc:
            log.warning("decrypt_message failed: %s", exc)
            raise ChanjetError(f"消息解密失败: {exc}") from exc

    @staticmethod
    def extract_app_ticket(msg: dict) -> Optional[str]:
        """从解密后的推送消息中提取 appTicket（形如 t-xxx）。

        仅匹配 t- 前缀的 ticket 串，避免把 APP_TEST / notice 等消息的业务内容误判为 ticket。
        """
        biz = msg.get("bizContent")
        if isinstance(biz, dict):
            for key in ("appTicket", "appticket", "ticket", "app_ticket", "value"):
                val = biz.get(key)
                if isinstance(val, str) and re.fullmatch(r"t-[A-Za-z0-9]{8,64}", val):
                    return val
        raw = json.dumps(msg, ensure_ascii=False)
        m = re.search(r'"?(t-[A-Za-z0-9]{8,64})"?', raw)
        return m.group(1) if m else None

    # ---- 业务接口（与商店应用同一套，仅 token 获取方式不同） ----

    def list_account_books(self, token: str) -> list:
        """拉取帐套列表。"""
        url = self._api_url("/accounting/accountBook/list")
        data = self._call_json(url, token)
        return data.get("value") or data.get("data") or [] if isinstance(data, dict) else []

    def get_balance_sheet(self, token: str, book_id: str, period: str) -> dict:
        """拉取指定帐套指定期间的资产负债表。

        优先走结构化接口 getNewBalanceSheet，回退到 gl/BalanceSheet。
        """
        # 结构化接口
        url = self._api_url(f"/accounting/fin/getNewBalanceSheet/{book_id}")
        data = self._call_json(url, token, params={"period": period})
        if isinstance(data, dict) and data.get("code") == "000000":
            return data
        # 回退传统接口
        url2 = self._api_url(f"/accounting/gl/BalanceSheet/{book_id}")
        data2 = self._call_json(url2, token, params={"period": period})
        return data2

    def get_detail_ledger(
        self,
        token: str,
        book_id: str,
        start_period: str,
        end_period: str,
        *,
        page_size: int = 100,
        page: int = 1,
    ) -> dict:
        """拉取明细账（分页）。"""
        url = self._api_url(f"/accounting/gl/SubsidiaryLedge/queryPaging/{book_id}")
        params = {
            "queryParam": "{}",
            "pageSize": page_size,
            "pageCount": page,
            "subsidiaryTag": 0,
            "startPeriod": start_period,
            "endPeriod": end_period,
        }
        return self._call_json(url, token, params=params)

    # ---- 底层 ----

    def _parse(self, resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def _call_json(self, url: str, token: str, *, params: Optional[dict] = None) -> dict:
        try:
            resp = self._http.get(url, params=params, headers=self._auth_headers(token))
        except httpx.HTTPError as exc:
            log.error("call %s http error: %s", url, exc)
            raise ChanjetError(f"网络异常: {exc}") from exc

        data = self._parse(resp)
        if resp.status_code >= 400:
            raise ChanjetError(
                f"接口 {url} HTTP {resp.status_code}: {self._error_message(data)}",
                status_code=resp.status_code,
            )
        if not self._ok(data):
            raise ChanjetError(f"接口 {url} 业务失败: {self._error_message(data)}",
                               code=str(data.get("code")) if isinstance(data, dict) else None)
        return data
