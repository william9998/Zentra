"""ZEN-019 的共用 fixture。

## 為什麼是 in-process ASGI，不是對 live server 打 HTTP

出口 proxy 的兩個關鍵性質**只有在同一個行程裡才驗得到**：

  · 「解析出來的 IP 就是實際連過去的 IP」（DNS rebinding，`Z19-29`）
  · 「host 沒命中白名單時，resolver 根本沒被呼叫」（`Z19-30`）

兩者都要替換 `egress.resolve_addresses` 與 `egress.build_transport`。
那兩個縫**不改變任何檢查**（見 `apps/api/services/egress.py` 的模組 docstring）——
它們只決定「名字怎麼變成 IP」與「位元組從哪個 socket 出去」。
`apps/**` 裡沒有任何可以關掉檢查的旗標，那是 `test_no_bypass_guardrail.py` 在守的。

所以這裡用真 PostgreSQL + 真 Redis + in-process FastAPI app。
DB 與 Redis 缺一不可，缺了就 skip（而 **skip 不算通過**）。

環境變數（沿用既有慣例）：

    ZENTRA_TEST_DB_URL     postgresql://zentra:zentra@postgres:5432/zentra
    ZENTRA_TEST_REDIS_URL  redis://redis:6379/0
    ZENTRA_TEST_BASE_URL   http://app:8000   （只有契約層的測試需要）
"""

from __future__ import annotations

import os
import uuid as uuidlib
from datetime import UTC, datetime

import pytest

from apps.api import rows

DB_URL = os.environ.get("ZENTRA_TEST_DB_URL")
REDIS_URL = os.environ.get("ZENTRA_TEST_REDIS_URL", "redis://redis:6379/0")
BASE_URL = os.environ.get("ZENTRA_TEST_BASE_URL")

requires_db = pytest.mark.skipif(not DB_URL, reason="需要 ZENTRA_TEST_DB_URL")
requires_base_url = pytest.mark.skipif(not BASE_URL, reason="需要 ZENTRA_TEST_BASE_URL")

# get_settings() 有 lru_cache，所以連線字串要在 import 之前放進環境變數。
if DB_URL:
    os.environ.setdefault("DATABASE_URL", DB_URL)
    os.environ.setdefault("REDIS_URL", REDIS_URL)
    # 沒有加密金鑰就存不了機密，而這一整組測試都繞著機密轉。
    os.environ.setdefault(
        "ZENTRA_SECRET_KEY", "Zq2sV4jK8mN0pR3tW6yA9cE1gH5kL7nQ0sU2wX4zB6c=")


# `pyproject.toml` 設了 `asyncio_mode = "auto"`，所以**不要**再掛
# `pytest.mark.anyio` —— 兩個 plugin 會各自開一個 runner，
# 症狀是 teardown 時 `Event loop is closed`，而測試本體明明過了。


#: 刪除順序不能反：`runs.agent_id` 與 `agent_containers.agent_version_id` 的外鍵
#: **沒有 CASCADE**（那是刻意的：正在跑的東西本來就該擋住刪版本）。
#: 這串與 `services/agents.delete_agent()` 的順序一致。
_PURGE = (
    "DELETE FROM egress_calls WHERE agent_id = $1",
    "DELETE FROM agent_egress_rules WHERE agent_id = $1",
    "DELETE FROM agent_email_sender WHERE agent_id = $1",
    "DELETE FROM agent_secrets WHERE agent_id = $1",
    "DELETE FROM agent_settings WHERE agent_id = $1",
    "DELETE FROM llm_calls WHERE run_id IN (SELECT id FROM runs WHERE agent_id = $1)",
    "DELETE FROM run_events WHERE run_id IN (SELECT id FROM runs WHERE agent_id = $1)",
    "DELETE FROM runs WHERE agent_id = $1",
    "DELETE FROM agent_containers WHERE agent_id = $1",
    "DELETE FROM agent_versions WHERE agent_id = $1",
    "DELETE FROM agents WHERE id = $1",
)


async def purge_agent(agent_id) -> None:
    """**測完自己收。** 留著的話下一個人沒辦法用 Console 看東西
    （實測累積到過 894 個 Agent）。"""
    from apps.api import db

    async with db.transaction() as conn:
        for statement in _PURGE:
            await conn.execute(statement, agent_id)


@pytest.fixture(autouse=True)
async def _asyncpg_pool():
    """in-process 測試不跑 lifespan，但葉節點 service 已走 asyncpg pool。"""
    if not DB_URL:
        yield
        return
    from apps.api import db

    await db.init_pool()
    try:
        yield
    finally:
        await db.close_pool()


@pytest.fixture
async def app_client():
    """in-process 的 FastAPI client。

    **不跑 lifespan**（那裡開 Redis 與 asyncpg pool），直接把 redis 放進
    `app.state`；asyncpg pool 由 `_asyncpg_pool` 這支 autouse fixture 建。
    跑 lifespan 會需要 ASGI lifespan 的支援套件，而我們只要那一個屬性。
    """
    import httpx
    from redis.asyncio import Redis

    from apps.api.main import app

    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    app.state.redis = redis
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        yield client
    await redis.aclose()


@pytest.fixture
async def redis_client():
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


def new_id() -> uuidlib.UUID:
    from apps.api.ids import uuid7

    return uuid7()


async def insert_agent(name: str | None = None, description: str = "ZEN-019 test") -> rows.Agent:
    from apps.api import db

    agent_id = new_id()
    async with db.transaction() as conn:
        rec = await conn.fetchrow(
            f"INSERT INTO agents (id, name, description) VALUES ($1, $2, $3) "
            f"RETURNING {rows.Agent.returning()}",
            agent_id, name or f"zen019-{os.urandom(5).hex()}", description,
        )
    return rows.Agent.from_row(rec)


