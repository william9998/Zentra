"""Public Run subset from API v0.2.2; full Run API remains ZEN-004."""

import asyncio
import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import get_session
from apps.api.deps import get_redis, load_run
from apps.api.enums import BUILTIN_AGENT_SEMVER, BUILTIN_AGENT_VERSION_ID, RunStatus, RunTrigger
from apps.api.ids import uuid7
from apps.api.models import Run
from apps.api.schemas import CreateRunRequest, CreateRunResponse, RunResponse
from apps.api.services import events
from apps.api.services.runs import validate_fixture
from apps.api.services.streaming import event_stream
from apps.api.tasks import dispatch_run

router = APIRouter(prefix="/v1", tags=["runs"])


def _to_response(run: Run) -> RunResponse:
    return RunResponse(
        run_id=str(run.id), agent_id=str(run.agent_id), agent_version=BUILTIN_AGENT_SEMVER,
        status=run.status, trigger=run.trigger, input=run.input,
        output=run.output, error=run.error, usage=run.usage,
        created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
    )


@router.post("/agents/{agent_id}/runs", status_code=202, response_model=CreateRunResponse)
async def create_run(
    agent_id: str, body: CreateRunRequest, response: Response,
    session: AsyncSession = Depends(get_session),
) -> CreateRunResponse:
    validate_fixture(agent_id, body)
    run_id = uuid7()
    run = Run(
        id=run_id, agent_id=uuid.UUID(agent_id),
        agent_version_id=uuid.UUID(BUILTIN_AGENT_VERSION_ID),
        status=RunStatus.QUEUED, trigger=RunTrigger.API,
        entrypoint=body.entrypoint, input=body.input,
        timeout_seconds=body.config.timeout_seconds, celery_task_id=str(run_id),
    )
    session.add(run)
    await session.flush()
    events.status_event(session, run)
    await session.commit()
    # The worker publishes QUEUED along with subsequent events. Broker I/O is
    # synchronous, so never run send_task on FastAPI's event loop.
    await asyncio.to_thread(dispatch_run, str(run_id))
    response.headers["Location"] = f"/v1/runs/{run_id}"
    return CreateRunResponse(
        run_id=str(run_id), agent_id=agent_id, agent_version=BUILTIN_AGENT_SEMVER,
        status=RunStatus.QUEUED, created_at=run.created_at,
        events_url=f"/v1/runs/{run_id}/events",
    )


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> RunResponse:
    return _to_response(await load_run(run_id, session))


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str, request: Request,
    session: AsyncSession = Depends(get_session), redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    run = await load_run(run_id, session)
    return StreamingResponse(
        event_stream(session, redis, run.id, request), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
