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

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models import AgentApiKey, LlmCall, Run
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


def _apply(statement: Select, agent_id: uuid.UUID, filters: CallFilters) -> Select:
    statement = statement.where(Run.agent_id == agent_id)
    if filters.api_key_id is not None:
        statement = statement.where(Run.api_key_id == filters.api_key_id)
    if filters.since is not None:
        statement = statement.where(LlmCall.created_at >= filters.since)
    if filters.until is not None:
        statement = statement.where(LlmCall.created_at <= filters.until)
    if filters.model:
        statement = statement.where(LlmCall.model == filters.model)
    if filters.credential_source:
        statement = statement.where(LlmCall.credential_source == filters.credential_source)
    if filters.status_code is not None:
        statement = statement.where(LlmCall.status_code == filters.status_code)
    if filters.errors_only:
        statement = statement.where(
            (LlmCall.error.is_not(None)) | (LlmCall.status_code >= 400))
    return statement


_TOTALS = (
    func.count(LlmCall.id),
    func.coalesce(func.sum(LlmCall.input_tokens), 0),
    func.coalesce(func.sum(LlmCall.output_tokens), 0),
    func.coalesce(func.sum(LlmCall.cost_usd), 0),
)


def _row(count: int, input_tokens: int, output_tokens: int, cost: Decimal) -> dict[str, Any]:
    return {
        "llm_calls": count,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cost_usd": _money(cost),
    }


async def aggregate(
    session: AsyncSession, agent_id: uuid.UUID, filters: CallFilters,
) -> dict[str, Any]:
    """一次回三種切面：總計、依 key、依 model。

    分三支查詢而不是讓呼叫端傳 `group_by`：除錯時這三個是一起看的
    （「這把 key 花了多少」「花在哪個模型上」），拆成三次往返只是把拼裝工作
    推給前端，而且中間資料還會變動。
    """
    base = select(*_TOTALS).select_from(LlmCall).join(Run, Run.id == LlmCall.run_id)
    totals = (await session.execute(_apply(base, agent_id, filters))).one()

    by_key_stmt = _apply(
        select(Run.api_key_id, AgentApiKey.name, AgentApiKey.key_prefix, *_TOTALS)
        .select_from(LlmCall)
        .join(Run, Run.id == LlmCall.run_id)
        # outerjoin：Console 發起的 Run 是 NULL，那一列必須留著，
        # 不然「沒歸戶的用量」會從報表裡消失，而那通常正是要找的東西。
        .outerjoin(AgentApiKey, AgentApiKey.id == Run.api_key_id),
        agent_id, filters,
    ).group_by(Run.api_key_id, AgentApiKey.name, AgentApiKey.key_prefix)
    by_key = [
        {
            "key_id": str(key_id) if key_id else None,
            "name": name,
            "key_prefix": prefix,
            **_row(count, tin, tout, cost),
        }
        for key_id, name, prefix, count, tin, tout, cost
        in (await session.execute(by_key_stmt)).all()
    ]

    by_model_stmt = _apply(
        select(LlmCall.model, *_TOTALS).select_from(LlmCall).join(Run, Run.id == LlmCall.run_id),
        agent_id, filters,
    ).group_by(LlmCall.model)
    by_model = [
        {"model": model, **_row(count, tin, tout, cost)}
        for model, count, tin, tout, cost
        in (await session.execute(by_model_stmt)).all()
    ]

    # 花最多的排前面——看報表的人要找的就是那一個
    by_key.sort(key=lambda r: Decimal(r["cost_usd"]), reverse=True)
    by_model.sort(key=lambda r: Decimal(r["cost_usd"]), reverse=True)
    return {"totals": _row(*totals), "by_key": by_key, "by_model": by_model}


async def list_calls(
    session: AsyncSession,
    agent_id: uuid.UUID,
    filters: CallFilters,
    *,
    limit: int,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """逐筆列出 LLM 呼叫，最新的在前。回 `(items, next_cursor)`。

    每一列都帶著 `run_id` 與 `api_key_id`，所以「這把 key 的哪一筆出錯了」
    看完這張表就能直接跳到那個 Run。
    """
    statement = _apply(
        select(LlmCall, Run.api_key_id, Run.status)
        .join(Run, Run.id == LlmCall.run_id),
        agent_id, filters,
    )
    if cursor:
        cursor_created_at, cursor_id = pagination.decode(cursor)
        statement = statement.where(
            (LlmCall.created_at < cursor_created_at)
            | ((LlmCall.created_at == cursor_created_at) & (LlmCall.id < cursor_id))
        )
    rows = (await session.execute(
        statement.order_by(LlmCall.created_at.desc(), LlmCall.id.desc()).limit(limit + 1)
    )).all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "call_id": str(call.id),
            "run_id": str(call.run_id),
            "run_status": run_status,
            "api_key_id": str(api_key_id) if api_key_id else None,
            "model": call.model,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "cost_usd": _money(call.cost_usd),
            "latency_ms": call.latency_ms,
            "status_code": call.status_code,
            "error": call.error,
            "credential_source": call.credential_source,
            "created_at": call.created_at,
        }
        for call, api_key_id, run_status in rows
    ]
    next_cursor = (
        pagination.encode(rows[-1][0].created_at, rows[-1][0].id) if has_more and rows else None
    )
    return items, next_cursor
