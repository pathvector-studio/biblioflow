"""Minimal async HTTP client for the BiblioFlow API. Never logs or echoes credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

TIMEOUT = 30.0


class ApiError(Exception):
    """An error with a message that is safe and useful to show to the model / user."""


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    cf_client_id: str | None = None
    cf_client_secret: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = dict(os.environ) if env is None else env
        url = env.get("BIBLIOFLOW_URL", "").strip().rstrip("/")
        token = env.get("BIBLIOFLOW_TOKEN", "").strip()
        if not url or not token:
            raise ApiError(
                "BIBLIOFLOW_URL and BIBLIOFLOW_TOKEN must be set "
                "(BIBLIOFLOW_URL と BIBLIOFLOW_TOKEN を環境変数で指定してください)."
            )
        if not url.startswith(("http://", "https://")):
            raise ApiError("BIBLIOFLOW_URL must start with http:// or https://")
        cf_id = env.get("CF_ACCESS_CLIENT_ID", "").strip() or None
        cf_secret = env.get("CF_ACCESS_CLIENT_SECRET", "").strip() or None
        if bool(cf_id) != bool(cf_secret):
            raise ApiError("Set both CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET, or neither.")
        return cls(url=url, token=token, cf_client_id=cf_id, cf_client_secret=cf_secret)


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, str) else ""


class BiblioflowClient:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None) -> None:
        headers = {"Authorization": f"Bearer {config.token}", "Accept": "application/json"}
        if config.cf_client_id and config.cf_client_secret:
            headers["CF-Access-Client-Id"] = config.cf_client_id
            headers["CF-Access-Client-Secret"] = config.cf_client_secret
        self._http = httpx.AsyncClient(
            base_url=config.url + "/api/v1",
            headers=headers,
            timeout=TIMEOUT,
            follow_redirects=False,  # an Access login redirect must not be followed (or sent credentials)
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            response = await self._http.request(method, path, params=clean, json=json)
        except httpx.TimeoutException:
            raise ApiError(
                "The BiblioFlow server did not answer in time (サーバーが時間内に応答しませんでした)."
            ) from None
        except httpx.HTTPError as exc:
            raise ApiError(
                f"Could not reach the BiblioFlow server: {type(exc).__name__} (サーバーに接続できません)."
            ) from None
        return self._check(response)

    @staticmethod
    def _check(response: httpx.Response) -> Any:
        status = response.status_code
        if 300 <= status < 400:
            raise ApiError(
                "Redirected to a login page: Cloudflare Access is blocking the request. Set CF_ACCESS_CLIENT_ID and "
                "CF_ACCESS_CLIENT_SECRET to a service token that the policy allows "
                "(Access のログイン画面に飛ばされました。サービストークンを CF_ACCESS_CLIENT_ID / "
                "CF_ACCESS_CLIENT_SECRET に設定してください)."
            )
        if status == 401:
            raise ApiError(
                "Authentication failed: the token is invalid, revoked or expired "
                "(認証に失敗しました。トークンが無効・失効・期限切れです)."
            )
        if status == 403:
            detail = _detail(response)
            hint = f" ({detail})" if detail else ""
            raise ApiError(
                f"Forbidden{hint}: the token lacks the required scope (read:papers or write:papers), or the "
                "request was blocked by Cloudflare Access "
                "(権限がありません。トークンのスコープ、または Cloudflare Access を確認してください)."
            )
        if status == 404:
            raise ApiError("Not found, or you have no access to it (見つからないか、アクセス権がありません).")
        if status == 429:
            retry = response.headers.get("retry-after")
            wait = f" Retry after {retry} s." if retry else ""
            raise ApiError(f"Rate limited.{wait} (リクエストが多すぎます。しばらく待ってください)")
        if status == 422:
            detail = _detail(response)
            raise ApiError(f"Invalid request: {detail or 'validation failed'} (リクエストが不正です).")
        if status >= 400:
            raise ApiError(f"BiblioFlow returned HTTP {status} (サーバーエラー).")
        if "json" not in response.headers.get("content-type", ""):
            raise ApiError(
                "The server did not answer with JSON; check BIBLIOFLOW_URL "
                "(JSON ではない応答です。BIBLIOFLOW_URL を確認してください)."
            )
        return response.json()

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, json: Any) -> Any:
        return await self.request("POST", path, json=json)
