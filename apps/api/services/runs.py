"""Run creation and result persistence; never imports image code."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from apps.api.enums import ErrorCode, RunStatus, RunTrigger
from apps.api.errors import ZentraError
from apps.api.ids import uuid7
from apps.api.rows import AgentVersion, Run
from apps.api.schemas import CreateRunRequest, RunResponse, SandboxResult
from apps.api.services import agents, containers, llm, pagination
from apps.api.services.events import lock_run, require_active, status_event


async def create_run(
    conn: asyncpg.Connection,
    agent_id: str,
    body: CreateRunRequest,
    *,
    trigger: RunTrigger,
    api_key_id: UUID | None = None,
    mcp_key_id: UUID | None = None,
) -> tuple[Run, AgentVersion]:
    """建立一筆 Run。

    `api_key_id` 是哪一把下游 key 觸發的（ZEN-015）。**Console 發起時是 None**，
    那不是錯誤：MVP 的 Console 沒有 key，契約 §0 也還沒要求 `/v1` 認證。
    這個欄位買到的是「用量歸戶」，不是「擋住未授權的人」。
    `mcp_key_id` 是哪一把 MCP key 觸發的，只有 `/mcp` 會帶。
    """
    agent = await agents.load_agent(conn, agent_id)
    version = await agents.resolve_published_version(conn, agent.id, body.version)
    selected = agents.entrypoint(version, body.entrypoint)
    agents.validate_input(selected["input_schema"], body.input)

    run_id = uuid7()
    # 版本被存活檢查放棄了：**仍然建一筆**，直接 FAILED、不派工。
    # 不建的話故障期間被拒絕的請求在 DB 裡完全沒有痕跡，事後查不到影響了誰。
    # 呼叫端看 `creation_rejection` 決定回 409。
    status, error, finished_at = RunStatus.QUEUED, None, None
    gave_up = await containers.given_up_container(conn, version.id)
    if gave_up is not None:
        status, finished_at = RunStatus.FAILED, datetime.now(UTC)
        error = {
            "code": str(ErrorCode.AGENT_NOT_RUNNING),
            "message": containers.given_up_message(gave_up),
            "run_id": str(run_id), "retryable": False,
        }
    rec = await conn.fetchrow(
        f"""
        INSERT INTO runs
            (id, agent_id, agent_version_id, status, trigger, entrypoint,
             input, timeout_seconds, celery_task_id, api_key_id, mcp_key_id,
             error, finished_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        RETURNING {Run.returning()}
        """,
        run_id, agent.id, version.id, str(status), str(trigger),
        body.entrypoint, body.input, body.config.timeout_seconds,
        str(run_id), api_key_id, mcp_key_id, error, finished_at,
    )
    run = Run.from_row(rec)
    await status_event(conn, run, terminal=status.is_terminal)
    return run, version


def creation_rejection(run: Run) -> ZentraError | None:
    """`create_run` 建出來就是終態（版本被放棄）→ 給呼叫端的 409；正常的 Run 回 `None`。

    要在**交易結束之後**才 raise —— 在交易裡 raise 會連那筆紀錄一起回滾。
    """
    if not RunStatus(run.status).is_terminal:
        return None
    error = run.error or {}
    return ZentraError(
        ErrorCode(error.get("code") or ErrorCode.AGENT_NOT_RUNNING),
        error.get("message") or "the run was rejected at creation",
        409, run_id=str(run.id),
    )


async def save_result(conn: asyncpg.Connection, run_id: UUID, body: SandboxResult) -> bool:
    """保存沙盒回報的 output / error，**但 usage 由平台自己算**。

    沙盒也會在 body 裡送 usage，那是使用者程式碼算出來的數字，沒有理由被當成帳。
    真正的用量在 `llm_calls`——每一次經 Internal Gateway 的呼叫都留了一列，
    而沙盒沒有其他對外出口，所以那就是全部（SPEC §10 稽核要求）。

    冪等比對因此只看 `output` / `error`：usage 是平台算的，重送同一份結果時
    它可能已經因為期間又有一次 LLM 呼叫而變大，那不是衝突。

    回傳是否該通知 worker 做常駐容器終態裁決（有 `container_id`）。
    """
    run = await lock_run(conn, run_id)
    require_active(run)
    usage = await llm.aggregate_usage(conn, run_id)
    if run.result_received_at is not None:
        if (run.output, run.error) != (body.output, body.error):
            raise ZentraError(ErrorCode.VALIDATION_ERROR, "result has already been submitted", 422,
                              run_id=str(run_id), details={"field": "output/error"})
        await conn.execute(
            "UPDATE runs SET usage = $2 WHERE id = $1", run_id, usage)
        return False
    await conn.execute(
        """
        UPDATE runs SET output = $2, error = $3, usage = $4, result_received_at = $5
        WHERE id = $1
        """,
        run_id, body.output, body.error, usage, datetime.now(UTC),
    )
    return run.container_id is not None


async def load_run(conn: asyncpg.Connection, run_id: UUID) -> Run | None:
    rec = await conn.fetchrow(
        f"SELECT {Run.returning()} FROM runs WHERE id = $1", run_id)
    return Run.from_row(rec) if rec is not None else None


async def version_semver(conn: asyncpg.Connection, version_id: UUID | None) -> str | None:
    if version_id is None:
        return None
    return await conn.fetchval(
        "SELECT semver FROM agent_versions WHERE id = $1", version_id)


def to_response(run: Run, semver: str) -> RunResponse:
    return RunResponse(
        run_id=str(run.id), agent_id=str(run.agent_id), agent_version=semver,
        status=run.status, trigger=run.trigger, input=run.input,
        output=run.output, error=run.error, usage=run.usage,
        created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
    )


async def list_runs(
    conn: asyncpg.Connection,
    *,
    agent_id: UUID | None,
    api_key_id: UUID | None,
    status: RunStatus | None,
    trigger: RunTrigger | None,
    created_after: datetime | None,
    created_before: datetime | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    where = ["TRUE"]
    args: list[Any] = []
    n = 1
    if agent_id is not None:
        where.append(f"r.agent_id = ${n}")
        args.append(agent_id)
        n += 1
    if api_key_id is not None:
        where.append(f"r.api_key_id = ${n}")
        args.append(api_key_id)
        n += 1
    if status is not None:
        where.append(f"r.status = ${n}")
        args.append(str(status))
        n += 1
    if trigger is not None:
        where.append(f"r.trigger = ${n}")
        args.append(str(trigger))
        n += 1
    if created_after is not None:
        where.append(f"r.created_at >= ${n}")
        args.append(created_after)
        n += 1
    if created_before is not None:
        where.append(f"r.created_at <= ${n}")
        args.append(created_before)
        n += 1
    if cursor:
        cursor_created_at, cursor_id = pagination.decode(cursor)
        where.append(
            f"(r.created_at < ${n} OR (r.created_at = ${n} AND r.id < ${n + 1}))")
        args.extend([cursor_created_at, cursor_id])
        n += 2
    rows = await conn.fetch(
        "SELECT r.id, r.agent_id, a.name AS agent_name, v.semver, r.status, r.trigger, "
        "r.created_at, r.started_at, r.finished_at "
        "FROM runs r "
        "JOIN agent_versions v ON v.id = r.agent_version_id "
        "JOIN agents a ON a.id = r.agent_id "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY r.created_at DESC, r.id DESC LIMIT ${n}",
        *args, limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "run_id": str(row["id"]),
            "agent_id": str(row["agent_id"]),
            "agent_name": row["agent_name"],
            "agent_version": row["semver"],
            "status": row["status"],
            "trigger": row["trigger"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
        for row in rows
    ]
    next_cursor = (
        pagination.encode(rows[-1]["created_at"], rows[-1]["id"]) if has_more and rows else None
    )
    return items, next_cursor
