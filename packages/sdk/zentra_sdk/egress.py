"""`ctx.http` 與 `ctx.send_email()` 的實作（ZEN-019 / api-contract §4.2、§4.4）。

沙盒網段**沒有對外路由**（SPEC §6.2 L1）。這裡不是「一個 HTTP client」，
而是**請平台代打**：請求送到 Internal Gateway，由它比對白名單、注入認證、
寫稽核，然後把結果原樣帶回來。

三件事要先知道：

1. **拿不到金鑰，但用得了金鑰。** 認證由平台在規則上設定（`auth_secret_name`
   + `auth_header`）並在代打時注入。你的程式碼裡不會有、也拿不到那個值。
2. **上游的狀態碼看得到。** 只要請求真的送出去了，這裡回的 `status` 就是上游
   的狀態碼 —— 這是「主源失敗換備援」能寫得出來的前提。
   被平台**擋下**才會拋例外（`EgressBlocked`）。
3. **這些方法是同步阻塞的。** 在 LangGraph 的**同步節點**裡呼叫
   （`def node(state)`，不是 `async def`）—— LangGraph 會把同步節點丟到
   worker thread 跑，不會卡住 event loop。在 `async def` 節點裡呼叫會讓
   同一個常駐容器裡的其他 Run 一起卡住。
"""

from __future__ import annotations

import json
from typing import Any

#: 沙盒可以自帶的 header，就這兩個。其餘由平台注入或直接 422（ADR-0014 E3）。
ALLOWED_HEADERS = ("Accept", "Content-Type")


class EgressError(RuntimeError):
    """出口相關錯誤的共同基底。`code` 是 api-contract §0 的錯誤 code。"""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

    @property
    def reason(self) -> str | None:
        """`EGRESS_BLOCKED` 的具體原因（api-contract §4.3 的枚舉）。"""
        value = self.details.get("reason")
        return str(value) if value else None


class EgressInvalid(EgressError):
    """請求本身寫錯了（`422`）—— 例如自帶了 `Authorization` header，
    或 `timeout_seconds` 超過 15（平台**不夾值**）。

    與 `EgressBlocked` 的差別是「該改哪裡」：這個要改**程式碼**，
    那個要改 Console 上的**設定**。
    """


class EgressBlocked(EgressError):
    """平台政策擋下了這次出口。**重試沒有用** —— 政策不會因為再打一次就改變。

    常見的 `reason`：`NO_RULES`（這個 Agent 一條白名單都沒有）、
    `HOST_NOT_ALLOWED`、`METHOD_NOT_ALLOWED`、`PATH_NOT_ALLOWED`、
    `SCHEME_NOT_ALLOWED`、`PRIVATE_ADDRESS`、`REDIRECT`、`RUN_CALL_LIMIT`。
    去 Console 的「對外出口」加規則，不要在程式碼裡重試。
    """


class EgressFailed(EgressError):
    """政策放行了，但上游打不通（DNS、連線被拒、逾時）。重試有意義。"""


class EgressResponse:
    """上游的回應。**`status` 是上游的狀態碼**，不是平台的。"""

    __slots__ = ("status", "headers", "text", "url")

    def __init__(self, status: int, headers: dict[str, str], text: str, url: str) -> None:
        self.status = status
        #: 安全子集：`content-type` / `content-length` / `date` / `retry-after` /
        #: `x-ratelimit-*`。`set-cookie` 與 `authorization` 平台不回傳。
        self.headers = headers
        self.text = text
        self.url = url

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> "EgressResponse":
        """非 2xx 時拋 `UpstreamStatusError`。

        主源／備援的寫法就靠它：`except Exception` 接到之後換下一家。
        """
        if not self.ok:
            raise UpstreamStatusError(self)
        return self

    def __repr__(self) -> str:                    # pragma: no cover - 除錯用
        return f"<EgressResponse {self.status} {self.url} {len(self.text)}B>"


class UpstreamStatusError(RuntimeError):
    """上游回了非 2xx。**這不是平台錯誤** —— 請求真的送出去了。"""

    def __init__(self, response: EgressResponse) -> None:
        super().__init__(
            f"{response.url} answered {response.status}: {response.text[:200]}")
        self.response = response
        self.status = response.status


def _raise_from_envelope(payload: Any, status_code: int) -> None:
    """把 §0 的錯誤信封變成一個看得懂的例外。"""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        raise EgressError(
            "INTERNAL_ERROR",
            f"the gateway answered HTTP {status_code} without the contract error shape")
    code = str(error.get("code") or "INTERNAL_ERROR")
    message = str(error.get("message") or "egress failed")
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    if code == "EGRESS_BLOCKED":
        raise EgressBlocked(code, message, details)
    if code == "EGRESS_FAILED":
        raise EgressFailed(code, message, details)
    if code == "VALIDATION_ERROR":
        raise EgressInvalid(code, message, details)
    raise EgressError(code, message, details)


