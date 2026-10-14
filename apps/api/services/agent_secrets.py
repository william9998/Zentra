"""per-agent 的具名機密（ZEN-019 / api-contract §2.7）。

**寫得進去，讀不出來。** 沿用 ADR-0011 §3 的形狀：這個模組
**不提供任何把明文回傳給 API 層的函式**。`describe()` / `list_secrets()` 回
metadata，`resolve()` 只給 Internal Gateway 的 egress／email 兩條路徑用。
這是靠「沒有那個函式」保證的，不是靠紀律。

與另外兩張機密表的差別**是刻意的，不要統一**：

  · `agent_secrets`（這裡）  Fernet 密文 —— 要解回來登入 SMTP、打外部 API
  · `agent_llm_credentials`  Fernet 密文（ZEN-012）—— 要轉發給供應商
  · `agent_api_keys`         SHA-256 雜湊（ZEN-015）—— 只需驗證身分

沙盒拿得到的只有**名字清單**（`secret_names`）。給名字是為了讓 agent 能 fail
fast 說出「缺 SMTP_PASSWORD」，而不是跑完整段分析才在最後一步失敗。
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError
from apps.api.models import AgentEgressRule, AgentEmailSender, AgentSecret
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


async def list_secrets(session: AsyncSession, agent_id: uuid.UUID) -> list[SecretMetadata]:
    rows = (await session.execute(
        select(AgentSecret).where(AgentSecret.agent_id == agent_id)
        .order_by(AgentSecret.name)
    )).scalars().all()
    return [_view(row) for row in rows]


async def names_for(session: AsyncSession, agent_id: uuid.UUID) -> list[str]:
    """給派工用：**排序後的名字，沒有值、沒有長度、沒有尾四碼**（契約 §4.1 第 5 點）。"""
    rows = (await session.execute(
        select(AgentSecret.name).where(AgentSecret.agent_id == agent_id)
        .order_by(AgentSecret.name)
    )).scalars().all()
    return list(rows)


async def exists(session: AsyncSession, agent_id: uuid.UUID, name: str) -> bool:
    row = await session.get(AgentSecret, (agent_id, name))
    return row is not None


async def set_secret(
    session: AsyncSession, agent_id: uuid.UUID, name: str, value: str,
) -> SecretMetadata:
    """寫入或覆寫。重複 `PUT` 同名是覆寫，`created_at` 保留。

    平台未設定 `ZENTRA_SECRET_KEY` 時在 `cipher.encrypt()` 就擋下 ——
    **不接受機密，而不是退回明文儲存**。
    """
    validate_name(name)
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_VALUE_LENGTH:
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR,
            f"secret value must be 1-{MAX_VALUE_LENGTH} characters",
            422, details={"field": "value"},
        )

    existing = await session.get(AgentSecret, (agent_id, name))
    if existing is None:
        count = len(await names_for(session, agent_id))
        if count >= MAX_SECRETS:
            raise ZentraError(
                ErrorCode.VALIDATION_ERROR,
                f"this agent already has {count} secrets, the limit is {MAX_SECRETS}",
                422, details={"field": "name"},
            )

    ciphertext = cipher.encrypt(value)      # 沒設 ZENTRA_SECRET_KEY 會在這裡擋下
    now = datetime.now(UTC)
    if existing is None:
        existing = AgentSecret(agent_id=agent_id, name=name, created_at=now)
        session.add(existing)
    existing.ciphertext = ciphertext
    existing.last_four = cipher.last_four(value)
    existing.updated_at = now
    await session.commit()
    return _view(existing)


async def referenced_by(
    session: AsyncSession, agent_id: uuid.UUID, name: str,
) -> list[dict[str, str]]:
    """誰以名字引用了這個機密（§2.8 的 `auth_secret_name`、§2.9 的 `password_secret_name`）。

    **引用檢查在服務層做，不是 DB constraint。** FK 到 `(agent_id, name)` 會讓
    刪除在 DB 層爆 constraint error，而契約要的是一個能說出「被誰引用」的 `409`。
    """
    users: list[dict[str, str]] = []
    rules = (await session.execute(
        select(AgentEgressRule)
        .where(AgentEgressRule.agent_id == agent_id,
               AgentEgressRule.auth_secret_name == name)
    )).scalars().all()
    for rule in rules:
        users.append({"kind": "egress_rule", "rule_id": str(rule.rule_id), "host": rule.host})
    sender = await session.get(AgentEmailSender, agent_id)
    if sender is not None and sender.password_secret_name == name:
        users.append({"kind": "email_sender", "smtp_host": sender.smtp_host})
    return users


async def delete_secret(session: AsyncSession, agent_id: uuid.UUID, name: str) -> None:
    """硬刪，重複刪除冪等（不存在也成功）。

    **還被引用時回 409** —— 直接刪掉會讓那條能力在下一次 Run 才無聲失敗。
    """
    users = await referenced_by(session, agent_id, name)
    if users:
        raise ZentraError(
            ErrorCode.VERSION_CONFLICT,
            f"secret {name!r} is still referenced; remove the reference first",
            409, details={"referenced_by": users},
        )
    await session.execute(
        delete(AgentSecret).where(
            AgentSecret.agent_id == agent_id, AgentSecret.name == name)
    )
    await session.commit()


async def resolve(session: AsyncSession, agent_id: uuid.UUID, name: str) -> str | None:
    """**給 Internal Gateway 用。這是明文唯一離開這個模組的路徑。**

    只有兩個呼叫端：§4.2 的出口代打（注入 auth header）與 §4.4 的寄信
    （登入 SMTP）。沒有第三個，也沒有任何 API 層的呼叫端。
    """
    row = await session.get(AgentSecret, (agent_id, name))
    if row is None:
        return None
    return cipher.decrypt(row.ciphertext)
