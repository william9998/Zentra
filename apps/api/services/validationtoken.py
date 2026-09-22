"""Short-lived tokens scoped to one Agent version validation."""

import secrets
import uuid

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis

_PREFIX = "validation_token:"
_VERSION_TOKENS_PREFIX = "validation_tokens:"


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


def key_for(token: str) -> str:
    return _PREFIX + token


def version_key(version_id: uuid.UUID | str) -> str:
    return _VERSION_TOKENS_PREFIX + str(version_id)


async def resolve(redis: AsyncRedis, token: str) -> str | None:
    return await redis.get(key_for(token))


def issue_sync(redis: SyncRedis, version_id: uuid.UUID | str, ttl_seconds: int = 600) -> str:
    ttl = min(max(ttl_seconds, 1), 600)
    token = secrets.token_urlsafe(32)
    with redis.pipeline(transaction=True) as pipe:
        pipe.set(key_for(token), str(version_id), ex=ttl)
        pipe.sadd(version_key(version_id), token)
        pipe.expire(version_key(version_id), ttl)
        pipe.execute()
    return token


def revoke_version_sync(redis: SyncRedis, version_id: uuid.UUID | str) -> None:
    index = version_key(version_id)
    tokens = redis.smembers(index)
    with redis.pipeline(transaction=True) as pipe:
        for token in tokens:
            pipe.delete(key_for(_text(token)))
        pipe.delete(index)
        pipe.execute()
