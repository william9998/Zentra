"""Postgres owns event identity/order; Redis only wakes SSE consumers."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import ErrorCode, EventType, RunStatus
from apps.api.errors import ZentraError
from apps.api.ids import uuid7
from apps.api.models import Run, RunEvent
from apps.api.schemas import SandboxEvent

STREAM_TTL_SECONDS = 3600


def stream_key(run_id: uuid.UUID | str) -> str:
    return f"run:{run_id}"


async def lock_run(session: AsyncSession, run_id: uuid.UUID) -> Run:
    # populate_existing matters: token authentication may already have loaded
    # this row before the worker committed its terminal transition.
    return (await session.execute(
        select(Run).where(Run.id == run_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one()


def require_active(run: Run) -> None:
    if RunStatus(run.status).is_terminal:
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "run token revoked", 401)


def status_event(session: AsyncSession, run: Run, *, terminal: bool = False) -> RunEvent:
    """Caller holds the Run row lock and commits state + event together."""
    row = RunEvent(
        run_id=run.id, event_id=uuid7(), type=EventType.RUN_STATUS,
        payload={"status": str(run.status)}, terminal=terminal,
    )
    session.add(row)
    return row


async def publish_pending(session: AsyncSession, redis: Redis, run_id: uuid.UUID) -> None:
    """Retry persisted events without allocating another event_id or seq.

    XADD may succeed just before a crash or failed DB commit. A duplicate Redis
    notification is safe: consumers fetch ordered DB rows after their last seq.
    """
    rows = (await session.execute(
        select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.published_at.is_(None))
        .order_by(RunEvent.seq).with_for_update()
    )).scalars().all()
    try:
        for row in rows:
            key = stream_key(run_id)
            await redis.xadd(key, {
                "seq": str(row.seq), "type": row.type,
                "data": json.dumps(row.payload, ensure_ascii=False),
            })
            await redis.expire(key, STREAM_TTL_SECONDS)
            row.published_at = datetime.now(UTC)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise


async def ingest_batch(
    session: AsyncSession, redis: Redis, run_id: uuid.UUID, batch: list[SandboxEvent],
) -> dict[str, Any]:
    run = await lock_run(session, run_id)
    require_active(run)
    accepted = 0
    last_seq = None
    for event in batch:
        row = (await session.execute(select(RunEvent).where(
            RunEvent.run_id == run_id, RunEvent.event_id == event.event_id,
        ))).scalar_one_or_none()
        if row is None:
            row = RunEvent(
                run_id=run_id, event_id=event.event_id,
                type=event.type, payload=event.payload,
            )
            session.add(row)
            await session.flush()
            accepted += 1
        elif row.type != event.type or row.payload != event.payload:
            raise ZentraError(
                ErrorCode.VALIDATION_ERROR, "event_id already belongs to a different event", 422,
                run_id=str(run_id), details={"field": "event_id"},
            )
        last_seq = row.seq
    await session.commit()
    await publish_pending(session, redis, run_id)
    return {"accepted": accepted, "last_seq": last_seq}
