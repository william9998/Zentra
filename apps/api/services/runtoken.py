"""Run token —— P4 的實作。

綁定單一 run_id、TTL = 該 Run 的 timeout、跑完立即撤銷。
存 Redis 而不是資料表，因為 TTL 與撤銷是 Redis 的原生能力。

**這是沙盒唯一拿得到的憑證。** 它只能對 Internal Gateway 使用，
而且帶著 A 的 token 去存取 B 的 run_id 會被擋（見 deps.require_run_token）。
"""

import secrets
import uuid

from redis.asyncio import Redis

_PREFIX = "run_token:"


async def issue(redis: Redis, run_id: uuid.UUID, ttl_seconds: int) -> str:
    token = secrets.token_urlsafe(32)
    await redis.set(_PREFIX + token, str(run_id), ex=ttl_seconds)
    return token


async def resolve(redis: Redis, token: str) -> str | None:
    """回傳這個 token 綁定的 run_id，無效或過期則 None。"""
    return await redis.get(_PREFIX + token)


async def revoke(redis: Redis, token: str) -> None:
    await redis.delete(_PREFIX + token)
