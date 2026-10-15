"""ZEN-020 驗收：純 SQL runner、一次性接續腳本、之後的連線層。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
BUILTIN_AGENT_ID = "01930000-0000-7000-8000-000000000001"


def _admin_dsn() -> str:
    raw = os.environ.get("ZENTRA_TEST_DB_URL")
    if not raw:
        pytest.skip("需要 ZENTRA_TEST_DB_URL")
    return re.sub(
        r"\+asyncpg|\+psycopg2?|\+psycopg",
        "",
        raw,
    )


def psycopg_dsn(database: str) -> str:
    return re.sub(r"/[^/?]+(\?.*)?$", f"/{database}", _admin_dsn())


async def _connect_admin() -> asyncpg.Connection:
    return await asyncpg.connect(_admin_dsn())


@asynccontextmanager
async def temporary_database() -> AsyncIterator[str]:
    name = f"zen020_{uuid.uuid4().hex[:12]}"
    admin = await _connect_admin()
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    try:
        yield name
    finally:
        admin = await _connect_admin()
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
                name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()


@pytest.fixture
async def fresh_database() -> str:
    async with temporary_database() as name:
        yield name


def run_runner(database: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = psycopg_dsn(database)
    env["PYTHONPATH"] = env.get("PYTHONPATH", "/srv")
    return subprocess.run(
        [sys.executable, "-m", "apps.api.migrations"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def docker_available() -> bool:
    try:
        probe = subprocess.run(
            ["docker", "compose", "ps", "-q", "postgres"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return probe.returncode == 0 and bool(probe.stdout.strip())


def require_docker() -> None:
    if not docker_available():
        pytest.skip("需要主機 docker compose（映像內沒有 docker CLI）")
