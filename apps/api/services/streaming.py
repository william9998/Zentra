"""SSE uses Redis XREAD for notifications and DB seq for ordering/dedup."""

import asyncio
import json
from uuid import UUID

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models import RunEvent
from apps.api.services.events import stream_key


async def event_stream(session: AsyncSession, redis: Redis, run_id: UUID, request: Request):
    last_seq = 0
    redis_id = "0-0"
    while not await request.is_disconnected():
        rows = (await session.execute(
            select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > last_seq)
            .order_by(RunEvent.seq).limit(200)
        )).scalars().all()
        # Do not hold a DB connection/transaction across the blocking XREAD.
        await session.commit()
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
