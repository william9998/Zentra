"""Opt-in Postgres/Redis and Gateway contract tests for ZEN-000.

Requires migrated test services. Example:
ZENTRA_TEST_DB_URL=postgresql://zentra:zentra@127.0.0.1:5432/zentra \
ZENTRA_TEST_REDIS_URL=redis://127.0.0.1:6379/0 \
ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 \
python -m pytest tests/zen000/test_gateway_db_integration.py
"""

import os
from datetime import UTC, datetime
from uuid import UUID

# ingest_batch / finish 走 app 的 asyncpg pool；必須在 get_settings() 快取前對齊 DSN。
_TEST_DB_URL = os.getenv("ZENTRA_TEST_DB_URL")
if _TEST_DB_URL:
    os.environ.setdefault("DATABASE_URL", _TEST_DB_URL)

import httpx
import pytest
from redis.asyncio import Redis

from apps.api import db
from apps.api.config import normalize_database_url
from apps.api.enums import (
    BUILTIN_AGENT_ID,
    BUILTIN_AGENT_VERSION_ID,
    RunStatus,
    RunTrigger,
)
from apps.api.ids import uuid7
from apps.api.schemas import SandboxEvent
from apps.api.services import events, runtoken
from apps.worker.tasks.run import finish


def _db_url() -> str:
    value = os.getenv("ZENTRA_TEST_DB_URL")
    if not value:
        pytest.skip("Set ZENTRA_TEST_DB_URL to run migrated Postgres tests")
    return normalize_database_url(value)


async def _pg():
    import json

    import asyncpg

    conn = await asyncpg.connect(_db_url())
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(
            typ, encoder=json.dumps, decoder=json.loads,
            schema="pg_catalog", format="text")
    return conn


async def _insert_run(run_id: UUID, *, output=None, result_received_at=None) -> None:
    conn = await _pg()
    try:
        await conn.execute(
            "INSERT INTO runs (id, agent_id, agent_version_id, status, trigger, "
            "entrypoint, input, timeout_seconds, started_at, output, result_received_at) "
            "VALUES ($1, $2, $3, $4, $5, 'default', $6, 90, $7, $8, $9)",
            run_id, UUID(BUILTIN_AGENT_ID), UUID(BUILTIN_AGENT_VERSION_ID),
            RunStatus.RUNNING, RunTrigger.API, {"question": "hello"},
            datetime.now(UTC), output, result_received_at,
        )
    finally:
        await conn.close()


async def _delete_runs(*run_ids: UUID) -> None:
    conn = await _pg()
    try:
        await conn.execute("DELETE FROM runs WHERE id = ANY($1::uuid[])", list(run_ids))
    finally:
        await conn.close()


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


@pytest.mark.asyncio
async def test_gateway_requires_run_scoped_token_and_accepts_array_events() -> None:
    redis = Redis.from_url(_redis_url(), decode_responses=True)
    run_a, run_b = uuid7(), uuid7()
    token = None
    try:
        await _insert_run(run_a)
        await _insert_run(run_b)
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
            # `default_model` 是 v0.9.0 加的：平台算好送進沙盒，SDK 不准自己補寫死值。
            # `config` / `secret_names` 是 v0.12.0 加的（ZEN-019 §4.1）——
            # **頂層欄位，不得合併進 manifest**：manifest 是版本的不可變內容，
            # 把可變設定攪進去會讓 P5 在 runtime 這一側失去實體。
            assert set(bootstrap.json()) == {
                "run_id", "entrypoint", "input", "manifest", "default_model",
                "config", "secret_names"}
            assert "config" not in bootstrap.json()["manifest"]
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

        conn = await _pg()
        try:
            assert await conn.fetchval(
                "SELECT count(*) FROM run_events WHERE run_id = $1 AND event_id = $2",
                run_a, UUID(event_id)) == 1
            assert (await conn.fetchval("SELECT output FROM runs WHERE id = $1", run_b)) is None
        finally:
            await conn.close()
    finally:
        if token is not None:
            await runtoken.revoke(redis, token)
        await _delete_runs(run_a, run_b)
        await redis.aclose()


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
    run_id, event_id = uuid7(), uuid7()
    notification = XaddFailsOnce()
    event = SandboxEvent(event_id=event_id, type="custom", payload={"name": "qa", "payload": {"n": 1}})
    try:
        await db.init_pool()
        await _insert_run(run_id)
        with pytest.raises(RuntimeError, match="injected Redis XADD outage"):
            await events.ingest_batch(notification, run_id, [event])
        conn = await _pg()
        try:
            persisted = await conn.fetchrow(
                "SELECT seq, published_at FROM run_events WHERE run_id = $1 AND event_id = $2",
                run_id, event_id)
            original_seq = persisted["seq"]
            assert persisted["published_at"] is None
        finally:
            await conn.close()
        replay = await events.ingest_batch(notification, run_id, [event])
        assert replay == {"accepted": 0, "last_seq": original_seq}
        assert len(notification.published) == 1
        assert int(notification.published[0][1]["seq"]) == original_seq
        conn = await _pg()
        try:
            assert await conn.fetchval(
                "SELECT count(*) FROM run_events WHERE run_id = $1 AND event_id = $2",
                run_id, event_id) == 1
        finally:
            await conn.close()
    finally:
        await _delete_runs(run_id)
        await db.close_pool()


@pytest.mark.asyncio
async def test_worker_alone_creates_one_terminal_event_and_redelivery_is_idempotent() -> None:
    run_id = uuid7()
    try:
        await db.init_pool()
        await _insert_run(run_id, output={"echo": "hello"}, result_received_at=datetime.now(UTC))
        await finish(run_id, 0, None)
        conn = await _pg()
        try:
            completed = await conn.fetchrow(
                "SELECT status, output FROM runs WHERE id = $1", run_id)
            assert completed["status"] == RunStatus.SUCCEEDED
            assert completed["output"] == {"echo": "hello"}
            terminal_count = await conn.fetchval(
                "SELECT count(*) FROM run_events WHERE run_id = $1 AND terminal", run_id)
            assert terminal_count == 1
        finally:
            await conn.close()
        await finish(run_id, 0, None)
        conn = await _pg()
        try:
            assert await conn.fetchval(
                "SELECT count(*) FROM run_events WHERE run_id = $1 AND terminal", run_id) == 1
        finally:
            await conn.close()
    finally:
        await _delete_runs(run_id)
        await db.close_pool()
