"""runner-worker —— 一次 Run 就是一個 Celery task（ADR-0002）。

╔══════════════════════════════════════════════════════════════════════════╗
║ P1：這個檔案**永遠不得 import 或 exec 使用者程式碼**。                      ║
║ 它只做四件事：申請沙盒 → 注入 run token → 等它結束 → 回寫狀態。             ║
║ 連「檢查 build() 在不在」都不行 —— import 使用者模組就是執行使用者程式碼。   ║
║ 要驗證：`grep -rn "apps.sandbox.runtime" apps/worker/` 必須沒有結果。       ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import logging
import uuid
from datetime import UTC, datetime

import docker
from redis import Redis
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import NoResultFound

from apps.api.config import get_settings
from apps.api.enums import ErrorCode, RunStatus
from apps.api.models import Run
from apps.api.services import runtoken
from apps.api.services import events as async_events
from apps.sandbox.drivers.base import SandboxHandle
from apps.sandbox.drivers.docker_driver import DockerSandboxDriver
from apps.sandbox.manager import QuotaExceeded, SandboxManager
from apps.worker.celery_app import celery_app
from apps.worker.db import SessionSync
from apps.worker.events import add_status_event, lock_run, publish_pending

log = logging.getLogger(__name__)
settings = get_settings()

def _manager(redis: Redis) -> SandboxManager:
    return SandboxManager(
        DockerSandboxDriver(docker.from_env()),
        redis,
        image=settings.sandbox_image,
        network=settings.sandbox_network,
        gateway_url=settings.sandbox_gateway_url,
        max_concurrent=settings.sandbox_max_concurrent,
    )


def _transition(
    session: Session, redis: Redis, run_id: uuid.UUID, status: RunStatus,
    *, terminal: bool = False, error: dict | None = None, **fields,
) -> Run:
    """狀態 + 它的 run.status 事件在同一個 transaction 裡 commit，之後才發 Redis。

    順序很重要：先落地再發布。反過來的話 Redis 發出去了但 commit 失敗，
    消費端會看到一個資料庫裡不存在的事件。
    """
    run = lock_run(session, run_id)
    run.status = status
    if error is not None:
        run.error = error
    if terminal:
        run.finished_at = datetime.now(UTC)
    for key, value in fields.items():
        setattr(run, key, value)
    add_status_event(session, run, terminal=terminal)
    session.commit()
    publish_pending(session, redis, run_id)
    return run


def _error(code: ErrorCode, message: str, run_id: str, retryable: bool) -> dict:
    return {"code": str(code), "message": message, "run_id": run_id, "retryable": retryable}


def _claim_run(
    session: Session, redis: Redis, run_id: uuid.UUID, task_id: str | None,
) -> tuple[Run | None, bool]:
    """Acquire the single execution right for a Run.

    The lock and status recheck must happen immediately before reserving a
    sandbox. A duplicate Celery delivery can otherwise observe the old
    ``QUEUED`` value and create a second container while the first delivery is
    already running.
    """
    try:
        run = lock_run(session, run_id)
    except NoResultFound:
        return None, False
    if RunStatus(run.status).is_terminal:
        # A previous delivery may have committed a terminal event but failed
        # before publishing it. Redelivery is also the retry path for it.
        publish_pending(session, redis, run_id)
        return run, False
    if RunStatus(run.status) is not RunStatus.QUEUED:
        return run, False

    run.status = RunStatus.SCHEDULING
    run.celery_task_id = task_id
    add_status_event(session, run)
    session.commit()
    publish_pending(session, redis, run_id)
    return run, True


def _finish_sync(
    session: Session,
    redis: Redis,
    run_id: uuid.UUID,
    exit_code: int,
    logs: str | None,
    *,
    timed_out: bool = False,
) -> dict:
    """Finish a sandbox run after the process has exited.

    ``/result`` only stores the result.  This function is the worker's single
    authority for deciding whether the exited sandbox succeeded, and it emits
    the terminal event in the same transaction as the status update.
    """
    run = lock_run(session, run_id)
    if RunStatus(run.status).is_terminal:
        publish_pending(session, redis, run_id)
        return {"run_id": str(run_id), "status": str(run.status), "idempotent_skip": True}

    if timed_out:
        status = RunStatus.FAILED
        error = _error(ErrorCode.SANDBOX_TIMEOUT,
                       f"sandbox exceeded {run.timeout_seconds}s", str(run_id), True)
    elif run.error is not None:
        # The runtime reports USER_CODE_ERROR through /result before exiting.
        status = RunStatus.FAILED
        error = None
    elif exit_code != 0:
        status = RunStatus.FAILED
        error = _error(ErrorCode.SANDBOX_ERROR,
                       f"sandbox exited {exit_code}: {(logs or '')[-500:]}",
                       str(run_id), True)
    elif run.result_received_at is None:
        # A zero exit code without a Gateway result is still an incomplete run.
        status = RunStatus.FAILED
        error = _error(ErrorCode.SANDBOX_ERROR,
                       "sandbox exited without submitting a result", str(run_id), True)
    else:
        status = RunStatus.SUCCEEDED
        error = None

    run.status = status
    if error is not None:
        run.error = error
    run.finished_at = datetime.now(UTC)
    add_status_event(session, run, terminal=True)
    session.commit()
    publish_pending(session, redis, run_id)
    return {"run_id": str(run_id), "status": str(run.status)}


async def finish(
    session: AsyncSession,
    run_id: uuid.UUID,
    exit_code: int,
    logs: str | None,
    *,
    timed_out: bool = False,
) -> dict:
    """Async DB equivalent used by Gateway integration tests and async callers.

    Keeping this small adapter beside the sync Celery implementation lets the
    worker remain synchronous while preserving the exact terminal-state rules
    for async verification code.
    """
    run = await async_events.lock_run(session, run_id)
    if RunStatus(run.status).is_terminal:
        return {"run_id": str(run_id), "status": str(run.status), "idempotent_skip": True}

    if timed_out:
        status = RunStatus.FAILED
        error = _error(ErrorCode.SANDBOX_TIMEOUT,
                       f"sandbox exceeded {run.timeout_seconds}s", str(run_id), True)
    elif run.error is not None:
        status = RunStatus.FAILED
        error = None
    elif exit_code != 0:
        status = RunStatus.FAILED
        error = _error(ErrorCode.SANDBOX_ERROR,
                       f"sandbox exited {exit_code}: {(logs or '')[-500:]}",
                       str(run_id), True)
    elif run.result_received_at is None:
        status = RunStatus.FAILED
        error = _error(ErrorCode.SANDBOX_ERROR,
                       "sandbox exited without submitting a result", str(run_id), True)
    else:
        status = RunStatus.SUCCEEDED
        error = None

    run.status = status
    if error is not None:
        run.error = error
    run.finished_at = datetime.now(UTC)
    async_events.status_event(session, run, terminal=True)
    await session.commit()
    return {"run_id": str(run_id), "status": str(run.status)}


@celery_app.task(name="zentra.run.execute", bind=True, acks_late=True)
def execute_run(self, run_id: str) -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    manager = _manager(redis)
    session: Session = SessionSync()

    handle: SandboxHandle | None = None
    token: str | None = None
    reserved = False

    try:
        rid = uuid.UUID(run_id)
        run, claimed = _claim_run(session, redis, rid, self.request.id)
        if run is None:
            log.warning("run %s not found, dropping task", run_id)
            return {"run_id": run_id, "status": "NOT_FOUND"}

        # 冪等（acks_late 會重送）：row lock 下重查到已被其他 delivery
        # claim 的 SCHEDULING/RUNNING，不能再 reserve 或 create sandbox。
        if not claimed:
            log.info("run %s already claimed/terminal (%s), skipping", run_id, run.status)
            return {"run_id": run_id, "status": run.status, "idempotent_skip": True}

        try:
            manager.reserve(run_id)
            reserved = True
        except QuotaExceeded as exc:
            _transition(session, redis, rid, RunStatus.FAILED, terminal=True,
                        error=_error(ErrorCode.QUOTA_EXCEEDED, str(exc), run_id, True))
            return {"run_id": run_id, "status": str(RunStatus.FAILED)}

        # P4：這是沙盒唯一拿得到的憑證
        token = runtoken.issue_sync(
            redis, run_id, run.timeout_seconds,
        )

        handle = manager.create(run_id, token)
        run = _transition(session, redis, rid, RunStatus.RUNNING,
                          sandbox_id=handle.id, started_at=datetime.now(UTC))

        timeout = run.timeout_seconds
        result = manager.driver.wait(handle, timeout=timeout)

        # 沙盒已經透過 Internal Gateway 回寫了 output / error，重讀一次拿最新值
        session.expire_all()
        run = session.get(Run, rid)

        return _finish_sync(session, redis, rid, result.exit_code, result.logs,
                            timed_out=result.timed_out)

    except Exception as exc:                      # noqa: BLE001 — 最後一道網，不能讓 Run 卡在 RUNNING
        log.exception("run %s crashed in worker", run_id)
        try:
            session.rollback()
            current = session.get(Run, uuid.UUID(run_id))
            if current and not RunStatus(current.status).is_terminal:
                _transition(session, redis, current.id, RunStatus.FAILED, terminal=True,
                            error=_error(ErrorCode.INTERNAL_ERROR, str(exc), run_id, True))
        except Exception:
            log.exception("failed to mark run %s as FAILED", run_id)
        raise

    finally:
        # 順序有意義：先撤銷憑證，再銷毀沙盒，最後才放掉併發名額
        if token:
            # Revoke every token for this Run, including one left by a
            # redelivered task that raced before the terminal state was set.
            runtoken.revoke_run_sync(redis, run_id)
        if handle:
            try:
                manager.driver.destroy(handle)     # 冪等
            except Exception:
                log.exception("failed to destroy sandbox for run %s", run_id)
        if reserved:
            manager.release(run_id)
        session.close()
        redis.close()
