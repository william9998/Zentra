"""Per-agent LLM 憑證的存取（ZEN-012）。

**明文只在兩個地方存在**：`set()` 收到請求的那一刻，以及 `resolve()` 交給
Internal Gateway 換金鑰的那一刻。中間全程是密文。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg

from apps.api.rows import AgentLlmCredential
from apps.api.services import secrets


@dataclass(frozen=True)
class CredentialMetadata:
    """可以安全回傳給使用者的部分。**沒有明文，也沒有密文。**"""

    configured: bool
    last_four: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ResolvedCredential:
    """給 Internal Gateway 用的。這是明文唯一離開這個模組的形狀。"""

    api_key: str
    base_url: str | None
    default_model: str | None
    source: str          # "AGENT" | "PLATFORM"


async def set_credential(
    conn: asyncpg.Connection,
    agent_id: uuid.UUID,
    *,
    api_key: str,
    base_url: str | None = None,
    default_model: str | None = None,
) -> CredentialMetadata:
    """寫入或覆寫。**覆寫會立刻對該 Agent 的所有版本生效**（ADR-0011）。"""
    ciphertext = secrets.encrypt(api_key)
    now = datetime.now(UTC)
    last_four = secrets.last_four(api_key)
    await conn.execute(
        """
        INSERT INTO agent_llm_credentials
            (agent_id, ciphertext, last_four, base_url, default_model, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $6)
        ON CONFLICT (agent_id) DO UPDATE SET
            ciphertext = EXCLUDED.ciphertext,
            last_four = EXCLUDED.last_four,
            base_url = EXCLUDED.base_url,
            default_model = EXCLUDED.default_model,
            updated_at = EXCLUDED.updated_at
        """,
        agent_id, ciphertext, last_four, base_url, default_model, now,
    )
    return CredentialMetadata(
        configured=True, last_four=last_four, base_url=base_url,
        default_model=default_model, updated_at=now,
    )


async def describe(conn: asyncpg.Connection, agent_id: uuid.UUID) -> CredentialMetadata:
    """回 metadata。**永遠不含金鑰**，連密文都不回。"""
    row = await conn.fetchrow(
        f"SELECT {AgentLlmCredential.returning()} FROM agent_llm_credentials WHERE agent_id = $1",
        agent_id,
    )
    if row is None:
        return CredentialMetadata(configured=False)
    return CredentialMetadata(
        configured=True, last_four=row["last_four"], base_url=row["base_url"],
        default_model=row["default_model"], updated_at=row["updated_at"],
    )


async def delete_credential(conn: asyncpg.Connection, agent_id: uuid.UUID) -> bool:
    """移除。之後該 Agent 會退回用平台預設金鑰。回傳有沒有真的刪到東西。"""
    result = await conn.execute(
        "DELETE FROM agent_llm_credentials WHERE agent_id = $1", agent_id)
    return result == "DELETE 1"


def pick_default_model(agent_default: str | None) -> str:
    """這個 Agent 沒指定 model 時該用哪個。**優先序的唯一定義。**"""
    from apps.api.config import get_settings

    return agent_default or get_settings().llm_default_model


async def default_model_for(conn: asyncpg.Connection, agent_id: uuid.UUID) -> str:
    """給 bootstrap：只讀 model 名稱，**不解密金鑰**。"""
    row = await conn.fetchval(
        "SELECT default_model FROM agent_llm_credentials WHERE agent_id = $1",
        agent_id,
    )
    return pick_default_model(row)


async def resolve(conn: asyncpg.Connection, agent_id: uuid.UUID) -> ResolvedCredential | None:
    """給 Gateway：這個 Agent 該用哪一把金鑰。**這是明文唯一離開這個模組的路徑。**"""
    row = await conn.fetchrow(
        f"SELECT {AgentLlmCredential.returning()} FROM agent_llm_credentials WHERE agent_id = $1",
        agent_id,
    )
    if row is None:
        return None
    cred = AgentLlmCredential.from_row(row)
    return ResolvedCredential(
        api_key=secrets.decrypt(cred.ciphertext),
        base_url=cred.base_url,
        default_model=cred.default_model,
        source="AGENT",
    )
