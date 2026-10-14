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

    ZENTRA_TEST_DB_URL     postgresql+asyncpg://zentra:zentra@postgres:5432/zentra
    ZENTRA_TEST_REDIS_URL  redis://redis:6379/0
    ZENTRA_TEST_BASE_URL   http://app:8000   （只有契約層的測試需要）
"""

from __future__ import annotations

import os
import uuid as uuidlib
from datetime import UTC, datetime

import pytest

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


def _use_nullpool() -> None:
    """每個測試一個 event loop，而 `apps.api.db` 的 engine 是 module 層的。

    連線池會把上一個測試的連線留著，下一個測試拿到它時就會得到
    `Future attached to a different loop` —— 那個錯訊息完全看不出跟連線池有關。
    `NullPool` 讓每次都開新連線：慢一點，但測試之間真的隔離。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from apps.api import db

    if getattr(db, "_zen019_nullpool", False):
        return
    db.engine = create_async_engine(DB_URL, poolclass=NullPool, future=True)
    db.SessionLocal = async_sessionmaker(
        db.engine, expire_on_commit=False,
        class_=__import__("sqlalchemy.ext.asyncio", fromlist=["AsyncSession"]).AsyncSession)
    db._zen019_nullpool = True


#: 刪除順序不能反：`runs.agent_id` 與 `agent_containers.agent_version_id` 的外鍵
#: **沒有 CASCADE**（那是刻意的：正在跑的東西本來就該擋住刪版本）。
#: 這串與 `services/agents.delete_agent()` 的順序一致。
_PURGE = (
    "DELETE FROM egress_calls WHERE agent_id = :id",
    "DELETE FROM agent_egress_rules WHERE agent_id = :id",
    "DELETE FROM agent_email_sender WHERE agent_id = :id",
    "DELETE FROM agent_secrets WHERE agent_id = :id",
    "DELETE FROM agent_settings WHERE agent_id = :id",
    "DELETE FROM llm_calls WHERE run_id IN (SELECT id FROM runs WHERE agent_id = :id)",
    "DELETE FROM run_events WHERE run_id IN (SELECT id FROM runs WHERE agent_id = :id)",
    "DELETE FROM runs WHERE agent_id = :id",
    "DELETE FROM agent_containers WHERE agent_id = :id",
    "DELETE FROM agent_versions WHERE agent_id = :id",
    "DELETE FROM agents WHERE id = :id",
)


async def purge_agent(session, agent_id) -> None:
    """**測完自己收。** 留著的話下一個人沒辦法用 Console 看東西
    （實測累積到過 894 個 Agent）。"""
    from sqlalchemy import text

    await session.rollback()
    for statement in _PURGE:
        await session.execute(text(statement), {"id": agent_id})
    await session.commit()


@pytest.fixture
async def app_client():
    """in-process 的 FastAPI client。

    **不跑 lifespan**（那裡只做一件事：開 Redis 連線），直接把 redis 放進
    `app.state` —— 跑 lifespan 會需要 ASGI lifespan 的支援套件，
    而我們只要那一個屬性。
    """
    import httpx
    from redis.asyncio import Redis

    _use_nullpool()
    from apps.api.main import app

    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    app.state.redis = redis
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        yield client
    await redis.aclose()


@pytest.fixture
async def session():
    _use_nullpool()
    from apps.api import db

    async with db.SessionLocal() as value:
        yield value


@pytest.fixture
async def redis_client():
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


def new_id() -> uuidlib.UUID:
    from apps.api.ids import uuid7

    return uuid7()


@pytest.fixture
async def agent_row(session):
    """一個乾淨的 Agent。**測完自己刪掉** —— 留著的話下一個人沒辦法用 Console
    看東西（實測累積到過 894 個）。"""
    from apps.api.models import Agent

    row = Agent(id=new_id(), name=f"zen019-{os.urandom(5).hex()}",
                description="ZEN-019 test")
    session.add(row)
    await session.commit()
    yield row
    await purge_agent(session, row.id)


async def make_published_version(session, agent_id, semver="1.0.0"):
    from apps.api.enums import AgentVersionStatus
    from apps.api.models import AgentVersion

    row = AgentVersion(
        id=new_id(), agent_id=agent_id, semver=semver,
        image_ref=f"localhost:6000/zen019@sha256:{'a' * 64}",
        image_digest=f"sha256:{'a' * 64}", version_id="a" * 12,
        manifest={"apiVersion": "zentra/v1", "name": "zen019", "version": semver},
        entrypoints=[{"name": "default", "description": "d",
                      "input_schema": {"type": "object"},
                      "output_schema": {"type": "object"}}],
        status=AgentVersionStatus.PUBLISHED, published_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    return row


async def make_run(session, agent_id, version_id, status="RUNNING"):
    from apps.api.models import Run

    row = Run(id=new_id(), agent_id=agent_id, agent_version_id=version_id,
              status=status, trigger="API", entrypoint="default", input={},
              timeout_seconds=900)
    session.add(row)
    await session.commit()
    return row


async def issue_run_token(redis, run_id) -> str:
    from apps.api.services import runtoken

    return await runtoken.issue(redis, run_id, 900)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
