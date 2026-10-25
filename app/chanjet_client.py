"""畅捷通开放平台 HTTP 客户端封装。

统一鉴权头、错误归一化、超时、日志。
"""
from __future__ import annotations

import time
from typing import Any, Optional
from urllib.parse import urlencode

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

        部分接口用 code=="000000" 表示成功；getToken 等接口则直接返回 result 字段。
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
            msg = data.get("message") or data.get("msg") or data.get("error") or data.get("data")
            code = data.get("code")
            return f"code={code}, message={msg}"
        return str(data)

    # ---- OAuth ----

    def build_authorize_url(self) -> str:
        """生成跳转授权页 URL。

        若配置了 orgId 则带上；否则不带，等待用户在工作台选择企业。
        """
        params: dict[str, str] = {
            "appKey": self.settings.app_key,
            "appName": self.settings.app_name,
            "scope": self.settings.scope,
        }
        if self.settings.org_id:
            params["orgId"] = self.settings.org_id
        return f"{self.settings.authorize_base}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        """用授权码换取 token。"""
        url = self._api_url("/auth/v2/getToken")
        params = {
            "grantType": "authorization_code",
            "code": code,
            "redirectUri": self.settings.redirect_uri,
        }
        try:
            resp = self._http.get(url, params=params, headers=self._auth_headers())
        except httpx.HTTPError as exc:  # 网络层异常
            log.error("exchange_code http error: %s", exc)
            raise ChanjetError(f"网络异常: {exc}") from exc

        data = self._parse(resp)
        result = data.get("result") if isinstance(data, dict) else None
        if not result:
            raise ChanjetError(f"换取 token 失败: {self._error_message(data)}",
                               status_code=resp.status_code)
        return result

    def refresh(self, refresh_token: str) -> dict:
        """刷新 token。"""
        url = self._api_url("/auth/v2/refreshToken")
        params = {
            "grantType": "refresh_token",
            "refreshToken": refresh_token,
        }
        try:
            resp = self._http.get(url, params=params, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            log.error("refresh http error: %s", exc)
            raise ChanjetError(f"网络异常: {exc}") from exc

        data = self._parse(resp)
        result = data.get("result") if isinstance(data, dict) else None
        if not result:
            raise ChanjetError(f"刷新 token 失败: {self._error_message(data)}",
                               status_code=resp.status_code)
        return result

    # ---- 业务接口 ----

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
