"""per-agent 的具名機密（ZEN-019 / api-contract §2.7）。

**寫得進去，讀不出來。** 沿用 ADR-0011 §3 的形狀：這個模組
**不提供任何把明文回傳給 API 層的函式**。`describe()` / `list_secrets()` 回
metadata，`resolve()` 只給 Internal Gateway 的 egress／email 兩條路徑用。
這是靠「沒有那個函式」保證的，不是靠紀律。
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg

from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError
from apps.api.rows import AgentSecret
from apps.api.services import secrets as cipher

#: 契約 §2.7 第 3 點。名字**長得像環境變數，但它永遠不會變成環境變數** ——
#: 沙盒的 env 是精確集合檢查（§4.1 第 3 點），機密一個都不會出現在那裡。
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
MAX_SECRETS = 50
MAX_VALUE_LENGTH = 4096


@dataclass(frozen=True)
class SecretMetadata:
    """可以安全回傳給使用者的部分。**沒有明文，也沒有密文。**"""

    name: str
    last_four: str
    created_at: datetime
    updated_at: datetime


def _view(row: AgentSecret) -> SecretMetadata:
    return SecretMetadata(
        name=row.name, last_four=row.last_four,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR,
            f"secret name {name!r} is invalid: it must match {NAME_PATTERN.pattern}",
            422, details={"field": "name"},
        )
    return name


async def list_secrets(conn: asyncpg.Connection, agent_id: uuid.UUID) -> list[SecretMetadata]:
    rows = await conn.fetch(
        f"SELECT {AgentSecret.returning()} FROM agent_secrets "
        "WHERE agent_id = $1 ORDER BY name",
        agent_id,
    )
    return [_view(AgentSecret.from_row(row)) for row in rows]


async def names_for(conn: asyncpg.Connection, agent_id: uuid.UUID) -> list[str]:
    """給派工用：**排序後的名字，沒有值、沒有長度、沒有尾四碼**（契約 §4.1 第 5 點）。"""
    rows = await conn.fetch(
        "SELECT name FROM agent_secrets WHERE agent_id = $1 ORDER BY name", agent_id)
    return [row["name"] for row in rows]


async def exists(conn: asyncpg.Connection, agent_id: uuid.UUID, name: str) -> bool:
    value = await conn.fetchval(
        "SELECT 1 FROM agent_secrets WHERE agent_id = $1 AND name = $2",
        agent_id, name,
    )
    return value is not None


async def set_secret(
    conn: asyncpg.Connection, agent_id: uuid.UUID, name: str, value: str,
) -> SecretMetadata:
    """寫入或覆寫。重複 `PUT` 同名是覆寫，`created_at` 保留。"""
    validate_name(name)
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_VALUE_LENGTH:
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR,
            f"secret value must be 1-{MAX_VALUE_LENGTH} characters",
            422, details={"field": "value"},
        )

    existing = await conn.fetchrow(
        f"SELECT {AgentSecret.returning()} FROM agent_secrets "
        "WHERE agent_id = $1 AND name = $2",
        agent_id, name,
    )
    if existing is None:
        count = await conn.fetchval(
            "SELECT count(*) FROM agent_secrets WHERE agent_id = $1", agent_id)
        if count >= MAX_SECRETS:
            raise ZentraError(
                ErrorCode.VALIDATION_ERROR,
                f"this agent already has {count} secrets, the limit is {MAX_SECRETS}",
                422, details={"field": "name"},
            )

    ciphertext = cipher.encrypt(value)
    now = datetime.now(UTC)
    last_four = cipher.last_four(value)
    if existing is None:
        row = await conn.fetchrow(
            f"""
            INSERT INTO agent_secrets
                (agent_id, name, ciphertext, last_four, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $5)
            RETURNING {AgentSecret.returning()}
            """,
            agent_id, name, ciphertext, last_four, now,
        )
    else:
        row = await conn.fetchrow(
            f"""
            UPDATE agent_secrets
            SET ciphertext = $3, last_four = $4, updated_at = $5
            WHERE agent_id = $1 AND name = $2
            RETURNING {AgentSecret.returning()}
            """,
            agent_id, name, ciphertext, last_four, now,
        )
    return _view(AgentSecret.from_row(row))


async def referenced_by(
    conn: asyncpg.Connection, agent_id: uuid.UUID, name: str,
) -> list[dict[str, str]]:
    """誰以名字引用了這個機密。引用檢查在服務層做，不是 DB constraint。"""
    users: list[dict[str, str]] = []
    rules = await conn.fetch(
        "SELECT rule_id, host FROM agent_egress_rules "
        "WHERE agent_id = $1 AND auth_secret_name = $2",
        agent_id, name,
    )
    for rule in rules:
        users.append({"kind": "egress_rule", "rule_id": str(rule["rule_id"]), "host": rule["host"]})
    sender = await conn.fetchrow(
        "SELECT smtp_host, password_secret_name FROM agent_email_sender WHERE agent_id = $1",
        agent_id,
    )
    if sender is not None and sender["password_secret_name"] == name:
        users.append({"kind": "email_sender", "smtp_host": sender["smtp_host"]})
    return users


async def delete_secret(conn: asyncpg.Connection, agent_id: uuid.UUID, name: str) -> None:
    """硬刪，重複刪除冪等（不存在也成功）。還被引用時回 409。"""
    users = await referenced_by(conn, agent_id, name)
    if users:
        raise ZentraError(
            ErrorCode.VERSION_CONFLICT,
            f"secret {name!r} is still referenced; remove the reference first",
            409, details={"referenced_by": users},
        )
    await conn.execute(
        "DELETE FROM agent_secrets WHERE agent_id = $1 AND name = $2",
        agent_id, name,
    )


async def resolve(conn: asyncpg.Connection, agent_id: uuid.UUID, name: str) -> str | None:
    """**給 Internal Gateway 用。這是明文唯一離開這個模組的路徑。**"""
    ciphertext = await conn.fetchval(
        "SELECT ciphertext FROM agent_secrets WHERE agent_id = $1 AND name = $2",
        agent_id, name,
    )
    if ciphertext is None:
        return None
    return cipher.decrypt(ciphertext)
