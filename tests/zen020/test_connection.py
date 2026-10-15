"""S2：URL 正規化、JSON 形狀、rows 對得上、D7 安全網（Z20-08／09／15／22）。"""

from __future__ import annotations

import uuid

import asyncpg
import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.config import normalize_database_url
from apps.api.db import _register_json_codecs
from apps.api.rows import ALL_ROWS
from tests.zen020.conftest import BUILTIN_AGENT_ID, psycopg_dsn, run_runner


def test_z20_08_url_normalisation() -> None:
    host = "zentra:zentra@postgres:5432/zentra"
    for raw in (
        f"postgresql://{host}",
        f"postgres://{host}",
        f"postgresql+asyncpg://{host}",
        f"postgresql+psycopg://{host}",
        f"postgresql+psycopg2://{host}",
    ):
        assert normalize_database_url(raw) == f"postgresql://{host}"
    with pytest.raises(ValueError, match="mysql"):
        normalize_database_url("mysql://zentra:zentra@localhost/zentra")


@pytest.mark.asyncio
async def test_z20_09_jsonb_is_object_not_string(fresh_database: str) -> None:
    assert run_runner(fresh_database).returncode == 0
    dsn = psycopg_dsn(fresh_database)
    run_id = uuid.uuid4()
    payload = {"question": "ping", "nested": {"ok": True}}

    pool = await asyncpg.create_pool(
        dsn=dsn, min_size=1, max_size=2, init=_register_json_codecs,
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO runs (id, agent_id, status, trigger, entrypoint, "
                "input, timeout_seconds) VALUES ($1, $2::uuid, 'QUEUED', 'API', "
                "'default', $3, 900)",
                run_id, BUILTIN_AGENT_ID, payload,
            )
            kind = await conn.fetchval(
                "SELECT jsonb_typeof(input) FROM runs WHERE id = $1", run_id
            )
            assert kind == "object"
            row = await conn.fetchrow("SELECT input FROM runs WHERE id = $1", run_id)
            assert row["input"] == payload
            assert isinstance(row["input"], dict)
    finally:
        await pool.close()

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        got = conn.execute(
            "SELECT input FROM runs WHERE id = %(id)s", {"id": run_id}
        ).fetchone()
        assert got["input"] == payload
        assert isinstance(got["input"], dict)
        worker_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO runs (id, agent_id, status, trigger, entrypoint, "
            "input, timeout_seconds) VALUES (%(id)s, %(agent)s, 'QUEUED', 'API', "
            "'default', %(input)s, 900)",
            {"id": worker_id, "agent": BUILTIN_AGENT_ID, "input": Jsonb({"from": "worker"})},
        )

    pool = await asyncpg.create_pool(
        dsn=dsn, min_size=1, max_size=2, init=_register_json_codecs,
    )
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT input FROM runs WHERE id = $1", worker_id)
            assert row["input"] == {"from": "worker"}
            kind = await conn.fetchval(
                "SELECT jsonb_typeof(input) FROM runs WHERE id = $1", worker_id
            )
            assert kind == "object"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_z20_15_rows_match_information_schema(fresh_database: str) -> None:
    from dataclasses import fields

    assert run_runner(fresh_database).returncode == 0
    conn = await asyncpg.connect(psycopg_dsn(fresh_database))
    try:
        assert len(ALL_ROWS) == 13
        for cls in ALL_ROWS:
            db_cols = {
                r["column_name"]
                for r in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=$1",
                    cls.TABLE,
                )
            }
            field_names = {f.name for f in fields(cls)}
            assert field_names == set(cls.COLUMNS) == db_cols, cls.TABLE
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_z20_22_idle_in_transaction_timeout() -> None:
    from apps.api.config import get_settings
    from apps.worker import db as worker_db

    settings = get_settings()
    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=1,
        max_size=2,
        server_settings={
            "application_name": "zentra-app",
            "idle_in_transaction_session_timeout": "60s",
        },
    )
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval("SHOW idle_in_transaction_session_timeout")
            name = await conn.fetchval("SHOW application_name")
        assert value in ("1min", "60s", "60s00ms")
        assert name == "zentra-app"
    finally:
        await pool.close()

    worker_db.reset_connection()
    with worker_db.connection() as conn:
        row = conn.execute("SHOW idle_in_transaction_session_timeout").fetchone()
        name = conn.execute("SHOW application_name").fetchone()
    # dict_row → first column values
    idle = next(iter(row.values()))
    app = next(iter(name.values()))
    assert idle in ("1min", "60s")
    assert app == "zentra-worker"

    with psycopg.connect(
        settings.dsn,
        autocommit=True,
        row_factory=dict_row,
        application_name="zentra-migrate",
    ) as conn:
        idle = conn.execute("SHOW idle_in_transaction_session_timeout").fetchone()
        assert next(iter(idle.values())) in ("0", "0s")
