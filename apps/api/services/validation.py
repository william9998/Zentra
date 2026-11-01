"""Idempotent persistence for validation-container reports."""

import uuid

import asyncpg

from apps.api.enums import AgentVersionStatus, ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.rows import AgentVersion
from apps.api.schemas import ValidationPayload


def _require_txn(conn: asyncpg.Connection) -> None:
    if not conn.is_in_transaction():
        raise RuntimeError("save_validation must be called inside db.transaction()")


async def save_validation(
    conn: asyncpg.Connection, version_id: uuid.UUID, body: ValidationPayload,
) -> None:
    """寫入驗證結果。**需在交易內呼叫**（`FOR UPDATE`）。"""
    _require_txn(conn)
    rec = await conn.fetchrow(
        f"SELECT {AgentVersion.returning()} FROM agent_versions "
        "WHERE id = $1 FOR UPDATE",
        version_id,
    )
    if rec is None:
        raise not_found("agent version")
    version = AgentVersion.from_row(rec)
    if version.status != str(AgentVersionStatus.BUILDING):
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
    await conn.execute(
        "UPDATE agent_versions SET validation_payload = $2 WHERE id = $1",
        version_id, payload,
    )
