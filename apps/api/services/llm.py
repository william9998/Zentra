"""Internal Gateway 的 LLM 代理 —— P4 的實作核心（SPEC §7.3，契約 §4）。

一句話：**沙盒拿 run token 來，平台換上真金鑰出去。**

    沙盒                      app（這裡）                    供應商
    ────                      ──────────                    ──────
    Authorization:            驗 token → 解出 run
      Bearer <run_token>  ──▶ 換成真金鑰            ──▶     OpenAI 相容端點
                              計量 token
                              寫 llm_calls
                          ◀── 原樣回傳               ◀──

為什麼真金鑰不能進沙盒（P4）：沙盒跑的是使用者的程式碼。金鑰一旦進去，
「使用者程式碼被攻陷」就等於「金鑰洩漏」，而且平台沒有任何辦法知道它被用去做什麼。
金鑰留在這一側，最壞情況只是某個 run 多花了錢 —— 而那筆錢**有紀錄**。

刻意做成 OpenAI 相容（契約 §4 結尾）：`ctx.llm()` 因此只是一個設了 `base_url` 與
`api_key=run_token` 的標準 LangChain chat model，SDK 不需要自訂 client。
"""

import json
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg
import httpx

from apps.api import db
from apps.api.config import get_settings
from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError
from apps.api.services import agent_credentials

#: 絕不轉發給供應商的 header。`authorization` 是最重要的一條 ——
#: 沙盒送來的是 run token，把它轉出去等於拿內部憑證去問外部服務。
_STRIP_REQUEST_HEADERS = frozenset({
    "authorization", "host", "content-length", "connection",
    "x-api-key", "openai-organization", "openai-project",
})

#: 不回傳給沙盒的 header（供應商的 rate limit / 組織資訊沒必要讓沙盒看到）。
_STRIP_RESPONSE_HEADERS = frozenset({
    "content-length", "content-encoding", "transfer-encoding", "connection",
    "openai-organization", "openai-project", "set-cookie",
})

_TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)

#: Gateway 願意轉發的上游路徑。**白名單，不是任意轉發** ——
#: 這個代理握著真金鑰，能打什麼必須是我們決定的，不是沙盒決定的。
#:
#: `responses` 是 ZEN-013 加的：模型自己決定何時用 web search 這條路走
#: Responses API，只代理 chat/completions 會把它擋在門外。
UPSTREAM_PATHS = frozenset({"chat/completions", "responses"})


def _price(model: str, kind: str) -> Decimal:
    """回傳每 token 的美金單價。沒設定價格的 model 一律 0。

    SPEC §11 把「計費出帳」列在 MVP 範圍外，所以這裡只做計量。
    價格留空不影響 token 數的正確性，只讓 `cost_usd` 是 "0"。
    """
    table = get_settings().llm_prices.get(model) or {}
    try:
        return Decimal(str(table.get(kind, "0"))) / _TOKENS_PER_PRICE_UNIT
    except (InvalidOperation, TypeError):
        return Decimal(0)


def _cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    return (
        _price(model, "input") * input_tokens
        + _price(model, "output") * output_tokens
    ).quantize(Decimal("0.000001"))


