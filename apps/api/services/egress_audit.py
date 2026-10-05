"""出口稽核查詢（ZEN-019 / api-contract §2.10）。

形狀刻意與 `usage.list_calls()`（ZEN-015）一樣：篩選 + 游標分頁 + 逐筆表。
**HTTP 與寄信在同一張表**，用 `kind` 分 —— 「這個 Agent 往外送了什麼」
是一個問題，不該有兩個要分別查的地方（ADR-0014 M4）。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models import EgressCall
from apps.api.services import pagination


@dataclass(frozen=True)
class CallFilters:
    """§2.10 列出的可篩欄位。全部選填，全部可以疊加。"""

    kind: str | None = None
    run_id: uuid.UUID | None = None
    since: datetime | None = None
    until: datetime | None = None
    host: str | None = None
    status_code: int | None = None
    #: 等於 `error IS NOT NULL OR status_code >= 400`（同 §2.4 第 4 點）。
    #: 除錯時第一個動作就是它。
    errors_only: bool = False


def _apply(statement: Select, agent_id: uuid.UUID, filters: CallFilters) -> Select:
    statement = statement.where(EgressCall.agent_id == agent_id)
    if filters.kind:
        statement = statement.where(EgressCall.kind == filters.kind)
    if filters.run_id is not None:
        statement = statement.where(EgressCall.run_id == filters.run_id)
    if filters.since is not None:
        statement = statement.where(EgressCall.created_at >= filters.since)
    if filters.until is not None:
        statement = statement.where(EgressCall.created_at <= filters.until)
    if filters.host:
        statement = statement.where(EgressCall.host == filters.host)
    if filters.status_code is not None:
        statement = statement.where(EgressCall.status_code == filters.status_code)
    if filters.errors_only:
        statement = statement.where(
            (EgressCall.error.is_not(None)) | (EgressCall.status_code >= 400))
    return statement


def view(row: EgressCall) -> dict[str, Any]:
    return {
        "call_id": str(row.call_id),
        "run_id": str(row.run_id),
        "kind": row.kind,
        "rule_id": str(row.rule_id) if row.rule_id else None,
        "method": row.method,
        "host": row.host,
        # **不含 query string**（§2.10 第 1 點）。kind=EMAIL 時這裡是收件人。
        "path": row.path,
        "status_code": row.status_code,
        "request_bytes": row.request_bytes,
        "response_bytes": row.response_bytes,
        "latency_ms": row.latency_ms,
        "error": row.error,
        "created_at": row.created_at,
    }


async def list_calls(
    session: AsyncSession,
    agent_id: uuid.UUID,
    filters: CallFilters,
    *,
    limit: int,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """逐筆列出，最新的在前。回 `(items, next_cursor)`。"""
    statement = _apply(select(EgressCall), agent_id, filters)
    if cursor:
        cursor_created_at, cursor_id = pagination.decode(cursor)
        statement = statement.where(
            (EgressCall.created_at < cursor_created_at)
            | ((EgressCall.created_at == cursor_created_at)
               & (EgressCall.call_id < cursor_id))
        )
    rows = (await session.execute(
        statement.order_by(EgressCall.created_at.desc(), EgressCall.call_id.desc())
        .limit(limit + 1)
    )).scalars().all()

    has_more = len(rows) > limit
    rows = list(rows[:limit])
    next_cursor = (
        pagination.encode(rows[-1].created_at, rows[-1].call_id) if has_more and rows
        else None
    )
    return [view(row) for row in rows], next_cursor
