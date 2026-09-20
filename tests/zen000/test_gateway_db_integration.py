"""Opt-in Postgres/Redis and Gateway contract tests for ZEN-000.

Requires migrated test services. Example:
ZENTRA_TEST_DB_URL=postgresql+asyncpg://zentra:zentra@127.0.0.1:5432/zentra \
ZENTRA_TEST_REDIS_URL=redis://127.0.0.1:6379/0 \
ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 \
python -m pytest tests/zen000/test_gateway_db_integration.py
"""

import os
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.enums import BUILTIN_AGENT_ID, RunStatus, RunTrigger
from apps.api.ids import uuid7
from apps.api.models import Run, RunEvent
from apps.api.schemas import SandboxEvent
from apps.api.services import events, runtoken
from apps.worker.tasks.run import finish


def _db_url() -> str:
    value = os.getenv("ZENTRA_TEST_DB_URL")
    if not value:
        pytest.skip("Set ZENTRA_TEST_DB_URL to run migrated Postgres tests")
    return value


def _redis_url() -> str:
    value = os.getenv("ZENTRA_TEST_REDIS_URL")
    if not value:
        pytest.skip("Set ZENTRA_TEST_REDIS_URL to run Gateway token tests")
    return value


def _base_url() -> str:
    value = os.getenv("ZENTRA_TEST_BASE_URL")
    if not value:
        pytest.skip("Set ZENTRA_TEST_BASE_URL to run live Gateway tests")
    return value


