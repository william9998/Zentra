"""SSE uses Redis XREAD for notifications and DB seq for ordering/dedup."""

import asyncio
import json
from uuid import UUID

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from apps.api.rows import RunEvent
from apps.api.services.events import stream_key


async def event_stream(redis: Redis, run_id: UUID, request: Request):
    from apps.api import db

    last_seq = 0
    redis_id = "0-0"
    while not await request.is_disconnected():
        async with db.connection() as conn:
            recs = await conn.fetch(
                f"SELECT {RunEvent.returning()} FROM run_events "
                "WHERE run_id = $1 AND seq > $2 ORDER BY seq LIMIT 200",
                run_id, last_seq,
            )
        rows = [RunEvent.from_row(rec) for rec in recs]
        for row in rows:
            last_seq = row.seq
            yield (f"id: {row.seq}\nevent: {row.type}\n"
                   f"data: {json.dumps(row.payload, ensure_ascii=False)}\n\n")
            if row.terminal:
                return
        if len(rows) == 200:
            continue
        try:
            batch = await redis.xread({stream_key(run_id): redis_id}, count=200, block=1000)
            for _, entries in batch:
                if entries:
                    redis_id = entries[-1][0]
        except RedisError:
            # A notification outage must not hide already committed events.
            await asyncio.sleep(1)
        if not rows:
            yield ": keepalive\n\n"
