"""S1：一次性腳本 Z20-03／Z20-03b／Z20-05／Z20-05b／Z20-21。需要主機 docker compose。"""

from __future__ import annotations

import subprocess
import pytest

from tests.zen020.conftest import (
    FIXTURES,
    ROOT,
    require_docker,
    run_runner,
    temporary_database,
)

SCRIPT = ROOT / "scripts" / "adopt-legacy-schema.sh"


def _adopt(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _psql(database: str, *args: str, input_sql: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [
        "docker", "compose", "exec", "-T", "postgres",
        "psql", "-U", "zentra", "-d", database, "-v", "ON_ERROR_STOP=1",
        *args,
    ]
    return subprocess.run(
        cmd,
        cwd=ROOT,
        input=input_sql,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _restore_legacy(database: str) -> None:
    dump = (FIXTURES / "legacy_0008_full.sql").read_text()
    result = _psql(database, input_sql=dump)
    assert result.returncode == 0, result.stdout + result.stderr


def _pg_dump(database: str, extra: list[str] | None = None) -> str:
    cmd = [
        "docker", "compose", "exec", "-T", "postgres",
        "pg_dump", "-U", "zentra", "--schema-only", "--no-owner", "--no-privileges",
        *(extra or []),
        database,
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _normalize_dump(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("\\restrict") or line.startswith("\\unrestrict"):
            continue
        if line.startswith("-- Dumped from") or line.startswith("-- Dumped by"):
            continue
        lines.append(line)
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def _docker() -> None:
    require_docker()


def _strip_schema_migrations(dump: str) -> str:
    """拿掉 schema_migrations 的區塊，方便比對「只差這張表」。"""
    keep: list[str] = []
    skipping = False
    for line in dump.splitlines():
        if "schema_migrations" in line:
            skipping = True
            continue
        if skipping:
            if line.startswith("--") and "Name:" in line and "schema_migrations" not in line:
                skipping = False
                keep.append(line)
            elif line.startswith("--") and line.endswith("Owner: -"):
                continue
            elif line.strip() == "":
                continue
            elif skipping and not line.startswith("ALTER") and not line.startswith("CREATE") and not line.startswith("    "):
                skipping = False
                keep.append(line)
            continue
        keep.append(line)
    return "\n".join(keep)


@pytest.mark.asyncio
async def test_z20_03_adopt_then_runner_does_not_apply_ddl() -> None:
    async with temporary_database() as database:
        _restore_legacy(database)
        before = _normalize_dump(_pg_dump(database))
        count_sql = (
            "SELECT 'agents=' || (SELECT count(*) FROM agents) || "
            " ' versions=' || (SELECT count(*) FROM agent_versions) || "
            " ' runs=' || (SELECT count(*) FROM runs);"
        )
        before_counts = _psql(database, "-t", "-A", "-c", count_sql).stdout
        adopted = _adopt("--database", database)
        assert adopted.returncode == 0, adopted.stdout + adopted.stderr
        assert "0001" in adopted.stdout and "0008" in adopted.stdout
        ran = run_runner(database)
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert "套用 " not in ran.stdout
        after = _normalize_dump(_pg_dump(database))
        assert "CREATE TABLE public.schema_migrations" in after
        assert "alembic_version" in after
        assert _strip_schema_migrations(before) == _strip_schema_migrations(after)
        version = _psql(database, "-t", "-A", "-c", "SELECT version_num FROM alembic_version;").stdout.strip()
        assert version == "0008"
        n = _psql(database, "-t", "-A", "-c", "SELECT count(*) FROM schema_migrations;").stdout.strip()
        assert n == "8"
        after_counts = _psql(database, "-t", "-A", "-c", count_sql).stdout
        assert after_counts == before_counts


@pytest.mark.asyncio
async def test_z20_03b_schema_migrations_ddl_matches_runner() -> None:
    async with temporary_database() as via_script, temporary_database() as via_runner:
        _restore_legacy(via_script)
        assert _adopt("--database", via_script).returncode == 0
        assert run_runner(via_runner).returncode == 0
        dump_script = _pg_dump(via_script, ["-t", "schema_migrations"])
        dump_runner = _pg_dump(via_runner, ["-t", "schema_migrations"])
        assert _normalize_dump(dump_script) == _normalize_dump(dump_runner)


@pytest.mark.asyncio
async def test_z20_05_runner_on_restored_legacy_does_not_change_anything() -> None:
    async with temporary_database() as database:
        _restore_legacy(database)
        before = _pg_dump(database)
        ran = run_runner(database)
        assert ran.returncode != 0
        assert "scripts/adopt-legacy-schema.sh" in ran.stderr
        after = _pg_dump(database)
        assert _normalize_dump(before) == _normalize_dump(after)
        exists = _psql(
            database, "-t", "-A", "-c",
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL;",
        ).stdout.strip()
        assert exists == "f"


@pytest.mark.asyncio
async def test_z20_05b_script_rejects_wrong_legacy_version() -> None:
    async with temporary_database() as database:
        _psql(database, "-c", "CREATE TABLE leftover (id int PRIMARY KEY);")
        rejected = _adopt("--database", database)
        assert rejected.returncode != 0
        assert "0008" in rejected.stderr

        _psql(
            database, "-c",
            "CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);"
            "INSERT INTO alembic_version VALUES ('0007');",
        )
        rejected7 = _adopt("--database", database)
        assert rejected7.returncode != 0
        assert "0008" in rejected7.stderr
        exists = _psql(
            database, "-t", "-A", "-c",
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL;",
        ).stdout.strip()
        assert exists == "f"


@pytest.mark.asyncio
async def test_z20_21_dry_run_and_idempotent() -> None:
    async with temporary_database() as database:
        _restore_legacy(database)
        dry = _adopt("--dry-run", "--database", database)
        assert dry.returncode == 0, dry.stdout + dry.stderr
        assert "0001" in dry.stdout and "0008" in dry.stdout
        exists = _psql(
            database, "-t", "-A", "-c",
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL;",
        ).stdout.strip()
        assert exists == "f"

        first = _adopt("--database", database)
        assert first.returncode == 0, first.stdout + first.stderr
        second = _adopt("--database", database)
        assert second.returncode == 0, second.stdout + second.stderr
        assert "已接過" in second.stdout
