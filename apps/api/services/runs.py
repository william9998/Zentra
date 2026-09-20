"""ZEN-000 fixture validation and result persistence; never imports graph code."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import BUILTIN_AGENT_ID, BUILTIN_AGENT_SEMVER, ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.schemas import CreateRunRequest, SandboxResult
from apps.api.services.events import lock_run, require_active


BUILTIN_MANIFEST = {
    "agent_version": BUILTIN_AGENT_SEMVER,
    "builtin": True,
    "entrypoints": [{
        "name": "default",
        "input_schema": {
            "type": "object", "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
        "output_schema": {
            "type": "object", "properties": {"echo": {"type": "string"}},
            "required": ["echo"],
        },
    }],
}


def validate_fixture(agent_id: str, body: CreateRunRequest) -> None:
    if agent_id != BUILTIN_AGENT_ID:
        raise not_found("agent")
    if body.entrypoint != "default":
        raise ZentraError(ErrorCode.VALIDATION_ERROR, "unknown entrypoint", 422,
                          details={"field": "entrypoint"})
    if body.version is not None and body.version != BUILTIN_AGENT_SEMVER:
        raise ZentraError(ErrorCode.AGENT_NOT_PUBLISHED, "agent version is not published", 409)
    if not isinstance(body.input.get("question"), str):
        raise ZentraError(ErrorCode.VALIDATION_ERROR, "question must be a string", 422,
                          details={"field": "input.question"})


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
