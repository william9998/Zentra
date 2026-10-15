"""Token-authenticated Gateway; reachable on port 8000, hidden from OpenAPI."""

import asyncio
import json

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis

from apps.api import db
from apps.api.deps import (
    get_redis, require_container_token, require_run_token, require_run_token_only,
    require_validation_token,
)
from apps.api.rows import AgentVersion, Run
from apps.api.schemas import (
    BootstrapResponse, EgressResponseEnvelope, NotifyEmailResponse, SandboxEvent,
    SandboxResult, ValidationPayload,
)
from apps.api.services import containers as container_service
from apps.api.services import (
    agent_credentials, agent_secrets, agent_settings, egress, events, llm, notify, work,
)
from apps.api.services.runs import save_result
from apps.api.services.validation import save_validation

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


@router.get("/runs/{run_id}/bootstrap", response_model=BootstrapResponse)
async def bootstrap(run: Run = Depends(require_run_token)) -> BootstrapResponse:
    async with db.connection() as conn:
        rec = await conn.fetchrow(
            f"SELECT {AgentVersion.returning()} FROM agent_versions WHERE id = $1",
            run.agent_version_id,
        )
        if rec is None or rec["manifest"] is None:
            from apps.api.errors import ZentraError
            from apps.api.enums import ErrorCode
            raise ZentraError(ErrorCode.INTERNAL_ERROR, "run version manifest is missing", 500,
                              run_id=str(run.id))
        default_model = await agent_credentials.default_model_for(conn, run.agent_id)
        config = await agent_settings.config_for(conn, run.agent_id)
        secret_names = await agent_secrets.names_for(conn, run.agent_id)
    return BootstrapResponse(
        run_id=str(run.id), entrypoint=run.entrypoint, input=run.input,
        manifest=rec["manifest"],
        default_model=default_model,
        # **頂層欄位，不得合併進 manifest**（契約 §4.1 第 1 點）：manifest 是版本
        # 的不可變內容，把可變設定攪進去會讓 P5 在 runtime 這一側失去實體。
        # 每筆 Run 重算 —— 中途改設定，下一筆就生效。
        config=config,
        # **只有名字**：沒有值、沒有長度、沒有尾四碼（§4.1 第 5 點）。
        secret_names=secret_names,
    )


@router.post("/runs/{run_id}/events", status_code=202)
async def ingest_events(
    body: list[SandboxEvent],
    run: Run = Depends(require_run_token),
    redis: Redis = Depends(get_redis),
) -> dict:
    return await events.ingest_batch(redis, run.id, body)


@router.post("/runs/{run_id}/result", status_code=202)
async def ingest_result(
    body: SandboxResult,
    run: Run = Depends(require_run_token),
) -> dict:
    async with db.transaction() as conn:
        should_finalize = await save_result(conn, run.id, body)
    if should_finalize:
        from apps.api.tasks import dispatch_finalize
        await asyncio.to_thread(dispatch_finalize, str(run.id))
    return {"accepted": True}


@router.post("/versions/{version_id}/validation", status_code=202)
async def ingest_validation(
    body: ValidationPayload,
    version: AgentVersion = Depends(require_validation_token),
) -> dict:
    async with db.transaction() as conn:
        await save_validation(conn, version.id, body)
    return {"accepted": True}


@router.post("/llm/v1/{upstream_path:path}")
async def llm_proxy(
    upstream_path: str,
    request: Request,
    run: Run = Depends(require_run_token_only),
) -> Response:
    """OpenAI 相容的 LLM 代理（契約 §4，SPEC §7.3）。

    路徑裡刻意**沒有** `run_id` —— 它要與 OpenAI 相容，`ctx.llm()` 才能只是一個
    設了 `base_url` 的標準 chat model。歸屬由 run token 決定，呼叫者指定不了別人的 run。

    `{upstream_path}` 走白名單（`llm.UPSTREAM_PATHS`）：`chat/completions` 與
    `responses`。**不是任意轉發** —— 這個代理握著真金鑰，能打什麼由平台決定。
    白名單外的路徑回 404，而不是默默轉出去。

    回應原樣轉回沙盒（含供應商的錯誤 body），只拿掉不該外流的 header。
    """
    body = await request.json()
    status, headers, raw = await llm.proxy(
        run.id, run.agent_id, upstream_path.strip("/"), body, request.headers
    )
    return Response(
        content=raw,
        status_code=status,
        headers=headers,
        media_type=headers.get("content-type", "application/json"),
    )


