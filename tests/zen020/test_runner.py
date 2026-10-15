"""S1：runner 在空庫套用、冪等、checksum、拒絕舊庫、併發（Z20-01/02/04/05/06/07）。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import asyncpg
import pytest

from apps.api.migrations.__main__ import MIGRATION_LOCK_KEY, SCHEMA_MIGRATIONS_DDL, checksum, migration_files
from tests.zen020.conftest import (
    BUILTIN_AGENT_ID,
    FIXTURES,
    ROOT,
    psycopg_dsn,
    run_runner,
    temporary_database,
)

MIGRATIONS = ROOT / "apps" / "api" / "migrations"
#: repo 裡所有的 migration 版本。0008 之後還會一直加，不要寫死。
ALL_VERSIONS = [version for version, _name, _path in migration_files()]


def _fingerprint_sql() -> str:
    return """
    SELECT 'column' AS kind, table_name || '.' || column_name AS key,
           ordinal_position::text || ' ' || data_type || ' ' || is_nullable
           || ' ' || coalesce(column_default, '') AS val
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name NOT IN ('schema_migrations', 'alembic_version')
    UNION ALL
    SELECT 'constraint', c.conname,
           pg_get_constraintdef(c.oid)
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public' AND t.relname NOT IN ('schema_migrations', 'alembic_version')
    UNION ALL
    SELECT 'index', i.relname, pg_get_indexdef(i.oid)
    FROM pg_class i
    JOIN pg_namespace n ON n.oid = i.relnamespace
    JOIN pg_index x ON x.indexrelid = i.oid
    JOIN pg_class t ON t.oid = x.indrelid
    WHERE n.nspname = 'public' AND i.relkind = 'i'
      AND t.relname NOT IN ('schema_migrations', 'alembic_version')
    ORDER BY 1, 2, 3
    """


async def _fingerprint(dsn: str) -> set[tuple[str, str, str]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(_fingerprint_sql())
        return {(r["kind"], r["key"], r["val"]) for r in rows}
    finally:
        await conn.close()


def _run_alembic(database: str):
    import os
    import shutil
    import subprocess

    if shutil.which("alembic") is None:
        pytest.skip("alembic 已不在 PATH（S6 之後改比對 S0 dump）")
    env = os.environ.copy()
    env["DATABASE_URL"] = psycopg_dsn(database).replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    env["PYTHONPATH"] = env.get("PYTHONPATH", "/srv")
    return subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.asyncio
async def test_z20_01_runner_schema_matches_alembic() -> None:
    async with temporary_database() as alembic_db, temporary_database() as runner_db:
        alembic = _run_alembic(alembic_db)
        assert alembic.returncode == 0, alembic.stdout + alembic.stderr
        runner = run_runner(runner_db)
        assert runner.returncode == 0, runner.stdout + runner.stderr
        expected = await _fingerprint(psycopg_dsn(alembic_db))
        actual = await _fingerprint(psycopg_dsn(runner_db))
        assert actual == expected, (
            f"only alembic {sorted(expected - actual)[:20]!r} "
            f"only runner {sorted(actual - expected)[:20]!r}"
        )


@pytest.mark.asyncio
async def test_z20_02_empty_database_gets_fixture_and_every_version(fresh_database: str) -> None:
    result = run_runner(fresh_database)
    assert result.returncode == 0, result.stdout + result.stderr
    for version in ALL_VERSIONS:
        assert f"套用 {version}" in result.stdout

    conn = await asyncpg.connect(psycopg_dsn(fresh_database))
    try:
        name = await conn.fetchval(
            "SELECT name FROM agents WHERE id = $1::uuid", BUILTIN_AGENT_ID
        )
        assert name == "builtin-echo"
        rows = await conn.fetch(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )
        assert [r["version"] for r in rows] == ALL_VERSIONS
        files = {version: path for version, _name, path in migration_files()}
        for row in rows:
            assert row["checksum"] == checksum(files[row["version"]])
            assert row["checksum"] == hashlib.sha256(
                files[row["version"]].read_bytes()
            ).hexdigest()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_z20_04_runner_is_idempotent(fresh_database: str) -> None:
    first = run_runner(fresh_database)
    assert first.returncode == 0, first.stdout + first.stderr
    second = run_runner(fresh_database)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "套用 " not in second.stdout
    for version in ALL_VERSIONS:
        assert f"已存在 {version}" in second.stdout


@pytest.mark.asyncio
async def test_z20_06_checksum_mismatch_refuses(fresh_database: str, tmp_path: Path) -> None:
    assert run_runner(fresh_database).returncode == 0
    target = MIGRATIONS / "0008_agent_config_secrets_egress.sql"
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n-- touched\n")
        result = run_runner(fresh_database)
    finally:
        target.write_bytes(original)
    assert result.returncode != 0
    assert "0008" in result.stderr
    assert "checksum mismatch" in result.stderr


@pytest.mark.asyncio
async def test_z20_07_two_runners_apply_each_file_once(fresh_database: str) -> None:
    first, second = await asyncio.gather(
        asyncio.to_thread(run_runner, fresh_database),
        asyncio.to_thread(run_runner, fresh_database),
    )
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    applied = (first.stdout + second.stdout).count("套用 0001 ")
    assert applied == 1
    conn = await asyncpg.connect(psycopg_dsn(fresh_database))
    try:
        n = await conn.fetchval("SELECT count(*) FROM schema_migrations")
        assert n == len(ALL_VERSIONS)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_z20_05_runner_refuses_legacy_dump_without_adopt(fresh_database: str) -> None:
    """還原 Alembic 0008 庫後直接跑 runner → 非 0，且不建 schema_migrations。"""
    dump = (FIXTURES / "legacy_0008_full.sql").read_text()
    if "CREATE TABLE public.alembic_version" not in dump:
        pytest.fail("S0 fixture 缺少 alembic_version")
    # 沒有 psql 時用最小舊庫：建幾張表、不建 schema_migrations。
    conn = await asyncpg.connect(psycopg_dsn(fresh_database))
    try:
        await conn.execute("CREATE TABLE leftover (id int PRIMARY KEY)")
        before = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
    finally:
        await conn.close()

    result = run_runner(fresh_database)
    assert result.returncode != 0
    assert "scripts/adopt-legacy-schema.sh" in result.stderr

    conn = await asyncpg.connect(psycopg_dsn(fresh_database))
    try:
        after = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
        names = {r["table_name"] for r in after}
        assert "schema_migrations" not in names
        assert {r["table_name"] for r in before} == names
    finally:
        await conn.close()


def test_lock_key_is_the_documented_constant() -> None:
    assert MIGRATION_LOCK_KEY == 820020
    assert "CREATE TABLE schema_migrations" in SCHEMA_MIGRATIONS_DDL
    assert "alembic" not in SCHEMA_MIGRATIONS_DDL.lower()


def test_runner_source_has_no_forbidden_words() -> None:
    text = (MIGRATIONS / "__main__.py").read_text()
    assert "alembic" not in text.lower()
    for path in MIGRATIONS.glob("*.sql"):
        body = path.read_text().lower()
        assert "alembic" not in body, path.name
        assert "revision id" not in body, path.name
        assert "begin;" not in body
        assert "commit;" not in body
