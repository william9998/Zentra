"""Token-authenticated Gateway; reachable on port 8000, hidden from OpenAPI."""

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import get_session
from apps.api.deps import get_redis, require_run_token, require_run_token_only
from apps.api.models import Run
from apps.api.schemas import BootstrapResponse, SandboxEvent, SandboxResult, ValidationPayload
from apps.api.services import events, llm
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