@router.post("/egress/request", response_model=EgressResponseEnvelope)
async def egress_request(
    request: Request,
    run: Run = Depends(require_run_token_only),
) -> EgressResponseEnvelope:
    """對外 HTTP 代打（契約 §4.2 / ADR-0014 §3）。

    路徑裡刻意**沒有** `run_id`：歸屬由 run token 決定，沙盒指定不了別人的 run，
    而稽核要綁得到 `run_id`（§2.10）。

    **信封語意**：只要請求真的送出去了，這裡一律回 `200`，上游的狀態碼在
    `status` 裡。上游回 418／429／500 不是平台錯誤 —— 那是「Binance 主源、
    OKX 備援」能實作的前提。Gateway 自己的錯誤才用 §0 的錯誤格式。

    檢查順序與每一條的理由見 `services/egress.py`。

    **body 刻意不由 FastAPI 綁定**：綁定失敗會在進到 handler 之前就回 `422`，
    那條路徑寫不了 `egress_calls` —— 而契約 §4.2 第 8 點要求「驗完 token 之後
    的每一條 return 路徑都要先寫稽核」。形狀仍然是 `schemas.EgressRequest`。
    """
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    envelope = await egress.perform(
        run_id=run.id, agent_id=run.agent_id, payload=payload)
    return EgressResponseEnvelope(
        status=envelope.status, headers=envelope.headers, body=envelope.body)


@router.post("/notify/email", status_code=202, response_model=NotifyEmailResponse)
async def notify_email(
    request: Request,
    run: Run = Depends(require_run_token_only),
) -> NotifyEmailResponse:
    """寄信（契約 §4.4 / ADR-0014 §4）。

    寄件者、帳號、密碼都由平台決定（§2.9）—— request 指定不了 `from`、
    指定不了 SMTP 主機、帶不了密碼（`extra="forbid"` 直接 422）。

    **`202` 表示 SMTP 伺服器收下了，不表示對方讀到了。**
    **不冪等**：重送就是再寄一封。

    body 不由 FastAPI 綁定，理由同 `/egress/request`：`422` 也要寫稽核列。
    """
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    message_id = await notify.send(run_id=run.id, agent_id=run.agent_id, payload=payload)
    return NotifyEmailResponse(accepted=True, message_id=message_id)


@router.get("/agents/{agent_id}/work")
async def take_work(
    agent_id: str,
    bound: tuple[str, str] = Depends(require_container_token),
    redis: Redis = Depends(get_redis),
) -> Response:
    """常駐容器 long-poll 取工作（ZEN-015 / ADR-0012）。

    **這是「容器拉、平台不推」的那一條路。** 讓平台推的話容器得監聽連線，
    SPEC §8.1「容器內不開對外 HTTP port」與 L1 的「沙盒只有一個對外方向」就失效了。
    用拉的，網路拓撲一個字都不用改。

    回應：
      · `200` + 工作項（含**那一個 Run 的** run token）
      · `204` 逾時，沒有工作 —— 容器應該立刻再打一次

    **取工作本身就是健康訊號**：容器能來要工作，表示它的 event loop 沒被卡死、
    網路通、憑證有效。那正是我們想知道的全部，所以不另外做 `/healthz`。
    """
    import uuid as _uuid

    bound_agent_id, bound_container_id = bound
    taker = _uuid.UUID(bound_container_id) if bound_container_id else None

    # **要知道這個容器綁哪個版本**：佇列是一個版本一條（P5）。
    # 舊 token（沒帶容器 id）時退回「這個 Agent 只有一個活著的容器」那種情況 ——
    # 那正是多版本並存還不會發生的狀態，所以退回去是安全的。
    async with db.transaction() as conn:
        row = await container_service.resolve_for_work(
            conn, _uuid.UUID(bound_agent_id), taker)
        if row is None:
            return Response(status_code=204)
        await container_service.touch_health(conn, row.id)

    item = await work.pop(redis, bound_agent_id, row.agent_version_id)
    if item is None:
        return Response(status_code=204)

    # **記在真正取走它的容器頭上。** 佇列是 per-agent 的（多個容器共用一條，
    # 先空出來的先拿），所以派工端猜的那個容器不一定就是執行的那個。
    # 不校正的話，畫面會顯示一個閒著的容器滿載、一個滿載的容器閒著。
    async with db.transaction() as conn:
        await container_service.attribute_run(conn, row.id, item.get("run_id"))

    return Response(
        content=json.dumps(item),
        status_code=200,
        media_type="application/json",
    )
