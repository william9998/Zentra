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

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from redis import Redis

from apps.api.enums import EventType
from apps.api.ids import uuid7
from apps.api.rows import Run, RunEvent
from apps.api.services.events import STREAM_TTL_SECONDS, stream_key


class RunNotFound(Exception):
    """lock_run 找不到這一列。對齊舊 scalar_one() 的 NoResultFound。"""


def _require_txn(conn: psycopg.Connection) -> None:
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError("lock functions must be called inside db.transaction()")


def lock_run(conn: psycopg.Connection, run_id: uuid.UUID) -> Run:
    """鎖住這一列 Run。**需在交易內呼叫。**"""
    _require_txn(conn)
    rec = conn.execute(
        f"SELECT {Run.returning()} FROM runs WHERE id = %(id)s FOR UPDATE",
        {"id": run_id},
    ).fetchone()
    if rec is None:
        raise RunNotFound(str(run_id))
    return Run.from_row(rec)


def add_status_event(
    conn: psycopg.Connection, run: Run, *, terminal: bool = False,
) -> RunEvent:
    """呼叫端必須持有 Run 的 row lock，並把狀態與事件一起 commit。

    `terminal=True` 只能出現一次 —— 資料庫的 partial unique index 會兜底。
    """
    rec = conn.execute(
        f"""
        INSERT INTO run_events (run_id, event_id, type, payload, terminal)
        VALUES (%(run_id)s, %(event_id)s, %(type)s, %(payload)s, %(terminal)s)
        RETURNING {RunEvent.returning()}
        """,
        {
            "run_id": run.id,
            "event_id": uuid7(),
            "type": str(EventType.RUN_STATUS),
            "payload": Jsonb({"status": str(run.status)}),
            "terminal": terminal,
        },
    ).fetchone()
    return RunEvent.from_row(rec)


def publish_pending(redis: Redis, run_id: uuid.UUID) -> None:
    """把尚未通知 SSE 的事件推上 Redis，再標記 published_at。

    Redis 是喚醒，不是真相。XADD 失敗時 `published_at` 保持 NULL，下次補發；
    XADD 成功但標記失敗時，重複通知是安全的（消費者用 DB seq 去重）。
    **不在交易內等 Redis。**
    """
    from apps.worker import db

    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT {RunEvent.returning()} FROM run_events "
            "WHERE run_id = %(id)s AND published_at IS NULL ORDER BY seq",
            {"id": run_id},
        ).fetchall()
    if not rows:
        return
    events = [RunEvent.from_row(row) for row in rows]
    try:
        key = stream_key(run_id)
        for row in events:
            redis.xadd(key, {
                "seq": str(row.seq), "type": row.type,
                "data": json.dumps(row.payload, ensure_ascii=False),
            })
            redis.expire(key, STREAM_TTL_SECONDS)
    except BaseException:
        raise
    with db.transaction() as conn:
        conn.execute(
            "UPDATE run_events SET published_at = %(ts)s WHERE seq = ANY(%(seqs)s)",
            {"ts": datetime.now(UTC), "seqs": [row.seq for row in events]},
        )
