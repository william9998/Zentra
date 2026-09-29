"""Agent registry, immutable version metadata, and schema generation."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy import and_, delete, desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import AgentSourceType, AgentVersionStatus, ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.ids import uuid7
from apps.api.models import Agent, AgentContainer, AgentVersion, Run
from apps.api.schemas import (
    AgentDetail,
    AgentEndpoints,
    AgentSummary,
    AgentVersionResponse,
    CreateAgentRequest,
    CreateAgentVersionRequest,
    ImageArtifact,
    UpdateAgentRequest,
    parse_image_ref,
)


def _cursor_encode(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        timestamp, row_id = json.loads(raw)
        return datetime.fromisoformat(timestamp), uuid.UUID(row_id)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR, "cursor is invalid", 422,
            details={"field": "cursor"},
        ) from exc


def _semver_key(value: str) -> tuple[int, int, int, int, str]:
    core_and_prerelease = value.split("+", 1)[0]
    core, separator, prerelease = core_and_prerelease.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    return major, minor, patch, int(not separator), prerelease


async def load_agent(session: AsyncSession, agent_id: str, *, include_deleted: bool = False) -> Agent:
    try:
        parsed = uuid.UUID(agent_id)
    except ValueError:
        raise not_found("agent") from None
    statement = select(Agent).where(Agent.id == parsed)
    if not include_deleted:
        statement = statement.where(Agent.deleted_at.is_(None))
    agent = (await session.execute(statement)).scalar_one_or_none()
    if agent is None:
        raise not_found("agent")
    return agent


async def latest_published(session: AsyncSession, agent_id: uuid.UUID) -> AgentVersion | None:
    rows = (await session.execute(select(AgentVersion).where(
        AgentVersion.agent_id == agent_id,
        AgentVersion.status == AgentVersionStatus.PUBLISHED,
    ))).scalars().all()
    return max(rows, key=lambda row: _semver_key(row.semver), default=None)


async def create_agent(session: AsyncSession, body: CreateAgentRequest) -> Agent:
    row = Agent(id=uuid7(), name=body.name, description=body.description)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR, "agent name already exists", 422,
            details={"field": "name"},
        ) from exc
    await session.refresh(row)
    return row


async def update_agent(session: AsyncSession, agent: Agent, body: UpdateAgentRequest) -> Agent:
    for field in body.model_fields_set:
        setattr(agent, field, getattr(body, field))
    agent.updated_at = datetime.now(UTC)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR, "agent name already exists", 422,
            details={"field": "name"},
        ) from exc
    await session.refresh(agent)
    return agent


async def delete_agent(session: AsyncSession, agent: Agent) -> dict:
    """**硬刪** Agent 與它的一切，並回傳還需要在平台外清理的東西。

    ## 為什麼是硬刪（ADR-0013 改的）

    原本是軟刪，為了留稽核軌跡。但實際使用下來，被刪掉的 Agent 留下的是
    **佔著記憶體的容器與佔著磁碟的 image**，而使用者以為自己已經刪掉了它。
    「看不到但還在吃資源」比「查不到歷史」糟。要保留軌跡請在刪除前先匯出。

    ## 刪除順序不能反

    `runs.agent_id` 與 `agent_containers.agent_version_id` 的外鍵**沒有 CASCADE**
    （那是刻意的：正在跑的東西本來就該擋住刪版本）。所以要由下往上刪，
    否則第一個 DELETE 就會被外鍵擋掉，而錯誤訊息只會說「違反約束」。

    回傳的 dict 給 worker 用：容器要停、image 要刪，那兩件事都碰不到 DB，
    而且**必須在 DB 刪掉之前先收集**——刪完就沒有人記得該清哪些東西了。
    """
    payload = {
        "agent_id": str(agent.id),
        "container_ids": [
            str(row) for row in (await session.execute(
                select(AgentContainer.id).where(AgentContainer.agent_id == agent.id)
            )).scalars()
        ],
        "image_refs": [
            ref for ref in (await session.execute(
                select(AgentVersion.image_ref).where(AgentVersion.agent_id == agent.id)
            )).scalars() if ref
        ],
    }

    for statement in (
        delete(Run).where(Run.agent_id == agent.id),
        delete(AgentContainer).where(AgentContainer.agent_id == agent.id),
        delete(AgentVersion).where(AgentVersion.agent_id == agent.id),
    ):
        await session.execute(statement)
    await session.delete(agent)          # 憑證與 api key 靠 CASCADE 跟著走
    await session.commit()
    return payload


async def list_agents(
    session: AsyncSession, *, limit: int, cursor: str | None,
) -> tuple[list[tuple[Agent, str | None]], str | None]:
    statement = select(Agent).where(Agent.deleted_at.is_(None))
    if cursor:
        created_at, row_id = _cursor_decode(cursor)
        statement = statement.where(or_(
            Agent.created_at < created_at,
            and_(Agent.created_at == created_at, Agent.id < row_id),
        ))
    rows = (await session.execute(
        statement.order_by(desc(Agent.created_at), desc(Agent.id)).limit(limit + 1)
    )).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    result: list[tuple[Agent, str | None]] = []
    for row in rows:
        latest = await latest_published(session, row.id)
        result.append((row, latest.semver if latest else None))
    next_cursor = _cursor_encode(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return result, next_cursor


def agent_summary(row: Agent, latest: str | None, *, created: bool = False) -> AgentSummary:
    return AgentSummary(
        agent_id=str(row.id), name=row.name, description=row.description,
        latest_published_version=latest,
        created_at=row.created_at if created else None,
        updated_at=None if created else row.updated_at,
    )


def agent_detail(row: Agent, latest: str | None, base_url: str) -> AgentDetail:
    root = base_url.rstrip("/")
    return AgentDetail(
        agent_id=str(row.id), name=row.name, description=row.description,
        latest_published_version=latest, created_at=row.created_at, updated_at=row.updated_at,
        endpoints=AgentEndpoints(
            rest=f"{root}/v1/agents/{row.id}/runs",
            mcp=f"{root}/mcp/agents/{row.id}",
            openapi=f"{root}/v1/agents/{row.id}/openapi.json",
        ),
    )


def version_response(row: AgentVersion, *, detail: bool = False) -> AgentVersionResponse:
    return AgentVersionResponse(
        agent_version_id=str(row.id), semver=row.semver, version_id=row.version_id,
        source_type=AgentSourceType(row.source_type),
        artifact=ImageArtifact(
            image_ref=row.image_ref, digest=row.image_digest,
            platform=row.image_platform, size_bytes=row.image_size_bytes,
            runtime_contract=row.runtime_contract,
        ),
        status=AgentVersionStatus(row.status), error=row.error, created_at=row.created_at,
        manifest=row.manifest if detail else None,
        entrypoints=row.entrypoints if detail else None,
    )


async def create_version(
    session: AsyncSession, agent: Agent, body: CreateAgentVersionRequest,
) -> AgentVersion:
    _, digest = parse_image_ref(body.image_ref)
    row = AgentVersion(
        id=uuid7(), agent_id=agent.id, semver=body.semver,
        source_type=AgentSourceType.OCI_IMAGE, image_ref=body.image_ref,
        image_digest=digest, version_id=digest.removeprefix("sha256:")[:12],
        status=AgentVersionStatus.BUILDING,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ZentraError(
            ErrorCode.VERSION_CONFLICT, "agent version already exists", 409,
            details={"field": "semver"},
        ) from exc
    await session.refresh(row)
    return row


async def load_version_by_semver(
    session: AsyncSession, agent_id: uuid.UUID, semver: str,
) -> AgentVersion:
    row = (await session.execute(select(AgentVersion).where(
        AgentVersion.agent_id == agent_id, AgentVersion.semver == semver,
    ))).scalar_one_or_none()
    if row is None:
        raise not_found("agent version")
    return row


async def list_versions(
    session: AsyncSession, agent_id: uuid.UUID, *, limit: int, cursor: str | None,
) -> tuple[list[AgentVersion], str | None]:
    statement = select(AgentVersion).where(AgentVersion.agent_id == agent_id)
    if cursor:
        created_at, row_id = _cursor_decode(cursor)
        statement = statement.where(or_(
            AgentVersion.created_at < created_at,
            and_(AgentVersion.created_at == created_at, AgentVersion.id < row_id),
        ))
    rows = (await session.execute(statement.order_by(
        desc(AgentVersion.created_at), desc(AgentVersion.id),
    ).limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _cursor_encode(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return rows, next_cursor


async def resolve_published_version(
    session: AsyncSession, agent_id: uuid.UUID, requested: str | None,
) -> AgentVersion:
    if requested is None:
        row = await latest_published(session, agent_id)
    else:
        row = (await session.execute(select(AgentVersion).where(
            AgentVersion.agent_id == agent_id, AgentVersion.semver == requested,
        ))).scalar_one_or_none()
    if row is None or row.status != AgentVersionStatus.PUBLISHED:
        raise ZentraError(
            ErrorCode.AGENT_NOT_PUBLISHED, "agent version is not published", 409
        )
    return row


def entrypoint(version: AgentVersion, name: str) -> dict[str, Any]:
    for item in version.entrypoints or []:
        if item.get("name") == name:
            return item
    raise ZentraError(
        ErrorCode.VALIDATION_ERROR, "unknown entrypoint", 422,
        details={"field": "entrypoint"},
    )


def validate_input(schema: dict[str, Any], value: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except (SchemaError, ValidationError) as exc:
        path = ".".join(str(part) for part in getattr(exc, "absolute_path", []))
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR, "input does not match entrypoint schema", 422,
            details={"field": f"input{'.' + path if path else ''}", "message": exc.message},
        ) from exc


def generate_agent_openapi(agent: Agent, version: AgentVersion, base_url: str) -> dict[str, Any]:
    entrypoints = version.entrypoints or []
    schemas: dict[str, Any] = {}
    alternatives: list[dict[str, Any]] = []
    for item in entrypoints:
        component = f"{item['name']}Input"
        schemas[component] = item["input_schema"]
        alternatives.append({
            "type": "object",
            "required": ["entrypoint", "input"],
            "properties": {
                "entrypoint": {"const": item["name"]},
                "input": {"$ref": f"#/components/schemas/{component}"},
                "version": {"type": "string", "const": version.semver},
            },
            "additionalProperties": False,
        })
    return {
        "openapi": "3.1.0",
        "info": {"title": agent.name, "version": version.semver,
                 "description": agent.description or ""},
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": {f"/v1/agents/{agent.id}/runs": {"post": {
            "operationId": "createAgentRun",
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"oneOf": alternatives},
            }}},
            "responses": {"202": {"description": "Run queued"}},
            "x-zentra-entrypoints": entrypoints,
        }}},
        "components": {"schemas": schemas},
    }


def mcp_tools(version: AgentVersion) -> list[dict[str, Any]]:
    return [{
        "name": item["name"],
        "description": item.get("description") or "",
        "inputSchema": item["input_schema"],
        "outputSchema": item["output_schema"],
    } for item in version.entrypoints or []]
