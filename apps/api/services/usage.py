"""per-key 的用量彙總與逐筆除錯查詢（ZEN-015 §3.3）。

## 為什麼歸戶靠 join，不在 `llm_calls` 再存一次 `api_key_id`

`llm_calls` 已經有 `run_id`，而 `runs` 已經有 `api_key_id`。再存一份會有**兩份可能
不一致的真相** —— 而且不一致時沒有辦法判斷哪份是對的。多一次 join 換掉那個風險很划算。

## 金額為什麼是字串

`cost_usd` 是 `Numeric(12,6)`。經過 float 會變成 `0.30000000000000004` 這種東西，
而這是帳。契約 §1 明訂十進位字串，這裡照辦。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg

from apps.api.services import pagination


def _money(value: Decimal | None) -> str:
    """十進位字串，不是 float。沒有呼叫時是 `"0"`，與契約 §1 的 usage 一致。"""
    if not value:
        return "0"
    return format(Decimal(value).normalize(), "f")


@dataclass(frozen=True)
class CallFilters:
    """§3.3 列出的可篩欄位。全部是選填，全部可以疊加。"""

    api_key_id: uuid.UUID | None = None
    since: datetime | None = None
    until: datetime | None = None
    model: str | None = None
    credential_source: str | None = None
    status_code: int | None = None
    #: 只看出錯的。除錯時第一個動作就是它，所以給它一個明確的開關，
    #: 而不是要呼叫端自己組 `status_code >= 400 OR error IS NOT NULL`。
    errors_only: bool = False


def _filters_sql(agent_id: uuid.UUID, filters: CallFilters, start: int = 1) -> tuple[str, list[Any]]:
    """動態片段只從程式碼常數挑；值一律走參數。"""
    clauses = [f"r.agent_id = ${start}"]
    args: list[Any] = [agent_id]
    n = start + 1
    if filters.api_key_id is not None:
        clauses.append(f"r.api_key_id = ${n}")
        args.append(filters.api_key_id)
        n += 1
    if filters.since is not None:
        clauses.append(f"c.created_at >= ${n}")
        args.append(filters.since)
        n += 1
    if filters.until is not None:
        clauses.append(f"c.created_at <= ${n}")
        args.append(filters.until)
        n += 1
    if filters.model:
        clauses.append(f"c.model = ${n}")
        args.append(filters.model)
        n += 1
    if filters.credential_source:
        clauses.append(f"c.credential_source = ${n}")
        args.append(filters.credential_source)
        n += 1
    if filters.status_code is not None:
        clauses.append(f"c.status_code = ${n}")
        args.append(filters.status_code)
        n += 1
    if filters.errors_only:
        clauses.append("(c.error IS NOT NULL OR c.status_code >= 400)")
    return " AND ".join(clauses), args


def _row(count: int, input_tokens: int, output_tokens: int, cost: Decimal) -> dict[str, Any]:
    return {
        "llm_calls": count,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cost_usd": _money(cost),
    }


async def aggregate(
    conn: asyncpg.Connection, agent_id: uuid.UUID, filters: CallFilters,
) -> dict[str, Any]:
    """一次回三種切面：總計、依 key、依 model。"""
    where, args = _filters_sql(agent_id, filters)
    totals = await conn.fetchrow(
        "SELECT count(c.id) AS n, coalesce(sum(c.input_tokens), 0) AS tin, "
        "coalesce(sum(c.output_tokens), 0) AS tout, coalesce(sum(c.cost_usd), 0) AS cost "
        f"FROM llm_calls c JOIN runs r ON r.id = c.run_id WHERE {where}",
        *args,
    )
    by_key_rows = await conn.fetch(
        "SELECT r.api_key_id, k.name, k.key_prefix, "
        "count(c.id) AS n, coalesce(sum(c.input_tokens), 0) AS tin, "
        "coalesce(sum(c.output_tokens), 0) AS tout, coalesce(sum(c.cost_usd), 0) AS cost "
        "FROM llm_calls c JOIN runs r ON r.id = c.run_id "
        "LEFT JOIN agent_api_keys k ON k.id = r.api_key_id "
        f"WHERE {where} "
        "GROUP BY r.api_key_id, k.name, k.key_prefix",
        *args,
    )
    by_key = [
        {
            "key_id": str(row["api_key_id"]) if row["api_key_id"] else None,
            "name": row["name"],
            "key_prefix": row["key_prefix"],
            **_row(row["n"], row["tin"], row["tout"], row["cost"]),
        }
        for row in by_key_rows
    ]

    by_model_rows = await conn.fetch(
        "SELECT c.model, count(c.id) AS n, coalesce(sum(c.input_tokens), 0) AS tin, "
        "coalesce(sum(c.output_tokens), 0) AS tout, coalesce(sum(c.cost_usd), 0) AS cost "
        "FROM llm_calls c JOIN runs r ON r.id = c.run_id "
        f"WHERE {where} GROUP BY c.model",
        *args,
    )
    by_model = [
        {"model": row["model"], **_row(row["n"], row["tin"], row["tout"], row["cost"])}
        for row in by_model_rows
    ]

    by_key.sort(key=lambda r: Decimal(r["cost_usd"]), reverse=True)
    by_model.sort(key=lambda r: Decimal(r["cost_usd"]), reverse=True)
    return {
        "totals": _row(totals["n"], totals["tin"], totals["tout"], totals["cost"]),
        "by_key": by_key,
        "by_model": by_model,
    }


async def list_calls(
    conn: asyncpg.Connection,
    agent_id: uuid.UUID,
    filters: CallFilters,
    *,
    limit: int,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """逐筆列出 LLM 呼叫，最新的在前。回 `(items, next_cursor)`。"""
    where, args = _filters_sql(agent_id, filters)
    if cursor:
        cursor_created_at, cursor_id = pagination.decode(cursor)
        n = len(args) + 1
        where += f" AND (c.created_at < ${n} OR (c.created_at = ${n} AND c.id < ${n + 1}))"
        args.extend([cursor_created_at, cursor_id])
    rows = await conn.fetch(
        "SELECT c.id, c.run_id, r.api_key_id, r.status AS run_status, c.model, "
        "c.input_tokens, c.output_tokens, c.cost_usd, c.latency_ms, c.status_code, "
        "c.error, c.credential_source, c.created_at "
        "FROM llm_calls c JOIN runs r ON r.id = c.run_id "
        f"WHERE {where} ORDER BY c.created_at DESC, c.id DESC LIMIT ${len(args) + 1}",
        *args, limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "call_id": str(row["id"]),
            "run_id": str(row["run_id"]),
            "run_status": row["run_status"],
            "api_key_id": str(row["api_key_id"]) if row["api_key_id"] else None,
            "model": row["model"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cost_usd": _money(row["cost_usd"]),
            "latency_ms": row["latency_ms"],
            "status_code": row["status_code"],
            "error": row["error"],
            "credential_source": row["credential_source"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    next_cursor = (
        pagination.encode(rows[-1]["created_at"], rows[-1]["id"]) if has_more and rows else None
    )
    return items, next_cursor
