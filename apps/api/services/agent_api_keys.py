"""下游應用呼叫 Agent 用的 API key（ZEN-015）。"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg

from apps.api.errors import not_found
from apps.api.ids import uuid7
from apps.api.rows import AgentApiKey

#: key 的前綴。看得出這是什麼東西，被貼到 issue 或 log 裡時認得出來要撤銷。
KEY_PREFIX = "zk_live_"
_SECRET_BYTES = 32


@dataclass(frozen=True)
class IssuedKey:
    """建立時回傳一次。**明文只在這裡出現過一次**，之後沒有任何端點讀得到。"""

    row: AgentApiKey
    plaintext: str


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _display_prefix(plaintext: str) -> str:
    """列表裡讓人認得出是哪一把。只露前綴加四碼 —— 足夠辨識，不足以使用。"""
    return plaintext[:len(KEY_PREFIX) + 4]


async def issue(conn: asyncpg.Connection, agent_id: uuid.UUID, name: str) -> IssuedKey:
    plaintext = KEY_PREFIX + secrets.token_urlsafe(_SECRET_BYTES)
    now = datetime.now(UTC)
    rec = await conn.fetchrow(
        f"""
        INSERT INTO agent_api_keys
            (id, agent_id, name, key_hash, key_prefix, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING {AgentApiKey.returning()}
        """,
        uuid7(), agent_id, name, hash_key(plaintext), _display_prefix(plaintext), now,
    )
    return IssuedKey(row=AgentApiKey.from_row(rec), plaintext=plaintext)


async def list_keys(conn: asyncpg.Connection, agent_id: uuid.UUID) -> list[AgentApiKey]:
    """列出所有 key，**含已撤銷的** —— 歷史用量還掛在它們身上，藏起來會讓帳無主。"""
    rows = await conn.fetch(
        f"SELECT {AgentApiKey.returning()} FROM agent_api_keys "
        "WHERE agent_id = $1 ORDER BY created_at DESC",
        agent_id,
    )
    return [AgentApiKey.from_row(row) for row in rows]


async def load_key(conn: asyncpg.Connection, agent_id: uuid.UUID, key_id: str) -> AgentApiKey:
    """取一把屬於這個 Agent 的 key。不存在或屬於別的 Agent 一律 404。"""
    try:
        kid = uuid.UUID(key_id)
    except ValueError:
        raise not_found("agent api key") from None
    rec = await conn.fetchrow(
        f"SELECT {AgentApiKey.returning()} FROM agent_api_keys "
        "WHERE id = $1 AND agent_id = $2",
        kid, agent_id,
    )
    if rec is None:
        raise not_found("agent api key")
    return AgentApiKey.from_row(rec)


async def revoke(conn: asyncpg.Connection, row: AgentApiKey) -> AgentApiKey:
    """軟撤銷。**不硬刪** —— 刪掉會讓這把 key 過去的用量變成無主的帳。"""
    if row.revoked_at is not None:
        return row
    rec = await conn.fetchrow(
        f"UPDATE agent_api_keys SET revoked_at = $1 WHERE id = $2 "
        f"RETURNING {AgentApiKey.returning()}",
        datetime.now(UTC), row.id,
    )
    return AgentApiKey.from_row(rec)


async def resolve(conn: asyncpg.Connection, plaintext: str) -> AgentApiKey | None:
    """用明文找出對應的 key。已撤銷的回 None。"""
    rec = await conn.fetchrow(
        f"SELECT {AgentApiKey.returning()} FROM agent_api_keys WHERE key_hash = $1",
        hash_key(plaintext),
    )
    if rec is None:
        return None
    row = AgentApiKey.from_row(rec)
    if row.revoked_at is not None:
        return None
    return row


async def touch(conn: asyncpg.Connection, row: AgentApiKey) -> None:
    """記下最後使用時間。用來回答「這把還有人在用嗎，可以撤掉嗎」。"""
    await conn.execute(
        "UPDATE agent_api_keys SET last_used_at = $1 WHERE id = $2",
        datetime.now(UTC), row.id,
    )