class EgressClient:
    """`ctx.http`。平台代打的對外 HTTP。

        rows = ctx.http.get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT"
                            "&interval=1h&limit=200").raise_for_status().json()

    只支援 `GET` 與 `POST`，只支援 `https`，不跟隨 redirect ——
    這些都不是這個 client 的限制，是平台的政策（ADR-0014 §3）。
    """

    #: 跟 Gateway 之間的緩衝。上游逾時是 `timeout_seconds`，這裡要多留一點，
    #: 否則上游逾時之前 SDK 自己就先斷了，錯誤訊息會指向完全錯的方向。
    _GATEWAY_MARGIN = 10.0

    def __init__(self, gateway: str, run_token: str) -> None:
        self._gateway = gateway.rstrip("/")
        self._run_token = run_token

    def get(self, url: str, *, headers: dict[str, str] | None = None,
            timeout_seconds: int = 10) -> EgressResponse:
        return self.request("GET", url, headers=headers, timeout_seconds=timeout_seconds)

    def post(self, url: str, *, body: str | None = None, json_body: Any = None,
             headers: dict[str, str] | None = None,
             timeout_seconds: int = 10) -> EgressResponse:
        """`POST` 必須在**那一條白名單規則上**明列，否則回 `METHOD_NOT_ALLOWED`。"""
        send_headers = dict(headers or {})
        if json_body is not None:
            if body is not None:
                raise ValueError("pass either body or json_body, not both")
            body = json.dumps(json_body)
            send_headers.setdefault("Content-Type", "application/json")
        return self.request("POST", url, body=body, headers=send_headers,
                            timeout_seconds=timeout_seconds)

    def request(self, method: str, url: str, *, body: str | None = None,
                headers: dict[str, str] | None = None,
                timeout_seconds: int = 10) -> EgressResponse:
        """`timeout_seconds` 是**整數 1–15**。超過上限平台回 `422`，**不夾值**。

        自帶 `Accept` / `Content-Type` 以外的 header 也是 `422`，
        **而且刻意讓平台來判** —— 在這裡先擋掉的話，那次嘗試不會出現在
        Console 的出口稽核裡，而「我的請求去哪了」正是那張表要回答的問題。
        """
        import httpx

        send_headers = dict(headers or {})
        payload: dict[str, Any] = {
            "url": url, "method": method.upper(), "headers": send_headers,
            "timeout_seconds": timeout_seconds,
        }
        if body is not None:
            payload["body"] = body

        try:
            response = httpx.post(
                f"{self._gateway}/egress/request", json=payload,
                headers={"Authorization": f"Bearer {self._run_token}"},
                timeout=timeout_seconds + self._GATEWAY_MARGIN, trust_env=False,
            )
        except Exception as exc:                  # noqa: BLE001 - 連 Gateway 都不通
            raise EgressFailed(
                "EGRESS_FAILED",
                f"could not reach the Zentra gateway: {type(exc).__name__}: {exc}") from exc

        if response.status_code != 200:
            try:
                parsed = response.json()
            except ValueError:
                parsed = None
            _raise_from_envelope(parsed, response.status_code)

        envelope = response.json()
        return EgressResponse(
            status=int(envelope.get("status", 0)),
            headers=dict(envelope.get("headers") or {}),
            text=str(envelope.get("body") or ""),
            url=url,
        )


def send_email(
    gateway: str, run_token: str, *, to: list[str] | str, subject: str,
    text: str | None = None, html: str | None = None, timeout_seconds: float = 45.0,
) -> str:
    """`ctx.send_email()`。回 `message_id`。

    **`202` 表示 SMTP 伺服器收下了，不表示對方讀到了。**
    寄件者、帳號、密碼都由平台決定（Console 的「寄件者」設定）；
    收件人必須在 `allowed_recipients` 裡，有一個不在**整封信都不寄**。

    **不冪等**：重送就是再寄一封。要冪等請自己在狀態裡記。
    """
    import httpx

    recipients = [to] if isinstance(to, str) else list(to)
    payload: dict[str, Any] = {"to": recipients, "subject": subject}
    if text is not None:
        payload["text"] = text
    if html is not None:
        payload["html"] = html

    try:
        response = httpx.post(
            f"{gateway.rstrip('/')}/notify/email", json=payload,
            headers={"Authorization": f"Bearer {run_token}"},
            timeout=timeout_seconds, trust_env=False,
        )
    except Exception as exc:                      # noqa: BLE001
        raise EgressFailed(
            "EGRESS_FAILED",
            f"could not reach the Zentra gateway: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 202:
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        _raise_from_envelope(parsed, response.status_code)
    return str(response.json().get("message_id") or "")
