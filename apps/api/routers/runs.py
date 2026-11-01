"""Public Run API from API contract v0.3.1."""

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from apps.api import db
from apps.api.deps import get_redis, load_run, optional_agent_api_key
from apps.api.enums import ErrorCode, RunStatus, RunTrigger
from apps.api.errors import ZentraError
from apps.api.schemas import (
    CreateRunRequest, CreateRunResponse, RunListResponse, RunResponse, RunSummary,
)
from apps.api.services.runs import create_run as create_run_record
from apps.api.services.runs import creation_rejection
from apps.api.services.runs import list_runs as list_run_rows
from apps.api.services.runs import to_response, version_semver
from apps.api.services.streaming import event_stream
from apps.api.tasks import dispatch_run

router = APIRouter(prefix="/v1", tags=["runs"])


@router.post("/agents/{agent_id}/runs", status_code=202, response_model=CreateRunResponse)
async def create_run(
    agent_id: str, body: CreateRunRequest, response: Response,
    api_key = Depends(optional_agent_api_key),
) -> CreateRunResponse:
    async with db.transaction() as conn:
        run, version = await create_run_record(
            conn, agent_id, body, trigger=RunTrigger.API,
            api_key_id=api_key.id if api_key else None,
        )
    # 版本被存活檢查放棄：Run 已經以 FAILED 留下紀錄，不派工，回 409。
    if (rejection := creation_rejection(run)) is not None:
        raise rejection
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
    api_key = Depends(optional_agent_api_key),
) -> RunResponse:
    async with db.transaction() as conn:
        run, version = await create_run_record(
            conn, agent_id, body, trigger=RunTrigger.API,
            api_key_id=api_key.id if api_key else None,
        )
    if (rejection := creation_rejection(run)) is not None:
        raise rejection
    await asyncio.to_thread(dispatch_run, str(run.id))
    deadline = asyncio.get_running_loop().time() + 60
    while asyncio.get_running_loop().time() < deadline:
        async with db.connection() as conn:
            current = await load_run(str(run.id), conn)
            if RunStatus(current.status).is_terminal:
                return to_response(current, version.semver)
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
) -> RunListResponse:
    parsed_agent = None
    if agent_id:
        try:
            parsed_agent = uuid.UUID(agent_id)
        except ValueError as exc:
            raise ZentraError(
                ErrorCode.VALIDATION_ERROR, "agent_id is invalid", 422,
                details={"field": "agent_id"},
            ) from exc
    parsed_key = None
    if api_key_id:
        try:
            parsed_key = uuid.UUID(api_key_id)
        except ValueError as exc:
            raise ZentraError(
                ErrorCode.VALIDATION_ERROR, "api_key_id is invalid", 422,
                details={"field": "api_key_id"},
            ) from exc
    async with db.connection() as conn:
        items, next_cursor = await list_run_rows(
            conn, agent_id=parsed_agent, api_key_id=parsed_key, status=status,
            trigger=trigger, created_after=created_after, created_before=created_before,
            limit=limit, cursor=cursor,
        )
    return RunListResponse(
        items=[RunSummary(**item) for item in items],
        next_cursor=next_cursor,
    )


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str) -> RunResponse:
    async with db.connection() as conn:
        run = await load_run(run_id, conn)
        semver = await version_semver(conn, run.agent_version_id)
    if semver is None:
        raise ZentraError(ErrorCode.INTERNAL_ERROR, "run version metadata is missing", 500,
                          run_id=str(run.id))
    return to_response(run, semver)


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str, request: Request, redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    async with db.connection() as conn:
        await load_run(run_id, conn)
    return StreamingResponse(
        event_stream(redis, uuid.UUID(run_id), request), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
