"""Run token —— P4 的實作。

綁定單一 run_id、TTL = 該 Run 的 timeout、跑完立即撤銷。
存 Redis 而不是資料表，因為 TTL 與撤銷本來就是 Redis 的原生能力。

**這是沙盒唯一拿得到的憑證。** 它只能對 Internal Gateway 使用，
而且帶著 A 的 token 存取 B 的 run_id 會被擋（見 deps.require_run_token）。

key 格式只定義在這裡（`key_for`），api 走 async、worker 走 sync，但共用同一份格式。
"""

import secrets
import uuid

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis

_PREFIX = "run_token:"
_RUN_TOKENS_PREFIX = "run_tokens:"


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


def new_token() -> str:
    return secrets.token_urlsafe(32)


def key_for(token: str) -> str:
    return _PREFIX + token


def run_key(run_id: uuid.UUID | str) -> str:
    """Return the Redis set containing all live tokens for one Run."""
    return _RUN_TOKENS_PREFIX + str(run_id)


async def resolve(redis: AsyncRedis, token: str) -> str | None:
    """回傳這個 token 綁定的 run_id，無效或過期則 None。"""
    return await redis.get(key_for(token))


async def issue(redis: AsyncRedis, run_id: uuid.UUID, ttl_seconds: int) -> str:
    token = new_token()
    async with redis.pipeline(transaction=True) as pipe:
        await pipe.set(key_for(token), str(run_id), ex=ttl_seconds)
        await pipe.sadd(run_key(run_id), token)
        await pipe.expire(run_key(run_id), ttl_seconds)
        await pipe.execute()
    return token


async def revoke(redis: AsyncRedis, token: str) -> None:
    """Immediately invalidate one run token.

    The operation is idempotent: deleting an already expired or revoked token
    is still a successful revocation.
    """
    run_id = await redis.get(key_for(token))
    async with redis.pipeline(transaction=True) as pipe:
        await pipe.delete(key_for(token))
        if run_id is not None:
            await pipe.srem(run_key(_text(run_id)), token)
        await pipe.execute()


async def revoke_run(redis: AsyncRedis, run_id: uuid.UUID | str) -> None:
    """Revoke every token issued for ``run_id`` and remove its index."""
    index = run_key(run_id)
    tokens = await redis.smembers(index)
    async with redis.pipeline(transaction=True) as pipe:
        for token in tokens:
            await pipe.delete(key_for(_text(token)))
        await pipe.delete(index)
        await pipe.execute()


def issue_sync(redis: SyncRedis, run_id: uuid.UUID | str, ttl_seconds: int) -> str:
    """Synchronous counterpart used by the Celery worker."""
    token = new_token()
    with redis.pipeline(transaction=True) as pipe:
        pipe.set(key_for(token), str(run_id), ex=ttl_seconds)
        pipe.sadd(run_key(run_id), token)
        pipe.expire(run_key(run_id), ttl_seconds)
        pipe.execute()
    return token


def revoke_sync(redis: SyncRedis, token: str) -> None:
    """Synchronous counterpart of :func:`revoke` for Celery."""
    run_id = redis.get(key_for(token))
    with redis.pipeline(transaction=True) as pipe:
        pipe.delete(key_for(token))
        if run_id is not None:
            pipe.srem(run_key(_text(run_id)), token)
        pipe.execute()


def revoke_run_sync(redis: SyncRedis, run_id: uuid.UUID | str) -> None:
    """Synchronous counterpart of :func:`revoke_run` for Celery."""
    index = run_key(run_id)
    tokens = redis.smembers(index)
    with redis.pipeline(transaction=True) as pipe:
        for token in tokens:
            pipe.delete(key_for(_text(token)))
        pipe.delete(index)
        pipe.execute()
