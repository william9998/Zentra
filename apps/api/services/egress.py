"""對外 HTTP 代打（ZEN-019 / api-contract v0.12.1 §4.2、ADR-0014 §3）。

沙盒網段沒有對外路由（SPEC §6.2 L1）。這個模組是平台**替**沙盒打外部 HTTP
的地方：白名單決定「能送到哪」、平台注入認證、每一次都寫稽核。

## ⚠️ 這是一條真正的外洩通道

白名單擋得住「送到哪」，**擋不住「送了什麼」**（ADR-0014 §6）。
`GET` 也能外洩 —— query string 就是資料。所以這裡的每一條檢查都不是裝飾：

  · 預設拒絕（E1）：沒有規則就一個都不准連
  · 只有 GET／POST（E2），POST 要在那一條規則上明列
  · 沙盒不得自帶認證（E3）：`headers` 只准 Accept 與 Content-Type
  · 只准 https、不跟隨 redirect（E4）：跟隨的話對方一行 302 就繞過白名單
  · 解析後的位址落在內網一律擋（E5）
  · 大小與次數上限（E6）
  · **每一個通過認證的請求都寫一列 `egress_calls`**，`422` 與被擋下的都要寫（E7 / U3）

## 檢查順序有意義（spec §5.3）

**格式錯誤（422）排在政策檢查（403）前面**：`422` 說的是「這個請求寫錯了」，
`403` 說的是「這個請求沒錯，但政策不准」。先判政策的話，一個同時寫錯又不被
允許的請求會回 `403`，使用者跑去改白名單，改完還是失敗。

**次數上限排在 DNS 之前**：DNS 是最貴的一步，而超過上限的請求無論如何都不會送出。

## SSRF：解析一次，直接連那個 IP

proxy 是從 `app` 發出去的，而 `app` **同時掛在 `zentra-net` 上** ——
它連得到 postgres 與 redis。所以「解析完再讓 http client 自己解析第二次」
等於沒檢查（DNS rebinding）。這裡的做法是：解析一次 → 檢查**每一個**位址 →
**連那個 IP**，並帶上 `Host` header 與 SNI（憑證仍以原主機名驗證）。

## 沒有旁路（契約 §4.2 第 9 點 / U8）

**這個檔案裡沒有任何旗標、環境變數或參數可以關掉 scheme／redirect／私有位址
三項檢查，連預設關閉的都沒有。** 一個存在的旁路遲早會有人打開，而它打開時
沒有任何症狀。

可測性是**做進結構裡**的，不是開後門：
  · `first_blocked_address()` 是純函式，吃「已解析的 IP 清單」→ 可以表格測試窮舉
  · `resolve_addresses()` 是 module 層函式 → 測試 monkeypatch 它
  · `build_transport()` 回 `None`（httpx 用預設）→ 測試換成 `httpx.MockTransport`
這三個縫都**不改變任何檢查**，只決定「名字怎麼變成 IP」與「位元組從哪個
socket 出去」。
"""

import asyncio
import ipaddress
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.enums import EgressKind, EgressReason, ErrorCode
from apps.api.errors import ZentraError
from apps.api.ids import uuid7
from apps.api.models import AgentEgressRule, EgressCall
from apps.api.schemas import EgressRequest
from apps.api.services import agent_secrets, egress_rules

#: 沙盒可以自帶的 header，**就這兩個**（E3）。其餘一律 422 並指名。
ALLOWED_REQUEST_HEADERS = {"accept", "content-type"}
#: 平台固定送這個 UA。
#:
#: ⚠️ **這個字串不是任意的，改之前要重驗主流交易所。** 實測：OKX 會擋字面
#: `Python-urllib/*` 這個 UA 並回 403（完全預設 → 403、只加 `Accept` → 403、
#: 只要換掉 UA 就 200）；`Zentra-Egress/1` 對 Binance 與 OKX 都回 200。
#: 有人覺得這字串不好看而改掉時，症狀會是**備援源突然全掛**，
#: 而且看起來像對方在擋我們。
USER_AGENT = "Zentra-Egress/1"
#: 回傳給沙盒的 header 白名單（§4.2 第 7 點）。`set-cookie` 與
#: `authorization` 一律不回傳。
_RESPONSE_HEADERS = {"content-type", "content-length", "date", "retry-after"}
_RESPONSE_HEADER_PREFIXES = ("x-ratelimit-",)


