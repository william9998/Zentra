"""Token-authenticated Gateway; reachable on port 8000, hidden from OpenAPI."""

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import get_session
from apps.api.deps import get_redis, require_run_token
from apps.api.models import Run
from apps.api.schemas import BootstrapResponse, SandboxEvent, SandboxResult
from apps.api.services import events
from apps.api.services.runs import BUILTIN_MANIFEST, save_result

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


@router.get("/runs/{run_id}/bootstrap", response_model=BootstrapResponse)
async def bootstrap(run: Run = Depends(require_run_token)) -> BootstrapResponse:
    return BootstrapResponse(
        run_id=str(run.id), entrypoint=run.entrypoint, input=run.input,
        manifest=BUILTIN_MANIFEST,
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
