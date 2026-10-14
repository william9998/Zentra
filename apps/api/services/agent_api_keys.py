"""下游應用呼叫 Agent 用的 API key（ZEN-015）。

## 為什麼存雜湊，而 `agent_llm_credentials` 存密文

兩張表都放「機密」，但用途不同，所以做法**刻意不同**：

    agent_api_keys          平台只需要驗證「來的人是誰」
                            → SHA-256 雜湊。DB 外洩也還原不出可用的 key。
    agent_llm_credentials   平台要把明文轉發給供應商
                            → 只能加密，不能雜湊。

**不要把兩者統一成一套。** 統一的結果一定是兩邊都用可逆加密，
那等於把本來不需要可逆的東西變得可逆。

## 為什麼是 SHA-256 而不是 bcrypt/argon2

那些是為**低熵的人選密碼**設計的，慢是它們的功能。這裡的 key 是 256 bit 的
隨機值，暴力破解不可行，而驗證發生在**每一次 API 請求**上 ——
用 bcrypt 等於給每個請求加上幾十毫秒，換一個對隨機 key 沒有意義的保護。
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.errors import not_found
from apps.api.ids import uuid7
from apps.api.models import AgentApiKey

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


async def issue(session: AsyncSession, agent_id: uuid.UUID, name: str) -> IssuedKey:
    plaintext = KEY_PREFIX + secrets.token_urlsafe(_SECRET_BYTES)
    row = AgentApiKey(
        id=uuid7(),
        agent_id=agent_id,
        name=name,
        key_hash=hash_key(plaintext),
        key_prefix=_display_prefix(plaintext),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return IssuedKey(row=row, plaintext=plaintext)


async def list_keys(session: AsyncSession, agent_id: uuid.UUID) -> list[AgentApiKey]:
    """列出所有 key，**含已撤銷的** —— 歷史用量還掛在它們身上，藏起來會讓帳無主。"""
    return list((await session.execute(
        select(AgentApiKey)
        .where(AgentApiKey.agent_id == agent_id)
        .order_by(AgentApiKey.created_at.desc())
    )).scalars())


async def load_key(session: AsyncSession, agent_id: uuid.UUID, key_id: str) -> AgentApiKey:
    """取一把屬於這個 Agent 的 key。不存在或屬於別的 Agent 一律 404。

    不分「不存在」與「別人的」是刻意的：分開回答等於告訴呼叫端哪些 key id 真的存在。
    """
    try:
        kid = uuid.UUID(key_id)
    except ValueError:
        raise not_found("agent api key") from None
    row = (await session.execute(
        select(AgentApiKey).where(
            AgentApiKey.id == kid, AgentApiKey.agent_id == agent_id)
    )).scalar_one_or_none()
    if row is None:
        raise not_found("agent api key")
    return row


async def revoke(session: AsyncSession, row: AgentApiKey) -> AgentApiKey:
    """軟撤銷。**不硬刪** —— 刪掉會讓這把 key 過去的用量變成無主的帳。"""
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(row)
    return row


async def resolve(session: AsyncSession, plaintext: str) -> AgentApiKey | None:
    """用明文找出對應的 key。已撤銷的回 None。

    比對的是雜湊，所以資料庫裡沒有任何可用的 key。
    """
    row = (await session.execute(
        select(AgentApiKey).where(AgentApiKey.key_hash == hash_key(plaintext))
    )).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    return row


async def touch(session: AsyncSession, row: AgentApiKey) -> None:
    """記下最後使用時間。用來回答「這把還有人在用嗎，可以撤掉嗎」。"""
    row.last_used_at = datetime.now(UTC)
    await session.commit()
