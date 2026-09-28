"""Token-authenticated Gateway; reachable on port 8000, hidden from OpenAPI."""

import json

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import get_session
from apps.api.deps import (
    get_redis, require_container_token, require_run_token, require_run_token_only,
)
from apps.api.models import Run
from apps.api.schemas import BootstrapResponse, SandboxEvent, SandboxResult, ValidationPayload
from apps.api.services import containers as container_service
from apps.api.services import agent_credentials, events, llm, work
from apps.api.services.runs import save_result
from apps.api.services.validation import save_validation
from apps.api.deps import require_validation_token
from apps.api.models import AgentVersion

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


@router.get("/runs/{run_id}/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    run: Run = Depends(require_run_token),
    session: AsyncSession = Depends(get_session),
) -> BootstrapResponse:
    version = await session.get(AgentVersion, run.agent_version_id)
    if version is None or version.manifest is None:
        from apps.api.errors import ZentraError
        from apps.api.enums import ErrorCode
        raise ZentraError(ErrorCode.INTERNAL_ERROR, "run version manifest is missing", 500,
                          run_id=str(run.id))
    return BootstrapResponse(
        run_id=str(run.id), entrypoint=run.entrypoint, input=run.input,
        manifest=version.manifest,
        default_model=await agent_credentials.default_model_for(session, run.agent_id),
    )


@router.post("/runs/{run_id}/events", status_code=202)
async def ingest_events(
    body: list[SandboxEvent],
    run: Run = Depends(require_run_token),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict:
    return await events.ingest_batch(session, redis, run.id, body)


@router.post("/runs/{run_id}/result", status_code=202)
async def ingest_result(
    body: SandboxResult,
    run: Run = Depends(require_run_token),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await save_result(session, run.id, body)
    return {"accepted": True}


@router.post("/versions/{version_id}/validation", status_code=202)
async def ingest_validation(
    body: ValidationPayload,
    version: AgentVersion = Depends(require_validation_token),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await save_validation(session, version.id, body)
    return {"accepted": True}


@router.post("/llm/v1/{upstream_path:path}")
async def llm_proxy(
    upstream_path: str,
    request: Request,
    run: Run = Depends(require_run_token_only),
    session: AsyncSession = Depends(get_session),
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
        session, run.id, run.agent_id, upstream_path.strip("/"), body, request.headers
    )
    return Response(
        content=raw,
        status_code=status,
        headers=headers,
        media_type=headers.get("content-type", "application/json"),
    )


@router.get("/agents/{agent_id}/work")
async def take_work(
    agent_id: str,
    bound: tuple[str, str] = Depends(require_container_token),
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
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
    if taker is not None:
        await container_service.touch_health(session, taker)
    else:
        # 舊 token（v0.9.0 之前發的）沒帶容器 id。退回照 Agent 更新，
        # 不要因此拒絕服務 —— 那會讓升級時所有在跑的容器一起變啞。
        await container_service.touch_health_by_agent(session, _uuid.UUID(bound_agent_id))

    item = await work.pop(redis, bound_agent_id)
    if item is None:
        return Response(status_code=204)

    # **記在真正取走它的容器頭上。** 佇列是 per-agent 的（多個容器共用一條，
    # 先空出來的先拿），所以派工端猜的那個容器不一定就是執行的那個。
    # 不校正的話，畫面會顯示一個閒著的容器滿載、一個滿載的容器閒著。
    if taker is not None:
        await container_service.attribute_run(session, taker, item.get("run_id"))

    return Response(
        content=json.dumps(item),
        status_code=200,
        media_type="application/json",
    )
