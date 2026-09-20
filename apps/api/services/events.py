"""事件匯流：持久化寫 Postgres，即時推送走 Redis Stream。

架構文件 §8：**持久化的真相在 Postgres**，Redis Stream 只是即時管道。
沙盒不得直連這兩者 —— 它只會 POST 到 Internal Gateway，由這裡代寫（SPEC §5）。
"""

import json
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import EventType
from apps.api.models import RunEvent

STREAM_TTL_SECONDS = 3600


def stream_key(run_id: uuid.UUID | str) -> str:
    return f"run:{run_id}"


async def append_event(
    session: AsyncSession,
    redis: Redis,
    run_id: uuid.UUID,
    event_type: EventType | str,
    payload: dict[str, Any],
) -> int:
    """寫一筆事件。回傳 seq（就是 SSE 的 event id）。"""
    row = RunEvent(run_id=run_id, type=str(event_type), payload=payload)
    session.add(row)
    await session.flush()          # 取得 seq
    await session.commit()

    key = stream_key(run_id)
    await redis.xadd(
        key,
        {"seq": str(row.seq), "type": str(event_type), "data": json.dumps(payload, ensure_ascii=False)},
    )
    await redis.expire(key, STREAM_TTL_SECONDS)
    return row.seq
