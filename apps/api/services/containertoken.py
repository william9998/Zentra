"""Container token —— 常駐容器用來「取工作」的憑證（ZEN-015 / ADR-0012）。

## 為什麼需要一種新的 token

一次性沙盒啟動時就知道自己要跑哪個 Run，所以拿 run token 就夠。
常駐容器啟動時**還不知道**要跑什麼 —— 它得先問。那一問需要憑證。

## 它跟 P4 的關係（這段要看懂再改）

P4 說「沙盒內拿不到任何長期憑證」。container token 的存活時間等於容器的存活時間，
**按字面它是長期的**。所以這裡靠的不是「短命」，是**權限窄到洩漏也沒用**：

    container token 能做的事，只有一件：
        GET /internal/agents/{它綁定的那個 agent}/work

    它**不能**：
      · 讀寫任何 Run（那要 run token，而 run token 跟著工作項才進來）
      · 呼叫 LLM（`/internal/llm/*` 只認 run token）
      · 取別的 Agent 的工作（token 綁定單一 agent_id）

最壞情況是「某個 Agent 的容器可以搶自己 Agent 的工作」——
而那個容器本來就是要處理那些工作的。跨 Agent 拿不到，這是關鍵的那條線。

## 與 run token 的分工

    容器啟動  → 只有 container token
    取到工作  → 工作項裡附帶**那一個 Run 的** run token，TTL = 該 Run 的 timeout
    Run 結束  → run token 撤銷，container token 不受影響

所以真正有威力的憑證仍然是「一個 Run 一把、跑完就沒」。
"""

import secrets
import uuid

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis

_PREFIX = "container_token:"
_AGENT_PREFIX = "container_tokens:"


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


def new_token() -> str:
    return secrets.token_urlsafe(32)


def key_for(token: str) -> str:
    return _PREFIX + token


def agent_key(agent_id: uuid.UUID | str) -> str:
    """某個 Agent 目前所有有效的 container token。停止 Agent 時要一次撤銷。"""
    return _AGENT_PREFIX + str(agent_id)


async def resolve(redis: AsyncRedis, token: str) -> str | None:
    """回傳這個 token 綁定的 agent_id，無效則 None。"""
    value = await redis.get(key_for(token))
    return None if value is None else _text(value)


def issue_sync(redis: SyncRedis, agent_id: uuid.UUID | str, ttl_seconds: int) -> str:
    """worker 建容器時發，所以是同步版。

    TTL 給得比預期壽命長 —— 容器還活著而 token 過期的話，它會取不到工作
    而且沒有任何錯誤訊息，那是最難查的一種故障。過期由「停止容器」主動撤銷，
    TTL 只是防止忘記撤銷時無限殘留。
    """
    token = new_token()
    with redis.pipeline(transaction=True) as pipe:
        pipe.set(key_for(token), str(agent_id), ex=ttl_seconds)
        pipe.sadd(agent_key(agent_id), token)
        pipe.expire(agent_key(agent_id), ttl_seconds)
        pipe.execute()
    return token


def revoke_agent_sync(redis: SyncRedis, agent_id: uuid.UUID | str) -> None:
    """撤銷某個 Agent 的全部 container token。停止或重啟容器時呼叫。"""
    index = agent_key(agent_id)
    tokens = redis.smembers(index)
    with redis.pipeline(transaction=True) as pipe:
        for token in tokens:
            pipe.delete(key_for(_text(token)))
        pipe.delete(index)
        pipe.execute()


async def revoke_agent(redis: AsyncRedis, agent_id: uuid.UUID | str) -> None:
    """非同步版，給 api 服務用。"""
    index = agent_key(agent_id)
    tokens = await redis.smembers(index)
    async with redis.pipeline(transaction=True) as pipe:
        for token in tokens:
            await pipe.delete(key_for(_text(token)))
        await pipe.delete(index)
        await pipe.execute()