def _run(run_id: UUID) -> Run:
    return Run(
        id=run_id, agent_id=UUID(BUILTIN_AGENT_ID), status=RunStatus.RUNNING,
        trigger=RunTrigger.API, entrypoint="default", input={"question": "hello"},
        timeout_seconds=90, started_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_gateway_requires_run_scoped_token_and_accepts_array_events() -> None:
    engine = create_async_engine(_db_url())
    redis = Redis.from_url(_redis_url(), decode_responses=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run_a, run_b = uuid7(), uuid7()
    token = None
    try:
        async with sessions() as session:
            session.add_all([_run(run_a), _run(run_b)])
            await session.commit()
        token = await runtoken.issue(redis, run_a, 90)
        assert 0 < await redis.ttl(f"run_token:{token}") <= 90
        headers = {"Authorization": f"Bearer {token}"}
        event_id = str(uuid7())
        body = [{"event_id": event_id, "type": "custom", "payload": {"name": "qa", "payload": {"n": 1}}}]

        async with httpx.AsyncClient(base_url=_base_url(), timeout=10) as client:
            paths = [
                ("GET", f"/internal/runs/{run_a}/bootstrap", None),
                ("POST", f"/internal/runs/{run_a}/events", body),
                ("POST", f"/internal/runs/{run_a}/result", {"output": {"echo": "hello"}, "error": None, "usage": {}}),
            ]
            for method, path, payload in paths:
                missing = await client.request(method, path, json=payload)
                assert missing.status_code == 401, missing.text
                assert missing.json()["error"]["code"] == "UNAUTHENTICATED"
                crossed = await client.request(method, path.replace(str(run_a), str(run_b)), json=payload, headers=headers)
                assert crossed.status_code == 403, crossed.text
                assert crossed.json()["error"]["code"] == "PERMISSION_DENIED"

            bootstrap = await client.get(f"/internal/runs/{run_a}/bootstrap", headers=headers)
            assert bootstrap.status_code == 200, bootstrap.text
            assert set(bootstrap.json()) == {"run_id", "entrypoint", "input", "manifest"}
            assert bootstrap.json()["input"] == {"question": "hello"}

            event_url = f"/internal/runs/{run_a}/events"
            first = await client.post(event_url, json=body, headers=headers)
            assert first.status_code == 202, first.text
            assert first.json()["accepted"] == 1
            seq = first.json()["last_seq"]
            replay = await client.post(event_url, json=body, headers=headers)
            assert replay.status_code == 202, replay.text
            assert replay.json() == {"accepted": 0, "last_seq": seq}
            wrapped = await client.post(event_url, json={"events": body}, headers=headers)
            assert wrapped.status_code == 422
            reserved = await client.post(event_url, json=[{
                "event_id": str(uuid7()), "type": "run.status", "payload": {"status": "SUCCEEDED"},
            }], headers=headers)
            assert reserved.status_code == 422

            result = await client.post(
                f"/internal/runs/{run_a}/result",
                json={"output": {"echo": "hello"}, "error": None, "usage": {}},
                headers=headers,
            )
            assert result.status_code == 202, result.text
            assert result.json() == {"accepted": True}
            public = await client.get(f"/v1/runs/{run_a}")
            assert public.status_code == 200
            assert public.json()["status"] == "RUNNING"  # /result cannot finish a Run.
            await runtoken.revoke(redis, token)
            revoked = await client.get(f"/internal/runs/{run_a}/bootstrap", headers=headers)
            assert revoked.status_code == 401

        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(RunEvent).where(
                RunEvent.run_id == run_a, RunEvent.event_id == UUID(event_id),
            )) == 1
            assert (await session.get(Run, run_b)).output is None
    finally:
        if token is not None:
            await runtoken.revoke(redis, token)
        async with sessions() as session:
            await session.execute(delete(Run).where(Run.id.in_([run_a, run_b])))
            await session.commit()
        await redis.aclose()
        await engine.dispose()


class XaddFailsOnce:
    def __init__(self) -> None:
        self.calls = 0
        self.published = []

    async def xadd(self, key, fields):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("injected Redis XADD outage")
        self.published.append((key, fields))
        return "1-0"

    async def expire(self, key, ttl):
        return True


@pytest.mark.asyncio
async def test_event_db_commit_survives_redis_failure_and_replay_reuses_seq() -> None:
    engine = create_async_engine(_db_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run_id, event_id = uuid7(), uuid7()
    notification = XaddFailsOnce()
    event = SandboxEvent(event_id=event_id, type="custom", payload={"name": "qa", "payload": {"n": 1}})
    try:
        async with sessions() as session:
            session.add(_run(run_id))
            await session.commit()
            with pytest.raises(RuntimeError, match="injected Redis XADD outage"):
                await events.ingest_batch(session, notification, run_id, [event])
        async with sessions() as session:
            persisted = (await session.execute(select(RunEvent).where(
                RunEvent.run_id == run_id, RunEvent.event_id == event_id,
            ))).scalar_one()
            original_seq = persisted.seq
            assert persisted.published_at is None
            replay = await events.ingest_batch(session, notification, run_id, [event])
            assert replay == {"accepted": 0, "last_seq": original_seq}
            assert len(notification.published) == 1
            assert int(notification.published[0][1]["seq"]) == original_seq
            assert await session.scalar(select(func.count()).select_from(RunEvent).where(
                RunEvent.run_id == run_id, RunEvent.event_id == event_id,
            )) == 1
    finally:
        async with sessions() as session:
            await session.execute(delete(Run).where(Run.id == run_id))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_alone_creates_one_terminal_event_and_redelivery_is_idempotent() -> None:
    engine = create_async_engine(_db_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run_id = uuid7()
    try:
        async with sessions() as session:
            run = _run(run_id)
            run.output = {"echo": "hello"}
            run.result_received_at = datetime.now(UTC)
            session.add(run)
            await session.commit()
            await finish(session, run_id, 0, None)
            completed = await session.get(Run, run_id)
            assert completed.status == RunStatus.SUCCEEDED
            assert completed.output == {"echo": "hello"}
            terminal_count = await session.scalar(select(func.count()).select_from(RunEvent).where(
                RunEvent.run_id == run_id, RunEvent.terminal.is_(True),
            ))
            assert terminal_count == 1
            await finish(session, run_id, 0, None)
            assert await session.scalar(select(func.count()).select_from(RunEvent).where(
                RunEvent.run_id == run_id, RunEvent.terminal.is_(True),
            )) == 1
    finally:
        async with sessions() as session:
            await session.execute(delete(Run).where(Run.id == run_id))
            await session.commit()
        await engine.dispose()
