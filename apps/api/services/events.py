"""Postgres owns event identity/order; Redis only wakes SSE consumers."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from redis.asyncio import Redis

from apps.api.enums import ErrorCode, EventType, RunStatus
from apps.api.errors import ZentraError, not_found
from apps.api.ids import uuid7
from apps.api.rows import Run, RunEvent
from apps.api.schemas import SandboxEvent

STREAM_TTL_SECONDS = 3600


def stream_key(run_id: uuid.UUID | str) -> str:
    return f"run:{run_id}"


def _require_txn(conn: asyncpg.Connection) -> None:
    if not conn.is_in_transaction():
        raise RuntimeError("lock functions must be called inside db.transaction()")


async def lock_run(conn: asyncpg.Connection, run_id: uuid.UUID) -> Run:
    """鎖住這一列 Run。**需在交易內呼叫。**"""
    _require_txn(conn)
    rec = await conn.fetchrow(
        f"SELECT {Run.returning()} FROM runs WHERE id = $1 FOR UPDATE", run_id)
    if rec is None:
        raise not_found("run")
    return Run.from_row(rec)


def require_active(run: Run) -> None:
    if RunStatus(run.status).is_terminal:
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "run token revoked", 401)


async def status_event(
    conn: asyncpg.Connection, run: Run, *, terminal: bool = False,
) -> RunEvent:
    """Caller holds the Run row lock and writes state + event together."""
    rec = await conn.fetchrow(
        f"""
        INSERT INTO run_events (run_id, event_id, type, payload, terminal)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING {RunEvent.returning()}
        """,
        run.id, uuid7(), str(EventType.RUN_STATUS),
        {"status": str(run.status)}, terminal,
    )
    return RunEvent.from_row(rec)


async def _unpublished(conn: asyncpg.Connection, run_id: uuid.UUID) -> list[RunEvent]:
    rows = await conn.fetch(
        f"SELECT {RunEvent.returning()} FROM run_events "
        "WHERE run_id = $1 AND published_at IS NULL ORDER BY seq",
        run_id,
    )
    return [RunEvent.from_row(row) for row in rows]


async def _mark_published(conn: asyncpg.Connection, seqs: list[int]) -> None:
    if not seqs:
        return
    await conn.execute(
        "UPDATE run_events SET published_at = $1 WHERE seq = ANY($2::bigint[])",
        datetime.now(UTC), seqs,
    )


async def publish_pending(redis: Redis, run_id: uuid.UUID) -> None:
    """把尚未通知 SSE 的事件推上 Redis，再標記 published_at。

    Redis 是喚醒，不是真相。XADD 失敗時 `published_at` 保持 NULL，下次補發；
    XADD 成功但標記失敗時，重複通知是安全的（消費者用 DB seq 去重）。
    **不在交易內等 Redis。**
    """
    from apps.api import db

    async with db.connection() as conn:
        rows = await _unpublished(conn, run_id)
    if not rows:
        return
    try:
        for row in rows:
            key = stream_key(run_id)
            await redis.xadd(key, {
                "seq": str(row.seq), "type": row.type,
                "data": json.dumps(row.payload, ensure_ascii=False),
            })
            await redis.expire(key, STREAM_TTL_SECONDS)
    except BaseException:
        raise
    async with db.transaction() as conn:
        await _mark_published(conn, [row.seq for row in rows])


async def ingest_batch(
    redis: Redis, run_id: uuid.UUID, batch: list[SandboxEvent],
) -> dict[str, Any]:
    from apps.api import db

    async with db.transaction() as conn:
        run = await lock_run(conn, run_id)
        require_active(run)
        accepted = 0
        last_seq = None
        for event in batch:
            existing = await conn.fetchrow(
                f"SELECT {RunEvent.returning()} FROM run_events "
                "WHERE run_id = $1 AND event_id = $2",
                run_id, event.event_id,
            )
            if existing is None:
                rec = await conn.fetchrow(
                    f"""
                    INSERT INTO run_events (run_id, event_id, type, payload)
                    VALUES ($1, $2, $3, $4)
                    RETURNING {RunEvent.returning()}
                    """,
                    run_id, event.event_id, event.type, event.payload,
                )
                accepted += 1
                last_seq = rec["seq"]
            else:
                row = RunEvent.from_row(existing)
                if row.type != event.type or row.payload != event.payload:
                    raise ZentraError(
                        ErrorCode.VALIDATION_ERROR,
                        "event_id already belongs to a different event", 422,
                        run_id=str(run_id), details={"field": "event_id"},
                    )
                last_seq = row.seq
    await publish_pending(redis, run_id)
    return {"accepted": accepted, "last_seq": last_seq}
