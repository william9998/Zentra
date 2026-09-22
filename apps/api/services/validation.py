"""Idempotent persistence for validation-container reports."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import AgentVersionStatus, ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.models import AgentVersion
from apps.api.schemas import ValidationPayload


async def save_validation(
    session: AsyncSession, version_id: uuid.UUID, body: ValidationPayload,
) -> None:
    version = (await session.execute(
        select(AgentVersion).where(AgentVersion.id == version_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if version is None:
        raise not_found("agent version")
    if version.status != AgentVersionStatus.BUILDING:
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR, "agent version is no longer accepting validation", 422,
            details={"field": "status"},
        )
    payload = body.model_dump(mode="json")
    if version.validation_payload is not None and version.validation_payload != payload:
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR, "validation result has already been submitted", 422,
            details={"field": "validation"},
        )
    version.validation_payload = payload
    await session.commit()
