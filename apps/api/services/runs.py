"""Run creation and result persistence; never imports image code."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import ErrorCode, RunStatus, RunTrigger
from apps.api.errors import ZentraError
from apps.api.ids import uuid7
from apps.api.models import AgentVersion, Run
from apps.api.schemas import CreateRunRequest, SandboxResult
from apps.api.services.events import lock_run, require_active
from apps.api.services import agents, llm



async def create_run(
    session: AsyncSession,
    agent_id: str,
    body: CreateRunRequest,
    *,
    trigger: RunTrigger,
) -> tuple[Run, AgentVersion]:
    agent = await agents.load_agent(session, agent_id)
    version = await agents.resolve_published_version(session, agent.id, body.version)
    selected = agents.entrypoint(version, body.entrypoint)
    agents.validate_input(selected["input_schema"], body.input)

    run_id = uuid7()
    run = Run(
        id=run_id, agent_id=agent.id, agent_version_id=version.id,
        status=RunStatus.QUEUED, trigger=trigger,
        entrypoint=body.entrypoint, input=body.input,
        timeout_seconds=body.config.timeout_seconds, celery_task_id=str(run_id),
    )
    session.add(run)
    await session.flush()
    from apps.api.services import events
    events.status_event(session, run)
    await session.commit()
    return run, version


async def save_result(session: AsyncSession, run_id: UUID, body: SandboxResult) -> None:
    """保存沙盒回報的 output / error，**但 usage 由平台自己算**。

    沙盒也會在 body 裡送 usage，那是使用者程式碼算出來的數字，沒有理由被當成帳。
    真正的用量在 `llm_calls`——每一次經 Internal Gateway 的呼叫都留了一列，
    而沙盒沒有其他對外出口，所以那就是全部（SPEC §10 稽核要求）。

    冪等比對因此只看 `output` / `error`：usage 是平台算的，重送同一份結果時
    它可能已經因為期間又有一次 LLM 呼叫而變大，那不是衝突。
    """
    run = await lock_run(session, run_id)
    require_active(run)
    usage = await llm.aggregate_usage(session, run_id)
    if run.result_received_at is not None:
        if (run.output, run.error) != (body.output, body.error):
            raise ZentraError(ErrorCode.VALIDATION_ERROR, "result has already been submitted", 422,
                              run_id=str(run_id), details={"field": "output/error"})
        run.usage = usage
    else:
        run.output, run.error = body.output, body.error
        run.usage = usage
        run.result_received_at = datetime.now(UTC)
    await session.commit()