def _usage_from(payload: dict[str, Any]) -> tuple[int, int]:
    """兩種 API 的 usage 形狀不同，兩種都要認得。

        chat/completions →  prompt_tokens / completion_tokens
        responses        →  input_tokens  / output_tokens

    只認其中一種的話，另一種的呼叫會被記成 0 token —— 帳就是錯的，
    而且錯得很安靜。
    """
    usage = payload.get("usage") or {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if prompt is None and completion is None:
        prompt = usage.get("input_tokens")
        completion = usage.get("output_tokens")
    return int(prompt or 0), int(completion or 0)


async def _record(
    conn: asyncpg.Connection,
    run_id: uuid.UUID,
    *,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int | None = None,
    status_code: int | None = None,
    error: str | None = None,
    credential_source: str = "PLATFORM",
) -> None:
    """寫一列 `llm_calls`。**失敗的呼叫也要寫** —— 稽核要看得到試過但失敗。"""
    await conn.execute(
        f"""
        INSERT INTO llm_calls
            (id, run_id, model, input_tokens, output_tokens, cost_usd,
             latency_ms, status_code, error, credential_source)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        uuid.uuid4(), run_id, model[:128], input_tokens, output_tokens,
        _cost(model, input_tokens, output_tokens),
        latency_ms, status_code,
        None if error is None else error[:2000],
        credential_source,
    )


async def aggregate_usage(conn: asyncpg.Connection, run_id: uuid.UUID) -> dict[str, Any]:
    """從 `llm_calls` 聚合出 Run 的 `usage`（契約 §1 的形狀）。

    **這是唯一的來源。** 沙盒也會在 `/result` 回報 usage，但那是使用者程式碼算的，
    沒有理由被當成帳。沒有任何呼叫的 Run 得到全 0 與 `cost_usd: "0"`，
    正是契約要求的。
    """
    rows = await conn.fetch(
        "SELECT input_tokens, output_tokens, cost_usd FROM llm_calls WHERE run_id = $1",
        run_id,
    )
    input_tokens = sum(r["input_tokens"] for r in rows)
    output_tokens = sum(r["output_tokens"] for r in rows)
    cost = sum((r["cost_usd"] for r in rows), Decimal(0))
    return {
        "llm_calls": len(rows),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        # decimal 字串，不是 float —— 契約 §1 明訂，避免浮點誤差
        "cost_usd": format(cost.normalize(), "f") if cost else "0",
    }


def _forwardable(headers: Any) -> dict[str, str]:
    return {
        key: value for key, value in headers.items()
        if key.lower() not in _STRIP_REQUEST_HEADERS
    }


async def proxy(
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
    upstream_path: str,
    body: dict[str, Any],
    headers: Any,
) -> tuple[int, dict[str, str], bytes]:
    """代理一次上游呼叫，回 `(status, headers, body)`。

    `upstream_path` 必須在 `UPSTREAM_PATHS` 裡 —— 沙盒指定不了任意目的地。

    金鑰的選擇順序（ZEN-012）：**Agent 自己註冊的優先，沒有才用平台預設的。**
    Agent 的憑證綁在 `agent_id` 上，所以該 Agent 的每個版本都吃同一把；
    換掉它，所有版本立刻一起換（ADR-0011）。

    串流（`stream: true`）也走這條：我們強制加上 `stream_options.include_usage`，
    這樣供應商會在最後一個 chunk 附上用量，計量才不會因為串流而漏掉。
    """
    settings = get_settings()

    # 明文在這裡取得，只往下游走一步（換 header），不進 log、不進回應。
    async with db.connection() as conn:
        owned = await agent_credentials.resolve(conn, agent_id)
    api_key = owned.api_key if owned else settings.llm_api_key
    base_url = (owned.base_url if owned and owned.base_url else settings.llm_base_url)
    # 與 bootstrap／工作項同一條規則，不要在這裡重寫一次
    fallback_model = agent_credentials.pick_default_model(owned.default_model if owned else None)
    source = owned.source if owned else "PLATFORM"

    model = str(body.get("model") or fallback_model)
    body = {**body, "model": model}
    streaming = bool(body.get("stream"))
    if streaming and upstream_path == "chat/completions":
        # `stream_options` 是 chat/completions 專屬的。塞給 Responses API 會被
        # 供應商以 400 `Unknown parameter` 擋下 —— 實測踩過（真金鑰才會發現，
        # 假供應商照單全收）。Responses API 的用量在 `response.completed` 事件裡，
        # 不需要也不接受這個參數。
        body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}

    if not api_key:
        # 沒設金鑰不是「沙盒做錯事」，但沙盒需要一個能看懂的錯誤。
        async with db.transaction() as conn:
            await _record(conn, run_id, model=model, credential_source=source,
                          error="no LLM credential is configured")
        raise ZentraError(
            ErrorCode.LLM_ERROR,
            "no LLM credential is configured: register one for this agent "
            "(PUT /v1/agents/{id}/llm-credential) or set ZENTRA_LLM_API_KEY on the app service",
            502, run_id=str(run_id), details={"retryable": False},
        )

    if upstream_path not in UPSTREAM_PATHS:
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR,
            f"unsupported upstream path {upstream_path!r}; "
            f"the gateway forwards only {sorted(UPSTREAM_PATHS)}",
            404, run_id=str(run_id),
        )

    url = base_url.rstrip("/") + "/" + upstream_path
    outbound = _forwardable(headers) | {
        "authorization": f"Bearer {api_key}",   # ← 真金鑰只在這一行出現
        "content-type": "application/json",
    }

    # 上游回應最久要 llm_timeout_seconds。手上沒有連線。
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(url, json=body, headers=outbound)
            raw = response.content
    except httpx.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        async with db.transaction() as conn:
            await _record(conn, run_id, model=model, latency_ms=elapsed,
                          credential_source=source, error=f"{type(exc).__name__}: {exc}")
        raise ZentraError(
            ErrorCode.LLM_ERROR, f"LLM provider unreachable: {type(exc).__name__}",
            502, run_id=str(run_id), details={"retryable": True},
        ) from None

    elapsed = int((time.monotonic() - started) * 1000)
    input_tokens, output_tokens, error = 0, 0, None

    if response.status_code >= 400:
        error = raw[:2000].decode(errors="replace")
    elif streaming:
        input_tokens, output_tokens = _usage_from_stream(raw)
    else:
        try:
            input_tokens, output_tokens = _usage_from(json.loads(raw))
        except (ValueError, TypeError):
            error = "provider response was not JSON; usage not metered"

    async with db.transaction() as conn:
        await _record(conn, run_id, model=model, input_tokens=input_tokens,
                      output_tokens=output_tokens, latency_ms=elapsed,
                      status_code=response.status_code, error=error,
                      credential_source=source)

    safe_headers = {
        key: value for key, value in response.headers.items()
        if key.lower() not in _STRIP_RESPONSE_HEADERS
    }
    return response.status_code, safe_headers, raw


def _usage_from_stream(raw: bytes) -> tuple[int, int]:
    """從 SSE 串流取出用量。兩種 API 放的位置不同，兩處都找。

        chat/completions →  data: {... "usage": {...}}          頂層
        responses        →  data: {"type":"response.completed",
                                   "response": {... "usage": {...}}}   巢狀一層

    只找頂層的話，Responses API 的串流會被記成 0 token —— 不會報錯，帳安靜地錯。
    找不到就記 0：寧可用量記少也不要記錯。
    """
    for line in reversed(raw.split(b"\n")):
        if not line.startswith(b"data: "):
            continue
        chunk = line[6:].strip()
        if chunk in (b"[DONE]", b""):
            continue
        try:
            payload = json.loads(chunk)
        except ValueError:
            continue
        if payload.get("usage"):
            return _usage_from(payload)
        nested = payload.get("response")
        if isinstance(nested, dict) and nested.get("usage"):
            return _usage_from(nested)
    return 0, 0
