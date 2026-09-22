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
from apps.api.services import agents



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
    run = await lock_run(session, run_id)
    require_active(run)
    if run.result_received_at is not None:
        if (run.output, run.error, run.usage) != (body.output, body.error, body.usage):
            raise ZentraError(ErrorCode.VALIDATION_ERROR, "result has already been submitted", 422,
                              run_id=str(run_id), details={"field": "output/error/usage"})
    else:
        run.output, run.error, run.usage = body.output, body.error, body.usage
        run.result_received_at = datetime.now(UTC)
    await session.commit()