@dataclass(frozen=True)
class EgressEnvelope:
    """**只要請求真的送出去了，Gateway 一律回 200**，上游狀態碼放在 `status`。

    上游回 418／429／500 不是平台錯誤 —— 這是「Binance 主源、OKX 備援」
    能實作的前提：agent 要看得到狀態碼才切得了備援。
    """

    status: int
    headers: dict[str, str]
    body: str


def blocked(reason: EgressReason, message: str, run_id: str, **details) -> ZentraError:
    """`EGRESS_BLOCKED`（403）：政策擋下。**不可重試** —— 政策不會因為再打一次就改變。"""
    return ZentraError(
        ErrorCode.EGRESS_BLOCKED, message, 403, run_id=run_id,
        details={"reason": str(reason), **details},
    )


def failed(message: str, run_id: str, **details) -> ZentraError:
    """`EGRESS_FAILED`（502）：政策放行了，但上游打不通。可重試。"""
    return ZentraError(
        ErrorCode.EGRESS_FAILED, message, 502, run_id=run_id, details=details)


def invalid(message: str, run_id: str, **details) -> ZentraError:
    """`422 VALIDATION_ERROR`：**請求本身寫錯了**，不是被政策擋下（U1）。

    §4.3 的 `reason` 枚舉只服務 `EGRESS_BLOCKED`，所以這裡用 §0 既有的欄位層級
    `details`（`details.header` / `details.field`）。分開是為了讓呼叫端答得出
    唯一需要判斷的事：**這個請求該改寫，還是該去改設定。**
    """
    return ZentraError(
        ErrorCode.VALIDATION_ERROR, message, 422, run_id=run_id, details=details)


