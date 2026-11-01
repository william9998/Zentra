"""平台 MCP 接口 `/mcp` 用的 key，以及每把 key 勾選的 Agent。

和 `agent_api_keys` 是兩套東西：那個綁單一 Agent 的 REST，這個綁一組 Agent 的 MCP。
保存方式相同——只存 SHA-256 雜湊，明文只在發放當下出現一次。
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg

from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.ids import uuid7
from apps.api.rows import McpKey

#: 和 Agent 的 `zk_live_` 不同前綴，貼到 log 裡一眼分得出是哪一種。
KEY_PREFIX = "zmcp_live_"
_SECRET_BYTES = 32


@dataclass(frozen=True)
class IssuedKey:
    """建立時回傳一次。**明文只在這裡出現過一次**。"""

    row: McpKey
    plaintext: str


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _display_prefix(plaintext: str) -> str:
    return plaintext[:len(KEY_PREFIX) + 4]


def _parse_agent_ids(agent_ids: list[str]) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    for raw in agent_ids:
        try:
            value = uuid.UUID(raw)
        except ValueError:
            raise not_found("agent") from None
        if value not in parsed:
            parsed.append(value)
    return parsed


async def _require_agents(conn: asyncpg.Connection, agent_ids: list[uuid.UUID]) -> None:
    """勾選的 Agent 必須都存在（且未刪除），否則 404，什麼都不寫。"""
    if not agent_ids:
        return
    found = await conn.fetchval(
        "SELECT count(*) FROM agents WHERE id = ANY($1::uuid[]) AND deleted_at IS NULL",
        agent_ids,
    )
    if found != len(agent_ids):
        raise not_found("agent")


async def _replace_agents(
    conn: asyncpg.Connection, key_id: uuid.UUID, agent_ids: list[uuid.UUID],
) -> None:
    await conn.execute("DELETE FROM mcp_key_agents WHERE mcp_key_id = $1", key_id)
    if agent_ids:
        await conn.execute(
            "INSERT INTO mcp_key_agents (mcp_key_id, agent_id) "
            "SELECT $1, unnest($2::uuid[])",
            key_id, agent_ids,
        )


async def issue(conn: asyncpg.Connection, name: str, agent_ids: list[str]) -> IssuedKey:
    parsed = _parse_agent_ids(agent_ids)
    await _require_agents(conn, parsed)
    plaintext = KEY_PREFIX + secrets.token_urlsafe(_SECRET_BYTES)
    rec = await conn.fetchrow(
        f"""
        INSERT INTO mcp_keys (id, name, key_hash, key_prefix, created_at)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING {McpKey.returning()}
        """,
        uuid7(), name, hash_key(plaintext), _display_prefix(plaintext), datetime.now(UTC),
    )
    row = McpKey.from_row(rec)
    await _replace_agents(conn, row.id, parsed)
    return IssuedKey(row=row, plaintext=plaintext)


async def list_keys(conn: asyncpg.Connection) -> list[McpKey]:
    """列出所有 key，**含已撤銷的** —— 歷史 Run 還掛在它們身上。"""
    rows = await conn.fetch(
        f"SELECT {McpKey.returning()} FROM mcp_keys ORDER BY created_at DESC")
    return [McpKey.from_row(row) for row in rows]


async def load_key(conn: asyncpg.Connection, key_id: str) -> McpKey:
    try:
        kid = uuid.UUID(key_id)
    except ValueError:
        raise not_found("mcp key") from None
    rec = await conn.fetchrow(
        f"SELECT {McpKey.returning()} FROM mcp_keys WHERE id = $1", kid)
    if rec is None:
        raise not_found("mcp key")
    return McpKey.from_row(rec)


async def agent_ids_by_key(
    conn: asyncpg.Connection, key_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[str]]:
    """每把 key 勾選的 Agent id。只含仍存在的 Agent。"""
    if not key_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT ka.mcp_key_id, ka.agent_id
        FROM mcp_key_agents ka
        JOIN agents a ON a.id = ka.agent_id AND a.deleted_at IS NULL
        WHERE ka.mcp_key_id = ANY($1::uuid[])
        ORDER BY a.name
        """,
        key_ids,
    )
    result: dict[uuid.UUID, list[str]] = {key_id: [] for key_id in key_ids}
    for row in rows:
        result[row["mcp_key_id"]].append(str(row["agent_id"]))
    return result


async def set_agents(conn: asyncpg.Connection, row: McpKey, agent_ids: list[str]) -> None:
    if row.revoked_at is not None:
        raise ZentraError(
            ErrorCode.VERSION_CONFLICT, "this MCP key is revoked; its agents can no longer change",
            409,
        )
    parsed = _parse_agent_ids(agent_ids)
    await _require_agents(conn, parsed)
    await _replace_agents(conn, row.id, parsed)


async def revoke(conn: asyncpg.Connection, row: McpKey) -> McpKey:
    """軟撤銷，立即失效；列保留。"""
    if row.revoked_at is not None:
        return row
    rec = await conn.fetchrow(
        f"UPDATE mcp_keys SET revoked_at = $1 WHERE id = $2 RETURNING {McpKey.returning()}",
        datetime.now(UTC), row.id,
    )
    return McpKey.from_row(rec)


async def resolve(conn: asyncpg.Connection, plaintext: str) -> McpKey | None:
    """用明文找出對應的 key。已撤銷的回 None。"""
    rec = await conn.fetchrow(
        f"SELECT {McpKey.returning()} FROM mcp_keys WHERE key_hash = $1",
        hash_key(plaintext),
    )
    if rec is None:
        return None
    row = McpKey.from_row(rec)
    return None if row.revoked_at is not None else row


async def touch(conn: asyncpg.Connection, row: McpKey) -> None:
    await conn.execute(
        "UPDATE mcp_keys SET last_used_at = $1 WHERE id = $2", datetime.now(UTC), row.id)
