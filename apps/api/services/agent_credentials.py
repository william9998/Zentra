"""Per-agent LLM 憑證的存取（ZEN-012）。

**明文只在兩個地方存在**：`set()` 收到請求的那一刻，以及 `resolve()` 交給
Internal Gateway 換金鑰的那一刻。中間全程是密文。

沒有任何函式會把明文回傳給 API 層 —— `describe()` 回的是 metadata，
`resolve()` 只給 Gateway 用。這不是靠紀律，是靠**沒有那個函式**。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models import AgentLlmCredential
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
    session: AsyncSession,
    agent_id: uuid.UUID,
    *,
    api_key: str,
    base_url: str | None = None,
    default_model: str | None = None,
) -> CredentialMetadata:
    """寫入或覆寫。**覆寫會立刻對該 Agent 的所有版本生效**（ADR-0011）。"""
    ciphertext = secrets.encrypt(api_key)          # 沒設 ZENTRA_SECRET_KEY 會在這裡擋下
    now = datetime.now(UTC)

    existing = await session.get(AgentLlmCredential, agent_id)
    if existing is None:
        existing = AgentLlmCredential(agent_id=agent_id, created_at=now)
        session.add(existing)
    existing.ciphertext = ciphertext
    existing.last_four = secrets.last_four(api_key)
    existing.base_url = base_url
    existing.default_model = default_model
    existing.updated_at = now
    await session.commit()

    return CredentialMetadata(
        configured=True, last_four=existing.last_four, base_url=base_url,
        default_model=default_model, updated_at=now,
    )


async def describe(session: AsyncSession, agent_id: uuid.UUID) -> CredentialMetadata:
    """回 metadata。**永遠不含金鑰**，連密文都不回。"""
    row = await session.get(AgentLlmCredential, agent_id)
    if row is None:
        return CredentialMetadata(configured=False)
    return CredentialMetadata(
        configured=True, last_four=row.last_four, base_url=row.base_url,
        default_model=row.default_model, updated_at=row.updated_at,
    )


async def delete_credential(session: AsyncSession, agent_id: uuid.UUID) -> bool:
    """移除。之後該 Agent 會退回用平台預設金鑰。回傳有沒有真的刪到東西。"""
    result = await session.execute(
        delete(AgentLlmCredential).where(AgentLlmCredential.agent_id == agent_id)
    )
    await session.commit()
    return bool(result.rowcount)


def pick_default_model(agent_default: str | None) -> str:
    """這個 Agent 沒指定 model 時該用哪個。**優先序的唯一定義。**

    Gateway、bootstrap 與常駐容器的工作項三個地方都要回答這個問題，
    各自寫一次的話它們遲早會不一致 —— 而不一致時，畫面上看起來完全一樣。
    """
    from apps.api.config import get_settings

    return agent_default or get_settings().llm_default_model


async def default_model_for(session: AsyncSession, agent_id: uuid.UUID) -> str:
    """給 bootstrap：只讀 model 名稱，**不解密金鑰**。

    用 `resolve()` 也拿得到，但那會把明文金鑰解出來丟掉 ——
    為了一個 model 名稱去碰明文是沒必要的風險。
    """
    row = (await session.execute(
        select(AgentLlmCredential.default_model)
        .where(AgentLlmCredential.agent_id == agent_id)
    )).scalar_one_or_none()
    return pick_default_model(row)


async def resolve(session: AsyncSession, agent_id: uuid.UUID) -> ResolvedCredential | None:
    """給 Gateway：這個 Agent 該用哪一把金鑰。

    有自己的就用自己的，沒有回 `None`（呼叫方退回平台預設）。
    **這是明文唯一離開這個模組的路徑。**
    """
    row = (await session.execute(
        select(AgentLlmCredential).where(AgentLlmCredential.agent_id == agent_id)
    )).scalar_one_or_none()
    if row is None:
        return None
    return ResolvedCredential(
        api_key=secrets.decrypt(row.ciphertext),
        base_url=row.base_url,
        default_model=row.default_model,
        source="AGENT",
    )
