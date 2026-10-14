"""Public Run API from API contract v0.3.1."""

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import get_session
from apps.api.deps import get_redis, load_run, optional_agent_api_key
from apps.api.enums import ErrorCode, RunStatus, RunTrigger
from apps.api.errors import ZentraError
from apps.api.models import Agent, AgentVersion, Run
from apps.api.schemas import (
    CreateRunRequest, CreateRunResponse, RunListResponse, RunResponse, RunSummary,
)
from apps.api.services import events, pagination
from apps.api.services.runs import create_run as create_run_record
from apps.api.services.streaming import event_stream
from apps.api.tasks import dispatch_run

router = APIRouter(prefix="/v1", tags=["runs"])


async def _to_response(run: Run, session: AsyncSession) -> RunResponse:
    version = await session.get(AgentVersion, run.agent_version_id)
    if version is None:
        raise ZentraError(ErrorCode.INTERNAL_ERROR, "run version metadata is missing", 500,
                          run_id=str(run.id))
    return RunResponse(
        run_id=str(run.id), agent_id=str(run.agent_id), agent_version=version.semver,
        status=run.status, trigger=run.trigger, input=run.input,
        output=run.output, error=run.error, usage=run.usage,
        created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
    )


@router.post("/agents/{agent_id}/runs", status_code=202, response_model=CreateRunResponse)
async def create_run(
    agent_id: str, body: CreateRunRequest, response: Response,
    session: AsyncSession = Depends(get_session),
    api_key = Depends(optional_agent_api_key),
) -> CreateRunResponse:
    run, version = await create_run_record(
        session, agent_id, body, trigger=RunTrigger.API,
        api_key_id=api_key.id if api_key else None,
    )
    # The worker publishes QUEUED along with subsequent events. Broker I/O is
    # synchronous, so never run send_task on FastAPI's event loop.
    await asyncio.to_thread(dispatch_run, str(run.id))
    response.headers["Location"] = f"/v1/runs/{run.id}"
    return CreateRunResponse(
        run_id=str(run.id), agent_id=agent_id, agent_version=version.semver,
        status=RunStatus.QUEUED, created_at=run.created_at,
        events_url=f"/v1/runs/{run.id}/events",
    )


@router.post("/agents/{agent_id}/runs:invoke", response_model=RunResponse)
async def invoke_run(
    agent_id: str,
    body: CreateRunRequest,
    session: AsyncSession = Depends(get_session),
    api_key = Depends(optional_agent_api_key),
) -> RunResponse:
    run, _ = await create_run_record(
        session, agent_id, body, trigger=RunTrigger.API,
        api_key_id=api_key.id if api_key else None,
    )
    await asyncio.to_thread(dispatch_run, str(run.id))
    deadline = asyncio.get_running_loop().time() + 60
    while asyncio.get_running_loop().time() < deadline:
        await session.refresh(run)
        if RunStatus(run.status).is_terminal:
            return await _to_response(run, session)
        # 最多等 60 秒。每輪結束交易，sleep 期間才不會占著池子裡的連線。
        await session.commit()
        await asyncio.sleep(0.1)
    raise ZentraError(
        ErrorCode.SANDBOX_TIMEOUT, "synchronous invocation exceeded 60 seconds", 504,
        run_id=str(run.id),
    )


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    agent_id: str | None = None,
    api_key_id: str | None = None,
    status: RunStatus | None = None,
    trigger: RunTrigger | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> RunListResponse:
    statement = select(Run, AgentVersion.semver, Agent.name).join(
        AgentVersion, AgentVersion.id == Run.agent_version_id
    ).join(Agent, Agent.id == Run.agent_id)
    if agent_id:
        try:
            statement = statement.where(Run.agent_id == uuid.UUID(agent_id))
        except ValueError as exc:
            raise ZentraError(
                ErrorCode.VALIDATION_ERROR, "agent_id is invalid", 422,
                details={"field": "agent_id"},
            ) from exc
    if api_key_id:
        # 「這把 key 打進來的哪幾筆失敗了」＝ api_key_id + status=FAILED（ZEN-015 §3.3）
        try:
            statement = statement.where(Run.api_key_id == uuid.UUID(api_key_id))
        except ValueError as exc:
            raise ZentraError(
                ErrorCode.VALIDATION_ERROR, "api_key_id is invalid", 422,
                details={"field": "api_key_id"},
            ) from exc
    if status is not None:
        statement = statement.where(Run.status == status)
    if trigger is not None:
        statement = statement.where(Run.trigger == trigger)
    if created_after is not None:
        statement = statement.where(Run.created_at >= created_after)
    if created_before is not None:
        statement = statement.where(Run.created_at <= created_before)
    if cursor:
        cursor_created_at, cursor_id = pagination.decode(cursor)
        statement = statement.where(or_(
            Run.created_at < cursor_created_at,
            and_(Run.created_at == cursor_created_at, Run.id < cursor_id),
        ))
    rows = (await session.execute(
        statement.order_by(desc(Run.created_at), desc(Run.id)).limit(limit + 1)
    )).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return RunListResponse(
        items=[RunSummary(
            run_id=str(run.id), agent_id=str(run.agent_id), agent_name=name,
            agent_version=semver,
            status=run.status, trigger=run.trigger, created_at=run.created_at,
            started_at=run.started_at, finished_at=run.finished_at,
        ) for run, semver, name in rows],
        next_cursor=(
            pagination.encode(rows[-1][0].created_at, rows[-1][0].id)
            if has_more and rows else None
        ),
    )


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> RunResponse:
    return await _to_response(await load_run(run_id, session), session)


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
