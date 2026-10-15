import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from apps.api.config import get_settings

ACQUIRE_TIMEOUT = 30.0
_pool: asyncpg.Pool | None = None


async def _register_json_codecs(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(
            typ,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
            format="text",
        )


async def init_pool() -> None:
    global _pool
    if _pool is not None:
        return
    settings = get_settings()
    _pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.db_pool_size,
        max_size=settings.db_pool_size + settings.db_max_overflow,
        init=_register_json_codecs,
        server_settings={
            "application_name": "zentra-app",
            "idle_in_transaction_session_timeout": "60s",
        },
    )


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("db pool is not initialised; call init_pool() during lifespan")
    return _pool


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    async with pool().acquire(timeout=ACQUIRE_TIMEOUT) as conn:
        async with conn.transaction():
            yield conn


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    async with pool().acquire(timeout=ACQUIRE_TIMEOUT) as conn:
        yield conn
