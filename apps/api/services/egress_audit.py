"""出口稽核查詢（ZEN-019 / api-contract §2.10）。

形狀刻意與 `usage.list_calls()`（ZEN-015）一樣：篩選 + 游標分頁 + 逐筆表。
**HTTP 與寄信在同一張表**，用 `kind` 分 —— 「這個 Agent 往外送了什麼」
是一個問題，不該有兩個要分別查的地方（ADR-0014 M4）。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from apps.api.rows import EgressCall
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


def _filters_sql(agent_id: uuid.UUID, filters: CallFilters) -> tuple[str, list[Any]]:
    """動態片段只從程式碼常數挑；值一律走參數。"""
    clauses = ["agent_id = $1"]
    args: list[Any] = [agent_id]
    n = 2
    if filters.kind:
        clauses.append(f"kind = ${n}")
        args.append(filters.kind)
        n += 1
    if filters.run_id is not None:
        clauses.append(f"run_id = ${n}")
        args.append(filters.run_id)
        n += 1
    if filters.since is not None:
        clauses.append(f"created_at >= ${n}")
        args.append(filters.since)
        n += 1
    if filters.until is not None:
        clauses.append(f"created_at <= ${n}")
        args.append(filters.until)
        n += 1
    if filters.host:
        clauses.append(f"host = ${n}")
        args.append(filters.host)
        n += 1
    if filters.status_code is not None:
        clauses.append(f"status_code = ${n}")
        args.append(filters.status_code)
        n += 1
    if filters.errors_only:
        clauses.append("(error IS NOT NULL OR status_code >= 400)")
    return " AND ".join(clauses), args


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
    conn: asyncpg.Connection,
    agent_id: uuid.UUID,
    filters: CallFilters,
    *,
    limit: int,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """逐筆列出，最新的在前。回 `(items, next_cursor)`。"""
    where, args = _filters_sql(agent_id, filters)
    if cursor:
        cursor_created_at, cursor_id = pagination.decode(cursor)
        n = len(args) + 1
        where += (
            f" AND (created_at < ${n} OR (created_at = ${n} AND call_id < ${n + 1}))"
        )
        args.extend([cursor_created_at, cursor_id])
    rows = await conn.fetch(
        f"SELECT {EgressCall.returning()} FROM egress_calls "
        f"WHERE {where} ORDER BY created_at DESC, call_id DESC "
        f"LIMIT ${len(args) + 1}",
        *args, limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [view(EgressCall.from_row(row)) for row in rows]
    next_cursor = (
        pagination.encode(rows[-1]["created_at"], rows[-1]["call_id"])
        if has_more and rows else None
    )
    return items, next_cursor
