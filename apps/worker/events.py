"""worker 側的事件寫入（同步版），對齊 `apps/api/services/events.py` 的語意。

兩個關鍵不變量，兩邊必須一致：
1. **事件 id 由產生端給**（UUIDv7），所以重送不會產生重複列。
2. **狀態轉換與它的 run.status 事件在同一個 transaction 裡 commit**，
   之後才發 Redis。Redis 發失敗不會讓事件消失 —— `publish_pending` 會補發。

api 走 async、worker 走 sync，是兩份實作。ZEN-004 若要收斂成一份，從這裡開始。
"""

import json
import uuid
from datetime import UTC, datetime

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.enums import EventType
from apps.api.ids import uuid7
from apps.api.models import Run, RunEvent
from apps.api.services.events import STREAM_TTL_SECONDS, stream_key


def lock_run(session: Session, run_id: uuid.UUID) -> Run:
    return session.execute(
        select(Run).where(Run.id == run_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def add_status_event(session: Session, run: Run, *, terminal: bool = False) -> RunEvent:
    """呼叫端必須持有 Run 的 row lock，並把狀態與事件一起 commit。

    `terminal=True` 只能出現一次 —— 資料庫的 partial unique index 會兜底。
    """
    row = RunEvent(
        run_id=run.id, event_id=uuid7(), type=EventType.RUN_STATUS,
        payload={"status": str(run.status)}, terminal=terminal,
    )
    session.add(row)
    return row


def publish_pending(session: Session, redis: Redis, run_id: uuid.UUID) -> None:
    """把還沒進 Redis 的事件補發出去，不重新配 event_id 或 seq。

    XADD 成功但 commit 失敗時會重複通知 —— 那是安全的：
    消費端是用 seq 去資料庫取有序的列，不是直接信 Redis 的內容。
    """
    rows = session.execute(
        select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.published_at.is_(None))
        .order_by(RunEvent.seq).with_for_update()
    ).scalars().all()
    try:
        key = stream_key(run_id)
        for row in rows:
            redis.xadd(key, {
                "seq": str(row.seq), "type": row.type,
                "data": json.dumps(row.payload, ensure_ascii=False),
            })
            redis.expire(key, STREAM_TTL_SECONDS)
            row.published_at = datetime.now(UTC)
        session.commit()
    except BaseException:
        session.rollback()
        raise