@pytest.fixture
async def agent_row():
    """一個乾淨的 Agent。**測完自己刪掉** —— 留著的話下一個人沒辦法用 Console
    看東西（實測累積到過 894 個）。"""
    row = await insert_agent()
    yield row
    await purge_agent(row.id)


async def make_published_version(agent_id, semver="1.0.0") -> rows.AgentVersion:
    from apps.api import db

    now = datetime.now(UTC)
    async with db.transaction() as conn:
        rec = await conn.fetchrow(
            f"""INSERT INTO agent_versions (
                    id, agent_id, semver, source_type, image_ref, image_digest,
                    version_id, manifest, entrypoints, status, published_at
                ) VALUES ($1, $2, $3, 'OCI_IMAGE', $4, $5, $6, $7, $8, 'PUBLISHED', $9)
                RETURNING {rows.AgentVersion.returning()}""",
            new_id(), agent_id, semver,
            f"localhost:6000/zen019@sha256:{'a' * 64}",
            f"sha256:{'a' * 64}", "a" * 12,
            {"apiVersion": "zentra/v1", "name": "zen019", "version": semver},
            [{"name": "default", "description": "d",
              "input_schema": {"type": "object"},
              "output_schema": {"type": "object"}}],
            now,
        )
    return rows.AgentVersion.from_row(rec)


async def make_run(agent_id, version_id, status="RUNNING") -> rows.Run:
    from apps.api import db

    async with db.transaction() as conn:
        rec = await conn.fetchrow(
            f"""INSERT INTO runs (
                    id, agent_id, agent_version_id, status, trigger,
                    entrypoint, input, timeout_seconds
                ) VALUES ($1, $2, $3, $4, 'API', 'default', $5, 900)
                RETURNING {rows.Run.returning()}""",
            new_id(), agent_id, version_id, status, {},
        )
    return rows.Run.from_row(rec)


async def insert_egress_rule(agent_id, **rule) -> rows.AgentEgressRule:
    from apps.api import db

    now = datetime.now(UTC)
    async with db.transaction() as conn:
        rec = await conn.fetchrow(
            f"""INSERT INTO agent_egress_rules (
                    rule_id, agent_id, host, methods, path_prefix,
                    auth_secret_name, auth_header, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
                RETURNING {rows.AgentEgressRule.returning()}""",
            new_id(), agent_id,
            rule.get("host", "api.example-exchange.com"),
            rule.get("methods", ["GET"]),
            rule.get("path_prefix"),
            rule.get("auth_secret_name"),
            rule.get("auth_header"),
            now,
        )
    return rows.AgentEgressRule.from_row(rec)


async def insert_email_sender(
    agent_id, *, host: str, port: int, recipients: list[str],
) -> rows.AgentEmailSender:
    from apps.api import db

    now = datetime.now(UTC)
    async with db.transaction() as conn:
        rec = await conn.fetchrow(
            f"""INSERT INTO agent_email_sender (
                    agent_id, smtp_host, smtp_port, username, from_address,
                    password_secret_name, allowed_recipients, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $4, 'SMTP_PASSWORD', $5, $6, $6)
                RETURNING {rows.AgentEmailSender.returning()}""",
            agent_id, host, port, "me@example.com", list(recipients), now,
        )
    return rows.AgentEmailSender.from_row(rec)


async def insert_container(
    agent_id, version_id, *, container_id="fake-container", status="READY",
) -> rows.AgentContainer:
    from apps.api import db

    async with db.transaction() as conn:
        rec = await conn.fetchrow(
            f"""INSERT INTO agent_containers (
                    id, agent_id, agent_version_id, container_id, status,
                    inflight_runs, started_at
                ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                RETURNING {rows.AgentContainer.returning()}""",
            new_id(), agent_id, version_id, container_id, status, datetime.now(UTC),
        )
    return rows.AgentContainer.from_row(rec)


async def insert_egress_call(agent_id, run_id, **fields) -> rows.EgressCall:
    from apps.api import db

    async with db.transaction() as conn:
        rec = await conn.fetchrow(
            f"""INSERT INTO egress_calls (
                    call_id, run_id, agent_id, kind, rule_id, method, host, path,
                    status_code, request_bytes, response_bytes, latency_ms,
                    error, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                RETURNING {rows.EgressCall.returning()}""",
            fields.get("call_id", new_id()), run_id, agent_id,
            fields.get("kind", "HTTP"),
            fields.get("rule_id"),
            fields.get("method"),
            fields.get("host", "127.0.0.1"),
            fields.get("path", "/"),
            fields.get("status_code"),
            fields.get("request_bytes", 1),
            fields.get("response_bytes", 0),
            fields.get("latency_ms"),
            fields.get("error"),
            fields.get("created_at", datetime.now(UTC)),
        )
    return rows.EgressCall.from_row(rec)


async def fetch_egress_calls(run_id) -> list[rows.EgressCall]:
    from apps.api import db

    async with db.connection() as conn:
        recs = await conn.fetch(
            f"SELECT {rows.EgressCall.returning()} FROM egress_calls "
            "WHERE run_id = $1 ORDER BY created_at",
            run_id,
        )
    return [rows.EgressCall.from_row(rec) for rec in recs]


async def table_count(table: str, agent_id) -> int:
    from apps.api import db

    async with db.connection() as conn:
        return await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE agent_id = $1", agent_id)


async def issue_run_token(redis, run_id) -> str:
    from apps.api.services import runtoken

    return await runtoken.issue(redis, run_id, 900)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
