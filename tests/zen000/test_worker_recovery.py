"""Focused ZEN-000 redelivery, pending-event, and token TTL regressions."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Lock
from uuid import UUID

_TEST_DB_URL = os.getenv("ZENTRA_TEST_DB_URL")
if _TEST_DB_URL:
    os.environ.setdefault("DATABASE_URL", _TEST_DB_URL)

import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.config import normalize_database_url
from apps.api.enums import BUILTIN_AGENT_ID, EventType, RunStatus, RunTrigger
from apps.api.ids import uuid7
from apps.api.services import runtoken
from apps.worker.events import publish_pending
from apps.worker.tasks.run import _claim_run, _finish_sync


def _dsn() -> str:
    url = os.getenv("ZENTRA_TEST_DB_URL")
    if not url:
        pytest.skip("Set ZENTRA_TEST_DB_URL to run PostgreSQL concurrency tests")
    return normalize_database_url(url)


def _psycopg_conn():
    return psycopg.connect(_dsn(), autocommit=True, row_factory=dict_row)


def _insert_run(conn, run_id: UUID, status: RunStatus, **extra) -> None:
    conn.execute(
        "INSERT INTO runs (id, agent_id, status, trigger, entrypoint, input, "
        "timeout_seconds, started_at, result_received_at, error) "
        "VALUES (%(id)s, %(agent_id)s, %(status)s, %(trigger)s, 'default', "
        "%(input)s, 90, %(started_at)s, %(result_received_at)s, %(error)s)",
        {
            "id": run_id,
            "agent_id": UUID(BUILTIN_AGENT_ID),
            "status": status,
            "trigger": RunTrigger.API,
            "input": Jsonb({"question": "qa"}),
            "started_at": datetime.now(UTC),
            "result_received_at": extra.get("result_received_at"),
            "error": Jsonb(extra["error"]) if extra.get("error") is not None else None,
        },
    )


def _delete_run(conn, run_id: UUID) -> None:
    conn.execute("DELETE FROM runs WHERE id = %(id)s", {"id": run_id})


class RecordingRedis:
    def __init__(self) -> None:
        self.lock = Lock()
        self.published = []

    def xadd(self, key, fields):
        with self.lock:
            self.published.append((key, dict(fields)))
        return "1-0"

    def expire(self, key, ttl):
        return True


def test_two_deliveries_claim_only_once_under_row_lock() -> None:
    conn = _psycopg_conn()
    run_id = uuid7()
    redis = RecordingRedis()
    barrier = Barrier(2)
    try:
        _insert_run(conn, run_id, RunStatus.QUEUED)

        def delivery(task_id: str) -> bool:
            owned = _psycopg_conn()
            try:
                barrier.wait(timeout=10)
                with owned.transaction():
                    _, claimed = _claim_run(owned, run_id, task_id)
                return claimed
            finally:
                owned.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(delivery, f"delivery-{i}") for i in range(2)]
            assert sorted(f.result(timeout=15) for f in futures) == [False, True]
        publish_pending(redis, run_id)

        run = conn.execute(
            "SELECT status, celery_task_id FROM runs WHERE id = %(id)s",
            {"id": run_id},
        ).fetchone()
        assert run["status"] == RunStatus.SCHEDULING
        assert run["celery_task_id"] in {"delivery-0", "delivery-1"}
        found = conn.execute(
            "SELECT payload FROM run_events WHERE run_id = %(id)s",
            {"id": run_id},
        ).fetchall()
        assert len(found) == 1
        assert found[0]["payload"] == {"status": "SCHEDULING"}
    finally:
        _delete_run(conn, run_id)
        conn.close()


def test_terminal_redelivery_republishes_original_pending_event() -> None:
    conn = _psycopg_conn()
    run_id, event_id = uuid7(), uuid7()
    redis = RecordingRedis()
    try:
        _insert_run(conn, run_id, RunStatus.SUCCEEDED)
        conn.execute(
            "INSERT INTO run_events (run_id, event_id, type, payload, terminal, published_at) "
            "VALUES (%(run_id)s, %(event_id)s, %(type)s, %(payload)s, TRUE, NULL)",
            {
                "run_id": run_id, "event_id": event_id, "type": EventType.RUN_STATUS,
                "payload": Jsonb({"status": "SUCCEEDED"}),
            },
        )
        original = conn.execute(
            "SELECT seq FROM run_events WHERE run_id = %(id)s",
            {"id": run_id},
        ).fetchone()
        original_seq = original["seq"]

        owned = _psycopg_conn()
        try:
            with owned.transaction():
                _, claimed = _claim_run(owned, run_id, "redelivery")
            publish_pending(redis, run_id)
            assert claimed is False
        finally:
            owned.close()
        assert conn.execute(
            "SELECT status FROM runs WHERE id = %(id)s", {"id": run_id},
        ).fetchone()["status"] == RunStatus.SUCCEEDED

        found = conn.execute(
            "SELECT event_id, seq, published_at FROM run_events WHERE run_id = %(id)s",
            {"id": run_id},
        ).fetchall()
        assert len(found) == 1
        assert found[0]["event_id"] == event_id
        assert found[0]["seq"] == original_seq
        assert found[0]["published_at"] is not None
        assert redis.published == [(f"run:{run_id}", {
            "seq": str(original_seq), "type": "run.status",
            "data": '{"status": "SUCCEEDED"}',
        })]
    finally:
        _delete_run(conn, run_id)
        conn.close()


@pytest.mark.parametrize(
    "exit_code,timed_out,has_result,prior_error,expected_code",
    [
        (2, False, False, None, "SANDBOX_ERROR"),
        (0, False, False, None, "SANDBOX_ERROR"),
        (0, True, False, None, "SANDBOX_TIMEOUT"),
        (0, False, True, {"code": "USER_CODE_ERROR", "message": "bad graph"}, "USER_CODE_ERROR"),
    ],
)
def test_worker_failure_is_terminal_once(
    exit_code: int,
    timed_out: bool,
    has_result: bool,
    prior_error: dict | None,
    expected_code: str,
) -> None:
    conn = _psycopg_conn()
    run_id = uuid7()
    redis = RecordingRedis()
    try:
        _insert_run(
            conn, run_id, RunStatus.RUNNING,
            result_received_at=datetime.now(UTC) if has_result else None,
            error=prior_error,
        )

        owned = _psycopg_conn()
        try:
            with owned.transaction():
                first = _finish_sync(owned, run_id, exit_code, "sandbox log", timed_out=timed_out)
            publish_pending(redis, run_id)
            with owned.transaction():
                second = _finish_sync(owned, run_id, exit_code, "sandbox log", timed_out=timed_out)
            publish_pending(redis, run_id)
            assert first == {"run_id": str(run_id), "status": "FAILED"}
            assert second["idempotent_skip"] is True
        finally:
            owned.close()

        run = conn.execute(
            "SELECT status, error, finished_at FROM runs WHERE id = %(id)s",
            {"id": run_id},
        ).fetchone()
        assert run["status"] == RunStatus.FAILED
        assert run["error"]["code"] == expected_code
        assert run["finished_at"] is not None
        events = conn.execute(
            "SELECT terminal, payload FROM run_events WHERE run_id = %(id)s",
            {"id": run_id},
        ).fetchall()
        assert len(events) == 1
        assert events[0]["terminal"] is True
        assert events[0]["payload"] == {"status": "FAILED"}
        assert len(redis.published) == 1
    finally:
        _delete_run(conn, run_id)
        conn.close()


class RecordingPipeline:
    def __init__(self) -> None:
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def set(self, *args, **kwargs):
        self.calls.append(("set", args, kwargs))

    def sadd(self, *args):
        self.calls.append(("sadd", args, {}))

    def expire(self, *args):
        self.calls.append(("expire", args, {}))

    def execute(self):
        return []


class PipelineRedis:
    def __init__(self) -> None:
        self.pipe = RecordingPipeline()

    def pipeline(self, transaction=True):
        assert transaction is True
        return self.pipe


def test_run_token_issue_uses_exact_timeout_without_buffer() -> None:
    run_id = uuid7()
    redis = PipelineRedis()
    token = runtoken.issue_sync(redis, run_id, 90)
    assert len(token) >= 32
    assert ("set", (runtoken.key_for(token), str(run_id)), {"ex": 90}) in redis.pipe.calls
    assert ("expire", (runtoken.run_key(run_id), 90), {}) in redis.pipe.calls