async def record(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
    kind: EgressKind,
    host: str,
    path: str,
    rule_id: uuid.UUID | None = None,
    method: str | None = None,
    status_code: int | None = None,
    request_bytes: int = 0,
    response_bytes: int = 0,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    """寫一列稽核。

    **驗完 run token 之後的每一條 return 路徑都要先呼叫這個**（契約 §4.2 第 8 點）。
    唯一的例外是連認證都沒過（401／403 token 無效）：那時沒有可信的 `run_id`，
    而 `egress_calls.run_id` 是 `NOT NULL`。

    `error` 的三種形狀（§2.10 第 4 點）：
      · `EGRESS_BLOCKED: <reason>`  政策擋下，`status_code` 為 `None`
      · `VALIDATION_ERROR: <細節>`  請求本身寫錯
      · `EGRESS_FAILED: <細節>`     上游打不通
      · `None`                      送出去了（**即使上游回 4xx／5xx**）
    """
    session.add(EgressCall(
        call_id=uuid7(), run_id=run_id, agent_id=agent_id, kind=str(kind),
        rule_id=rule_id, method=method, host=host, path=path,
        status_code=status_code, request_bytes=request_bytes,
        response_bytes=response_bytes, latency_ms=latency_ms, error=error,
        created_at=datetime.now(UTC),
    ))
    await session.commit()


def block_error_text(reason: EgressReason) -> str:
    """被擋下那一列的 `error`。Console 靠它把原因譯成人話。"""
    return f"{ErrorCode.EGRESS_BLOCKED}: {reason}"


def invalid_error_text(detail: str) -> str:
    return f"{ErrorCode.VALIDATION_ERROR}: {detail}"


async def attempt_count(
    session: AsyncSession, run_id: uuid.UUID, kind: EgressKind,
) -> int:
    """這筆 Run **嘗試**過幾次（契約 §4.2 第 6 點 / U5）。

    **被擋下的也算**，而且計數來源就是 `egress_calls` 的列數，
    **不另外維護計數器** —— 兩份會不一致，而不一致時沒有辦法判斷哪份是對的。

    只算成功的話，一個一直打非白名單主機的迴圈就完全不受限，
    而**會失控的迴圈通常正是一直失敗的那個**。
    """
    return int((await session.execute(
        select(func.count(EgressCall.call_id)).where(
            EgressCall.run_id == run_id,
            EgressCall.kind == str(kind),
        )
    )).scalar_one())


def is_public_address(address: str) -> bool:
    """這個 IP 是不是公網位址。**純函式，不碰網路**（U8）。

    用 `ipaddress` 的分類，不是自己寫網段表 —— 少一個 `fc00::/7` 就是一個洞。

    判準是 **`is_global`，不是 `not is_private`**：`100.64.0.0/10`（CGNAT）
    在 Python 裡 `is_private` 是 `False` 但 `is_global` 也是 `False`，
    用前者會把它放行。同理 `192.0.2.0/24`（文件用網段）也是。
    白名單式的判斷（只放行明確是公網的）比黑名單式的安全。

    IPv4-mapped 的 `::ffff:127.0.0.1` 要**先攤平再判斷**，
    不然它會被當成一個普通的 IPv6 位址放行。

    解析不出來的字串一律不安全 —— fail closed。
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if parsed.version == 6 and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    if (parsed.is_private or parsed.is_loopback or parsed.is_link_local
            or parsed.is_reserved or parsed.is_multicast or parsed.is_unspecified):
        return False
    return bool(parsed.is_global)


def first_blocked_address(addresses: list[str]) -> str | None:
    """回第一個不該連的位址，全部安全時回 `None`。**純函式**（U8）。

    **任何一個位址不安全就整個擋掉**：round-robin 的 DNS 可以把一個內網位址
    藏在第二筆裡，只看第一筆等於沒檢查。
    """
    for address in addresses:
        if not is_public_address(address):
            return address
    return None


async def resolve_addresses(host: str, port: int) -> list[str]:
    """名字 → IP 清單。**module 層函式，測試會 monkeypatch 它**（U8）。

    它只做解析，不做判斷 —— 判斷在 `first_blocked_address()`。
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in seen:
            seen.append(address)
    return seen


def build_transport():
    """httpx 的 transport。正式環境回 `None`（用預設），測試換成 `MockTransport`。

    **這不是旁路。** 它不改變任何檢查，只決定位元組從哪個 socket 出去 ——
    白名單、scheme、redirect、私有位址四項檢查在這之前就跑完了。
    """
    return None


def _authority(host: str, port: int) -> str:
    return host if port == 443 else f"{host}:{port}"


def _connect_url(scheme: str, address: str, port: int, path: str, query: str) -> str:
    literal = f"[{address}]" if ":" in address else address
    target = f"{scheme}://{literal}:{port}{path or '/'}"
    return f"{target}?{query}" if query else target


def filter_response_headers(headers) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in _RESPONSE_HEADERS or lowered.startswith(_RESPONSE_HEADER_PREFIXES):
            safe[lowered] = value
    return safe


def _first_error_detail(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error.get("loc", ())) or "body"
    return f"{location}: {error.get('msg', 'invalid')}"


def _peek_url(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("url"), str):
        return payload["url"]
    return ""


async def perform(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
    payload: Any,
) -> EgressEnvelope:
    """代打一次。`payload` 是沙盒送來的**原始 JSON**。

    為什麼吃原始 JSON 而不是綁好的 pydantic 模型：FastAPI 的 body 驗證失敗會在
    進到這個函式**之前**就回 `422`，那條路徑寫不了稽核列 —— 而契約 §4.2 第 8 點
    要求「驗完 token 之後的每一條 return 路徑都要先寫稽核」。
    所以驗證挪進來，形狀仍然用 `schemas.EgressRequest`（契約的鏡子只有一份）。
    """
    settings = get_settings()
    run_id_text = str(run_id)

    # 先從原始 payload 撈出 url，這樣連「請求本身寫錯」的那一列都指得出主機。
    peeked = urlsplit(_peek_url(payload)) if _peek_url(payload) else None
    audit_host = (peeked.hostname or "").lower() if peeked else ""
    audit_path = (peeked.path or "/") if peeked else "(invalid request)"
    if not audit_host:
        audit_host = "(unparsed)"

    async def write(**kwargs) -> None:
        await record(
            session, run_id=run_id, agent_id=agent_id, kind=EgressKind.HTTP,
            host=kwargs.pop("host", audit_host), path=kwargs.pop("path", audit_path),
            **kwargs,
        )

    # ── 2. body / timeout_seconds 的格式（422，不夾值；U4）─────────────
    try:
        body = EgressRequest.model_validate(payload)
    except ValidationError as exc:
        detail = _first_error_detail(exc)
        await write(error=invalid_error_text(detail))
        raise invalid(
            f"the egress request is malformed ({detail}). timeout_seconds must be an "
            f"integer 1-15 -- the platform does not silently clamp it, because "
            f"'I asked for 60s but it died at 15' is impossible to debug.",
            run_id_text, field=detail.split(":", 1)[0],
        ) from exc

    method = body.method
    parts = urlsplit(body.url)
    host = (parts.hostname or "").lower()
    # **稽核不存 query string**（§2.10 第 1 點）：那裡常常就是使用者放 token
    # 的地方，稽核表不該變成第二個機密外洩點。
    audit_path = parts.path or "/"
    audit_host = host or "(unparsed)"

    async def block(reason: EgressReason, message: str, rule=None, **details):
        await write(method=method, rule_id=rule.rule_id if rule is not None else None,
                    error=block_error_text(reason))
        return blocked(reason, message, run_id_text, **details)

    # ── 3. headers：**422 並指名是哪個**，不是默默丟掉（E3 / U1）────────
    for key in body.headers:
        if key.lower() not in ALLOWED_REQUEST_HEADERS:
            await write(method=method,
                        error=invalid_error_text(f"header {key}"))
            raise invalid(
                f"the sandbox cannot set the {key!r} header. Only Accept and "
                "Content-Type are allowed; to send credentials, set "
                "auth_secret_name + auth_header on the egress rule and the platform "
                "injects them -- the value never enters the sandbox.",
                run_id_text, header=key)

    # ── 4. 這個 Agent 有沒有規則（1. run token 由 deps 擋掉，那條不寫稽核）──
    if not await egress_rules.has_rules(session, agent_id):
        raise await block(
            EgressReason.NO_RULES,
            "this agent has no egress rules, so it cannot reach any external host. "
            "Add one in the console (Agent > 對外出口).")

    # ── 5. scheme ────────────────────────────────────────────────────
    # 比對的是**原始字串**，不是 `urlsplit` 正規化過的 scheme：後者會把
    # `HTTPS://` 小寫成 `https` 而放行。契約寫的是「只准 `https`」，
    # 這裡 fail closed。
    raw_scheme = body.url.split(":", 1)[0] if ":" in body.url else ""
    if parts.scheme != "https" or raw_scheme != "https":
        raise await block(
            EgressReason.SCHEME_NOT_ALLOWED,
            f"only https is proxied, got {raw_scheme or '(none)'}")
    if not host:
        raise await block(EgressReason.HOST_NOT_ALLOWED, "the URL has no host")

    # ── 6. host 命中哪一條規則 ────────────────────────────────────────
    rule: AgentEgressRule | None = await egress_rules.match_rule(session, agent_id, host)
    if rule is None:
        raise await block(
            EgressReason.HOST_NOT_ALLOWED,
            f"{host} is not on this agent's egress whitelist")

    # ── 7. method ────────────────────────────────────────────────────
    if method not in (rule.methods or []):
        raise await block(
            EgressReason.METHOD_NOT_ALLOWED,
            f"the rule for {host} allows {', '.join(rule.methods or [])}, not {method}",
            rule=rule)

    # ── 8. path_prefix ───────────────────────────────────────────────
    if rule.path_prefix and not (parts.path or "/").startswith(rule.path_prefix):
        raise await block(
            EgressReason.PATH_NOT_ALLOWED,
            f"the rule for {host} only allows paths under {rule.path_prefix}",
            rule=rule)

    # 大小上限。排在政策檢查之後、DNS 之前：沒有規則的 Agent 該看到 NO_RULES，
    # 而不是一個關於位元組數的訊息。
    raw_body = body.body.encode() if body.body else b""
    if len(raw_body) > settings.egress_max_request_bytes:
        raise await block(
            EgressReason.REQUEST_TOO_LARGE,
            f"request body is {len(raw_body)} bytes, the limit is "
            f"{settings.egress_max_request_bytes}", rule=rule)

    # ── 9. 這筆 Run 的次數上限（**含被擋下的嘗試**；U5）─────────────────
    used = await attempt_count(session, run_id, EgressKind.HTTP)
    if used >= settings.egress_max_calls_per_run:
        raise await block(
            EgressReason.RUN_CALL_LIMIT,
            f"this run already attempted {used} outbound HTTP calls, the limit is "
            f"{settings.egress_max_calls_per_run} (being rate-limited by an exchange "
            "gets the whole host banned, not just this agent)",
            rule=rule)

    # ── 10. DNS 解析 → 位址是不是內網 ─────────────────────────────────
    port = parts.port or 443
    # DNS 與上游都可能慢。別在等它們時占著池子裡的連線（見 llm.proxy）。
    await session.commit()
    try:
        addresses = await resolve_addresses(host, port)
    except OSError as exc:
        await write(method=method, rule_id=rule.rule_id, request_bytes=len(raw_body),
                    latency_ms=0,
                    error=f"{ErrorCode.EGRESS_FAILED}: DNS {type(exc).__name__}: {exc}")
        raise failed(f"cannot resolve {host}: {exc}", run_id_text) from exc
    if not addresses:
        raise await block(EgressReason.PRIVATE_ADDRESS,
                          f"{host} did not resolve to any address", rule=rule)
    unsafe = first_blocked_address(addresses)
    if unsafe is not None:
        raise await block(
            EgressReason.PRIVATE_ADDRESS,
            f"{host} resolves to a private/loopback/link-local address ({unsafe}); "
            "the proxy runs on the platform network, so that would reach the "
            "platform's own services",
            rule=rule, resolved=unsafe)

    # ── 11. 送出。**連解析出來的那個 IP**，帶上 Host header 與 SNI ──────
    #      交給 http client 自己再解析一次等於沒做第 10 步（DNS rebinding）。
    address = addresses[0]
    send_headers = dict(body.headers)
    send_headers["Host"] = _authority(host, port)
    send_headers["User-Agent"] = USER_AGENT
    if rule.auth_secret_name and rule.auth_header:
        # **平台注入認證。** 機密從頭到尾沒進過沙盒（E3）。
        value = await agent_secrets.resolve(session, agent_id, rule.auth_secret_name)
        if value is None:
            raise await block(
                EgressReason.HOST_NOT_ALLOWED,
                f"the rule for {host} references secret "
                f"{rule.auth_secret_name!r}, which no longer exists",
                rule=rule)
        send_headers[rule.auth_header] = value

    target = _connect_url(parts.scheme, address, port, parts.path, parts.query)
    await session.commit()
    started = time.monotonic()
    max_response = settings.egress_max_response_bytes
    try:
        async with httpx.AsyncClient(
            timeout=float(body.timeout_seconds), follow_redirects=False,
            trust_env=False, transport=build_transport(),
        ) as client:
            request = client.build_request(
                method, target, headers=send_headers,
                content=raw_body or None,
                # httpcore 用它做 TLS 的 server_hostname，憑證因此仍以原主機名驗證。
                extensions={"sni_hostname": host},
            )
            response = await client.send(request, stream=True)
            try:
                chunks: list[bytes] = []
                size = 0
                too_large = False
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_response:
                        too_large = True
                        break
                    chunks.append(chunk)
                payload_bytes = b"".join(chunks)
            finally:
                await response.aclose()
    except httpx.HTTPError as exc:
        latency = int((time.monotonic() - started) * 1000)
        await write(method=method, rule_id=rule.rule_id, request_bytes=len(raw_body),
                    latency_ms=latency,
                    error=f"{ErrorCode.EGRESS_FAILED}: {type(exc).__name__}: {exc}")
        raise failed(f"{host} could not be reached: {type(exc).__name__}: {exc}",
                     run_id_text) from exc

    latency = int((time.monotonic() - started) * 1000)

    if too_large:
        await write(method=method, rule_id=rule.rule_id,
                    status_code=response.status_code, request_bytes=len(raw_body),
                    response_bytes=size, latency_ms=latency,
                    error=block_error_text(EgressReason.RESPONSE_TOO_LARGE))
        # **超量不截斷。** 截斷過的 JSON 解析失敗時，錯誤訊息會指向 agent 的
        # 解析程式碼，而不是真正的原因。
        raise blocked(
            EgressReason.RESPONSE_TOO_LARGE,
            f"the response exceeded {max_response} bytes and was not truncated",
            run_id_text)

    # 3xx：**不跟隨**。跟隨的話對方一行 302 就繞過白名單了（E4）。
    if 300 <= response.status_code < 400:
        location = response.headers.get("location", "")
        await write(method=method, rule_id=rule.rule_id,
                    status_code=response.status_code, request_bytes=len(raw_body),
                    response_bytes=len(payload_bytes), latency_ms=latency,
                    error=block_error_text(EgressReason.REDIRECT))
        raise blocked(
            EgressReason.REDIRECT,
            f"{host} answered {response.status_code} and the proxy does not follow "
            "redirects: the destination would bypass the whitelist",
            run_id_text, location=location)

    # ── 12. 送出去了。**`error` 是 None，即使上游回 4xx／5xx** ──────────
    await write(method=method, rule_id=rule.rule_id,
                status_code=response.status_code, request_bytes=len(raw_body),
                response_bytes=len(payload_bytes), latency_ms=latency)
    return EgressEnvelope(
        status=response.status_code,
        headers=filter_response_headers(response.headers),
        # 上游偶爾會回非 UTF-8 的位元組。用 replace 而不是拋錯：
        # 那筆呼叫確實發生了，agent 應該看得到它拿到什麼。
        body=payload_bytes.decode("utf-8", errors="replace"),
    )
