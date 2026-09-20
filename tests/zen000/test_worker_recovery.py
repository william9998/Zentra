"""Focused ZEN-000 redelivery, pending-event, and token TTL regressions."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Lock
from uuid import UUID

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from apps.api.enums import BUILTIN_AGENT_ID, EventType, RunStatus, RunTrigger
from apps.api.ids import uuid7
from apps.api.models import Run, RunEvent
from apps.api.services import runtoken
from apps.worker.tasks.run import _claim_run, _finish_sync


def _sync_url() -> str:
    url = os.getenv("ZENTRA_TEST_DB_URL")
    if not url:
        pytest.skip("Set ZENTRA_TEST_DB_URL to run PostgreSQL concurrency tests")
    return url.replace("+asyncpg", "+psycopg")


def _run(run_id: UUID, status: RunStatus) -> Run:
    return Run(
        id=run_id, agent_id=UUID(BUILTIN_AGENT_ID), status=status,
        trigger=RunTrigger.API, entrypoint="default", input={"question": "qa"},
        timeout_seconds=90, started_at=datetime.now(UTC),
    )


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
    engine = create_engine(_sync_url())
    sessions = sessionmaker(engine, expire_on_commit=False)
    run_id = uuid7()
    redis = RecordingRedis()
    barrier = Barrier(2)
    try:
        with sessions() as session:
            session.add(_run(run_id, RunStatus.QUEUED))
            session.commit()

        def delivery(task_id: str) -> bool:
            with sessions() as session:
                barrier.wait(timeout=10)
                _, claimed = _claim_run(session, redis, run_id, task_id)
                return claimed

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(delivery, f"delivery-{i}") for i in range(2)]
            assert sorted(f.result(timeout=15) for f in futures) == [False, True]

        with sessions() as session:
            run = session.get(Run, run_id)
            assert run.status == RunStatus.SCHEDULING
            assert run.celery_task_id in {"delivery-0", "delivery-1"}
            rows = session.scalars(select(RunEvent).where(RunEvent.run_id == run_id)).all()
            assert len(rows) == 1
            assert rows[0].payload == {"status": "SCHEDULING"}
    finally:
        with sessions() as session:
            session.execute(delete(Run).where(Run.id == run_id))
            session.commit()
        engine.dispose()


def test_terminal_redelivery_republishes_original_pending_event() -> None:
    engine = create_engine(_sync_url())
    sessions = sessionmaker(engine, expire_on_commit=False)
    run_id, event_id = uuid7(), uuid7()
    redis = RecordingRedis()
    try:
        with sessions() as session:
            session.add(_run(run_id, RunStatus.SUCCEEDED))
            session.add(RunEvent(
                run_id=run_id, event_id=event_id, type=EventType.RUN_STATUS,
                payload={"status": "SUCCEEDED"}, terminal=True, published_at=None,
            ))
            session.commit()
            original = session.scalar(select(RunEvent).where(RunEvent.run_id == run_id))
            original_seq = original.seq

        with sessions() as session:
            _, claimed = _claim_run(session, redis, run_id, "redelivery")
            assert claimed is False
            assert session.get(Run, run_id).status == RunStatus.SUCCEEDED

        with sessions() as session:
            rows = session.scalars(select(RunEvent).where(RunEvent.run_id == run_id)).all()
            assert len(rows) == 1
            assert rows[0].event_id == event_id
            assert rows[0].seq == original_seq
            assert rows[0].published_at is not None
            assert redis.published == [(f"run:{run_id}", {
                "seq": str(original_seq), "type": "run.status",
                "data": '{"status": "SUCCEEDED"}',
            })]
    finally:
        with sessions() as session:
            session.execute(delete(Run).where(Run.id == run_id))
            session.commit()
        engine.dispose()


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
    engine = create_engine(_sync_url())
    sessions = sessionmaker(engine, expire_on_commit=False)
    run_id = uuid7()
    redis = RecordingRedis()
    try:
        with sessions() as session:
            run = _run(run_id, RunStatus.RUNNING)
            run.result_received_at = datetime.now(UTC) if has_result else None
            run.error = prior_error
            session.add(run)
            session.commit()

        with sessions() as session:
            first = _finish_sync(session, redis, run_id, exit_code, "sandbox log", timed_out=timed_out)
            second = _finish_sync(session, redis, run_id, exit_code, "sandbox log", timed_out=timed_out)
            assert first == {"run_id": str(run_id), "status": "FAILED"}
            assert second["idempotent_skip"] is True

            run = session.get(Run, run_id)
            assert run.status == RunStatus.FAILED
            assert run.error["code"] == expected_code
            assert run.finished_at is not None
            events = session.scalars(select(RunEvent).where(RunEvent.run_id == run_id)).all()
            assert len(events) == 1
            assert events[0].terminal is True
            assert events[0].payload == {"status": "FAILED"}
            assert len(redis.published) == 1
    finally:
        with sessions() as session:
            session.execute(delete(Run).where(Run.id == run_id))
            session.commit()
        engine.dispose()


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
