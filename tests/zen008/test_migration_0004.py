"""`0004_oci_agent_registry` 的回歸釘。

這個檔案存在的原因很具體：0004 在交件後被抓到**三個**會讓它整個跑不起來的 bug
（uuid 欄位收到 Python `str`、`text()` 把 JSON 裡的 `:true` 當成綁定參數、
以及引用了已刪除的常數）。沒有測試釘住，下一次動它會再壞一次，而且是
「四個服務全部起不來」等級的壞。

作法：每條測試開一個**全新的空資料庫**，在 api 容器裡跑
`python -m apps.api.migrations`。不是讀 migration 原始碼，是真的讓 PostgreSQL
執行它。
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

import asyncpg
import psycopg
import pytest
from psycopg.rows import dict_row

from apps.api.migrations.__main__ import SCHEMA_MIGRATIONS_DDL, checksum
from tests.zen008.conftest import db_url

API_CONTAINER = "zentra-app-1"
BUILTIN_AGENT_ID = "01930000-0000-7000-8000-000000000001"
BUILTIN_VERSION_ID = "01930000-0000-7000-8000-000000000002"
MIGRATIONS = Path(__file__).resolve().parents[2] / "apps/api/migrations"


def _dsn(database: str | None = None) -> str:
    """把測試 URL 轉成 asyncpg / psycopg DSN（測試從主機經 socat 代理連）。"""
    from apps.api.config import normalize_database_url

    raw = normalize_database_url(db_url())
    if database is None:
        return raw
    return re.sub(r"/[^/?]+$", f"/{database}", raw)


def _container_url(database: str) -> str:
    """api 容器內的連線字串：它走 compose 網段的 `postgres`，不是代理。"""
    return f"postgresql://zentra:zentra@postgres:5432/{database}"


async def _admin():
    return await asyncpg.connect(_dsn())


@pytest.fixture
async def fresh_database() -> str:
    name = f"zen008_mig_{uuid.uuid4().hex[:12]}"
    admin = await _admin()
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    yield name
    admin = await _admin()
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await admin.close()


def _migrate(database: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "-e", f"DATABASE_URL={_container_url(database)}",
         API_CONTAINER, "python", "-m", "apps.api.migrations"],
        capture_output=True, text=True, timeout=300)


@pytest.fixture(autouse=True)
def _needs_api_container() -> None:
    probe = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", API_CONTAINER],
                           capture_output=True, text=True)
    if probe.stdout.strip() != "true":
        pytest.skip(f"{API_CONTAINER} 沒有在跑，無法用產品映像執行 runner")


def _sql_file(version: str) -> Path:
    matches = sorted(MIGRATIONS.glob(f"{version}_*.sql"))
    assert len(matches) == 1, matches
    return matches[0]


def _apply_through(database: str, last: str) -> None:
    """在空庫上套用 0001…last，並寫進 schema_migrations（給後續 runner 接著跑）。"""
    with psycopg.connect(
        _dsn(database), autocommit=True, row_factory=dict_row,
        cursor_factory=psycopg.ClientCursor,
    ) as conn:
        conn.execute(SCHEMA_MIGRATIONS_DDL)
        for version in [f"{n:04d}" for n in range(1, int(last) + 1)]:
            path = _sql_file(version)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                (version, path.stem.split("_", 1)[1], checksum(path)),
            )


# --------------------------------------------------------------------- 主線


async def test_migrations_apply_to_an_empty_database(fresh_database: str) -> None:
    """乾淨 DB 一路 migrate 到 head。三個已修 bug 的直接回歸點。"""
    result = _migrate(fresh_database)
    assert result.returncode == 0, result.stdout + result.stderr

    connection = await asyncpg.connect(_dsn(fresh_database))
    try:
        versions = {row["version"] for row in await connection.fetch(
            "SELECT version FROM schema_migrations")}
        from apps.api.migrations.__main__ import migration_files

        assert versions == {v for v, _name, _path in migration_files()}, versions
    finally:
        await connection.close()


async def test_head_schema_matches_the_zen008_contract(fresh_database: str) -> None:
    assert _migrate(fresh_database).returncode == 0
    connection = await asyncpg.connect(_dsn(fresh_database))
    try:
        tables = {row["table_name"] for row in await connection.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        assert {"agents", "agent_versions", "runs", "run_events"} <= tables, tables

        columns = {row["column_name"]: row for row in await connection.fetch(
            "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='agent_versions'")}
        required = {"id", "agent_id", "semver", "source_type", "image_ref", "image_digest",
                    "version_id", "image_platform", "image_size_bytes", "runtime_contract",
                    "manifest", "entrypoints", "status", "error", "validation_payload",
                    "build_claimed_at", "builder_task_id", "created_at", "published_at"}
        assert required <= set(columns), required - set(columns)
        for name in ("agent_id", "semver", "source_type", "image_ref",
                     "image_digest", "version_id", "status"):
            assert columns[name]["is_nullable"] == "NO", name

        constraints = {row["conname"]: row["definition"] for row in await connection.fetch(
            "SELECT c.conname, pg_get_constraintdef(c.oid) AS definition "
            "FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
            "WHERE t.relname IN ('agent_versions','runs')")}
        # 0009（ADR-0016）把全域 UNIQUE 換成「一個版號至多一筆 PUBLISHED / BUILDING」的 partial index
        assert "uq_agent_versions_agent_semver" not in constraints, sorted(constraints)
        indexes = {row["indexname"]: row["indexdef"] for row in await connection.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'agent_versions'")}
        assert "WHERE" in indexes["uq_agent_versions_published_semver"]
        assert "PUBLISHED" in indexes["uq_agent_versions_published_semver"]
        assert "BUILDING" in indexes["uq_agent_versions_building_semver"]
        assert "sha256:[0-9a-f]{64}" in constraints["ck_agent_versions_digest"]
        assert "'OCI_IMAGE'" in constraints["ck_agent_versions_source_type"]
        assert "fk_runs_agent" in constraints
        assert "fk_runs_agent_version" in constraints
        assert "'API'" in constraints["ck_runs_trigger"]
        assert "'MCP'" in constraints["ck_runs_trigger"]
        assert "CONSOLE" not in constraints["ck_runs_trigger"], (
            "v0.3.1 已移除 CONSOLE，DB 不該還允許它")
    finally:
        await connection.close()


async def test_builtin_seed_row_is_inserted_with_correct_types(fresh_database: str) -> None:
    """三個已修 bug 之二：uuid 欄位收 `str`、JSON 的 `:true` 被當成綁定參數。

    兩者都只在 seed 真的執行時才會炸，所以這裡必須讀出 seed 的值，
    不能只確認 migration 回傳 0。
    """
    assert _migrate(fresh_database).returncode == 0
    connection = await asyncpg.connect(_dsn(fresh_database))
    try:
        agent = await connection.fetchrow(
            "SELECT id, name FROM agents WHERE id = $1::uuid", BUILTIN_AGENT_ID)
        assert agent is not None, "builtin agent seed 沒有寫進去"
        assert str(agent["id"]) == BUILTIN_AGENT_ID
        assert agent["name"] == "builtin-echo"

        version = await connection.fetchrow(
            "SELECT * FROM agent_versions WHERE id = $1::uuid", BUILTIN_VERSION_ID)
        assert version is not None
        assert str(version["agent_id"]) == BUILTIN_AGENT_ID
        assert version["semver"] == "0.0.0-builtin"
        assert version["status"] == "PUBLISHED"
        assert version["source_type"] == "OCI_IMAGE"
        assert version["runtime_contract"] == "zentra.image/v1"
        assert version["published_at"] is not None

        manifest = version["manifest"]
        if isinstance(manifest, str):
            manifest = json.loads(manifest)
        assert manifest["builtin"] is True, (
            "JSON 字面值裡的 `\"builtin\": true` 被當成綁定參數了", manifest)
        assert manifest["agent_version"] == "0.0.0-builtin"

        entrypoints = version["entrypoints"]
        if isinstance(entrypoints, str):
            entrypoints = json.loads(entrypoints)
        assert [item["name"] for item in entrypoints] == ["default"]
        assert entrypoints[0]["input_schema"]["required"] == ["question"]
    finally:
        await connection.close()


async def test_runner_is_idempotent_on_a_second_apply(fresh_database: str) -> None:
    """runner 沒有 downgrade。第二次套用必須是 no-op，不能重跑 DDL。"""
    assert _migrate(fresh_database).returncode == 0
    second = _migrate(fresh_database)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "套用" not in second.stdout
    assert "已存在 0008" in second.stdout


# -------------------------------------------------- 已知缺口：孤兒 runs


async def _seed_orphan_run(database: str) -> None:
    connection = await asyncpg.connect(_dsn(database))
    try:
        await connection.execute(
            "INSERT INTO runs (id, agent_id, status, trigger, entrypoint, input, "
            "timeout_seconds) VALUES ($1::uuid, $2::uuid, 'SUCCEEDED', 'API', 'default', "
            "'{}'::jsonb, 900)", str(uuid.uuid4()), str(uuid.uuid4()))
    finally:
        await connection.close()


@pytest.mark.xfail(strict=True, reason=(
    "ZEN-008-D4：0004 直接加 FK runs.agent_id → agents.id，"
    "既有 runs 若指向不存在的 agent 就會整個 upgrade 失敗，"
    "migration 沒有先清理或改用 NOT VALID。修好後這條會變 XPASS。"))
async def test_0004_applies_to_a_database_that_already_has_orphan_runs(
    fresh_database: str
) -> None:
    _apply_through(fresh_database, "0003")
    await _seed_orphan_run(fresh_database)
    result = _migrate(fresh_database)
    assert result.returncode == 0, result.stdout + result.stderr


async def test_a_failed_0004_upgrade_leaves_no_partial_schema(fresh_database: str) -> None:
    """上面那個缺口至少必須是**原子失敗**。

    擋住的錯：升級炸在半路、`agents` 表建好了但 FK 沒加，
    營運方以為只是重跑一次就好，實際上資料庫已經處在沒有人設計過的中間狀態。
    """
    _apply_through(fresh_database, "0003")
    await _seed_orphan_run(fresh_database)
    result = _migrate(fresh_database)
    assert result.returncode != 0, "前提改變了：0004 現在吃得下孤兒 runs（請刪掉這條測試）"

    connection = await asyncpg.connect(_dsn(fresh_database))
    try:
        tables = {row["table_name"] for row in await connection.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        assert "agents" not in tables and "agent_versions" not in tables, (
            "0004 失敗後留下了半套 schema", sorted(tables))
        versions = {row["version"] for row in await connection.fetch(
            "SELECT version FROM schema_migrations")}
        assert versions == {"0001", "0002", "0003"}, versions
    finally:
        await connection.close()
