"""Agent registry, immutable version metadata, and schema generation."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from asyncpg.exceptions import UniqueViolationError
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from apps.api.enums import AgentSourceType, AgentVersionStatus, ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.ids import uuid7
from apps.api.rows import Agent, AgentVersion
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


async def load_agent(
    conn: asyncpg.Connection, agent_id: str, *, include_deleted: bool = False,
) -> Agent:
    try:
        parsed = uuid.UUID(agent_id)
    except ValueError:
        raise not_found("agent") from None
    if include_deleted:
        rec = await conn.fetchrow(
            f"SELECT {Agent.returning()} FROM agents WHERE id = $1", parsed)
    else:
        rec = await conn.fetchrow(
            f"SELECT {Agent.returning()} FROM agents WHERE id = $1 AND deleted_at IS NULL",
            parsed)
    if rec is None:
        raise not_found("agent")
    return Agent.from_row(rec)


async def latest_published(conn: asyncpg.Connection, agent_id: uuid.UUID) -> AgentVersion | None:
    rows = await conn.fetch(
        f"SELECT {AgentVersion.returning()} FROM agent_versions "
        "WHERE agent_id = $1 AND status = $2",
        agent_id, str(AgentVersionStatus.PUBLISHED),
    )
    if not rows:
        return None
    return max((AgentVersion.from_row(row) for row in rows), key=lambda row: _semver_key(row.semver))


async def create_agent(conn: asyncpg.Connection, body: CreateAgentRequest) -> Agent:
    now = datetime.now(UTC)
    try:
        rec = await conn.fetchrow(
            f"""
            INSERT INTO agents (id, name, description, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $4)
            RETURNING {Agent.returning()}
            """,
            uuid7(), body.name, body.description, now,
        )
    except UniqueViolationError as exc:
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR, "agent name already exists", 422,
            details={"field": "name"},
        ) from exc
    return Agent.from_row(rec)


async def update_agent(
    conn: asyncpg.Connection, agent: Agent, body: UpdateAgentRequest,
) -> Agent:
    name = body.name if "name" in body.model_fields_set else agent.name
    description = body.description if "description" in body.model_fields_set else agent.description
    now = datetime.now(UTC)
    try:
        rec = await conn.fetchrow(
            f"""
            UPDATE agents SET name = $2, description = $3, updated_at = $4
            WHERE id = $1
            RETURNING {Agent.returning()}
            """,
            agent.id, name, description, now,
        )
    except UniqueViolationError as exc:
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR, "agent name already exists", 422,
            details={"field": "name"},
        ) from exc
    return Agent.from_row(rec)


async def delete_agent(conn: asyncpg.Connection, agent: Agent) -> dict:
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
    container_ids = [
        str(row["id"]) for row in await conn.fetch(
            "SELECT id FROM agent_containers WHERE agent_id = $1", agent.id)
    ]
    image_refs = [
        row["image_ref"] for row in await conn.fetch(
            "SELECT image_ref FROM agent_versions WHERE agent_id = $1", agent.id)
        if row["image_ref"]
    ]
    payload = {
        "agent_id": str(agent.id),
        "container_ids": container_ids,
        "image_refs": image_refs,
    }

    # ZEN-019 的五張表。**明著刪，不只靠 CASCADE**：這串就是 §2.5 那份
    # 「刪 Agent 會一起消失什麼」的清單，寫在這裡才不會有人加了新表卻
    # 忘了它，而漏掉的那張表裡放的可能是機密。
    # egress_calls 要排在 runs 與 egress_rules 之前：它同時指向兩者。
    for statement in (
        "DELETE FROM egress_calls WHERE agent_id = $1",
        "DELETE FROM agent_egress_rules WHERE agent_id = $1",
        "DELETE FROM agent_email_sender WHERE agent_id = $1",
        "DELETE FROM agent_secrets WHERE agent_id = $1",
        "DELETE FROM agent_settings WHERE agent_id = $1",
        "DELETE FROM runs WHERE agent_id = $1",
        "DELETE FROM agent_containers WHERE agent_id = $1",
        "DELETE FROM agent_versions WHERE agent_id = $1",
        "DELETE FROM agents WHERE id = $1",
    ):
        await conn.execute(statement, agent.id)
    return payload


async def list_agents(
    conn: asyncpg.Connection, *, limit: int, cursor: str | None,
) -> tuple[list[tuple[Agent, str | None]], str | None]:
    where = "deleted_at IS NULL"
    args: list[Any] = []
    if cursor:
        created_at, row_id = _cursor_decode(cursor)
        where += " AND (created_at < $1 OR (created_at = $1 AND id < $2))"
        args.extend([created_at, row_id])
    rows = await conn.fetch(
        f"SELECT {Agent.returning()} FROM agents WHERE {where} "
        f"ORDER BY created_at DESC, id DESC LIMIT ${len(args) + 1}",
        *args, limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    result: list[tuple[Agent, str | None]] = []
    for rec in rows:
        row = Agent.from_row(rec)
        latest = await latest_published(conn, row.id)
        result.append((row, latest.semver if latest else None))
    next_cursor = (
        _cursor_encode(rows[-1]["created_at"], rows[-1]["id"]) if has_more and rows else None
    )
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
    conn: asyncpg.Connection, agent: Agent, body: CreateAgentVersionRequest,
) -> AgentVersion:
    _, digest = parse_image_ref(body.image_ref)
    try:
        rec = await conn.fetchrow(
            f"""
            INSERT INTO agent_versions
                (id, agent_id, semver, source_type, image_ref, image_digest,
                 version_id, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING {AgentVersion.returning()}
            """,
            uuid7(), agent.id, body.semver, str(AgentSourceType.OCI_IMAGE),
            body.image_ref, digest, digest.removeprefix("sha256:")[:12],
            str(AgentVersionStatus.BUILDING),
        )
    except UniqueViolationError as exc:
        raise ZentraError(
            ErrorCode.VERSION_CONFLICT, "agent version already exists", 409,
            details={"field": "semver"},
        ) from exc
    return AgentVersion.from_row(rec)


async def load_version_by_semver(
    conn: asyncpg.Connection, agent_id: uuid.UUID, semver: str,
) -> AgentVersion:
    rec = await conn.fetchrow(
        f"SELECT {AgentVersion.returning()} FROM agent_versions "
        "WHERE agent_id = $1 AND semver = $2",
        agent_id, semver,
    )
    if rec is None:
        raise not_found("agent version")
    return AgentVersion.from_row(rec)


async def list_versions(
    conn: asyncpg.Connection, agent_id: uuid.UUID, *, limit: int, cursor: str | None,
) -> tuple[list[AgentVersion], str | None]:
    where = "agent_id = $1"
    args: list[Any] = [agent_id]
    if cursor:
        created_at, row_id = _cursor_decode(cursor)
        where += " AND (created_at < $2 OR (created_at = $2 AND id < $3))"
        args.extend([created_at, row_id])
    rows = await conn.fetch(
        f"SELECT {AgentVersion.returning()} FROM agent_versions WHERE {where} "
        f"ORDER BY created_at DESC, id DESC LIMIT ${len(args) + 1}",
        *args, limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        _cursor_encode(rows[-1]["created_at"], rows[-1]["id"]) if has_more and rows else None
    )
    return [AgentVersion.from_row(row) for row in rows], next_cursor


async def resolve_published_version(
    conn: asyncpg.Connection, agent_id: uuid.UUID, requested: str | None,
) -> AgentVersion:
    if requested is None:
        row = await latest_published(conn, agent_id)
    else:
        rec = await conn.fetchrow(
            f"SELECT {AgentVersion.returning()} FROM agent_versions "
            "WHERE agent_id = $1 AND semver = $2",
            agent_id, requested,
        )
        row = AgentVersion.from_row(rec) if rec is not None else None
    if row is None or row.status != str(AgentVersionStatus.PUBLISHED):
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
