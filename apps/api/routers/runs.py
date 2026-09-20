"""公開的 Run 端點 —— api-contract.md §1 的子集（ZEN-000 範圍）。

`cancel` / 列表 / `Last-Event-ID` 續傳留到 ZEN-004。
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.db import get_session
from apps.api.deps import get_redis, load_run
from apps.api.enums import (
    BUILTIN_AGENT_ID,
    BUILTIN_AGENT_SEMVER,
    BUILTIN_AGENT_VERSION_ID,
    EventType,
    RunStatus,
    RunTrigger,
)
from apps.api.errors import not_found
from apps.api.models import Run
from apps.api.schemas import CreateRunRequest, CreateRunResponse, RunResponse
from apps.api.services import events
from apps.api.tasks import dispatch_run

router = APIRouter(prefix="/v1", tags=["runs"])
settings = get_settings()


def _to_response(run: Run) -> RunResponse:
    return RunResponse(
        run_id=str(run.id),
        agent_id=str(run.agent_id),
        agent_version=BUILTIN_AGENT_SEMVER,
        status=run.status,
        trigger=run.trigger,
        input=run.input,
        output=run.output,
        error=run.error,
        usage=run.usage,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.post("/agents/{agent_id}/runs", status_code=202, response_model=CreateRunResponse)
async def create_run(
    agent_id: str,
    body: CreateRunRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> CreateRunResponse:
    # ZEN-000 沒有 Agent Registry，只有寫死在映像裡的 builtin agent（ZEN-001 才做真的）
    if agent_id != BUILTIN_AGENT_ID:
        raise not_found("agent")

    run = Run(
        id=uuid.uuid4(),
        agent_id=uuid.UUID(agent_id),
        agent_version_id=uuid.UUID(BUILTIN_AGENT_VERSION_ID),
        status=RunStatus.QUEUED,
        trigger=RunTrigger.API,
        entrypoint=body.entrypoint,
        input=body.input,
        timeout_seconds=body.config.timeout_seconds or settings.run_timeout_seconds,
    )
    session.add(run)
    await session.commit()

    await events.append_event(
        session, redis, run.id, EventType.RUN_STATUS, {"status": RunStatus.QUEUED}
    )
    task_id = dispatch_run(str(run.id))
    run.celery_task_id = task_id
    await session.commit()

    response.headers["Location"] = f"/v1/runs/{run.id}"
    return CreateRunResponse(
        run_id=str(run.id),
        agent_id=agent_id,
        agent_version=BUILTIN_AGENT_SEMVER,
        status=run.status,
        created_at=run.created_at,
        events_url=f"/v1/runs/{run.id}/events",
    )


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> RunResponse:
    return _to_response(await load_run(run_id, session))


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    """SSE。從 Redis Stream 的頭開始讀，所以連線晚了也拿得到完整事件流。

    `Last-Event-ID` 續傳（從 Postgres 補齊）留到 ZEN-004。
    """
    run = await load_run(run_id, session)
    key = events.stream_key(run.id)

    async def gen():
        last_id = "0-0"
        idle = 0.0
        while True:
            batch = await redis.xread({key: last_id}, count=200, block=1000)
            if not batch:
                idle += 1.0
                # 事件流結束後還沒收到終局事件（例如沙盒被 kill），別無限掛著
                if idle > 30:
                    break
                yield ": keepalive\n\n"
                continue

            idle = 0.0
            for _key, entries in batch:
                for entry_id, fields in entries:
                    last_id = entry_id
                    seq, etype = fields["seq"], fields["type"]
                    yield f"id: {seq}\nevent: {etype}\ndata: {fields['data']}\n\n"
                    if etype == EventType.RUN_STATUS:
                        status = json.loads(fields["data"]).get("status")
                        if status in tuple(RunStatus) and RunStatus(status).is_terminal:
                            return
            await asyncio.sleep(0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
