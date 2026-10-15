"""OCI image validation worker; user code only executes in a container."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import docker
from psycopg.types.json import Jsonb
from redis import Redis

from apps.api.config import get_settings
from apps.api.enums import AgentVersionStatus, ErrorCode
from apps.api.rows import AgentVersion
from apps.api.services import validationtoken
from apps.sandbox.drivers.base import SandboxHandle
from apps.sandbox.drivers.docker_driver import DockerSandboxDriver
from apps.sandbox.manager import SandboxManager
from apps.worker import db
from apps.worker.celery_app import celery_app

log = logging.getLogger(__name__)
settings = get_settings()
_CLAIM_LEASE = timedelta(minutes=10)


def _manager(redis: Redis) -> SandboxManager:
    return SandboxManager(
        DockerSandboxDriver(docker.from_env()), redis,
        image=settings.sandbox_image,
        network=settings.sandbox_network,
        gateway_url=settings.sandbox_gateway_url,
        max_concurrent=settings.sandbox_max_concurrent,
    )


def _build_error(message: str, stage: str) -> dict:
    return {
        "code": str(ErrorCode.BUILD_FAILED),
        "message": message,
        "run_id": None,
        "retryable": False,
        "details": {"stage": stage},
    }


def _load_version(conn, version_id: uuid.UUID, *, for_update: bool = False) -> AgentVersion | None:
    if for_update:
        db.require_transaction(conn)
        tail = " FOR UPDATE"
    else:
        tail = ""
    rec = conn.execute(
        f"SELECT {AgentVersion.returning()} FROM agent_versions "
        f"WHERE id = %(id)s{tail}",
        {"id": version_id},
    ).fetchone()
    return AgentVersion.from_row(rec) if rec is not None else None


def _claim(
    conn, version_id: uuid.UUID, task_id: str | None,
) -> tuple[AgentVersion | None, bool]:
    version = _load_version(conn, version_id, for_update=True)
    if version is None:
        return None, False
    if AgentVersionStatus(version.status).is_terminal:
        return version, False
    now = datetime.now(UTC)
    if version.build_claimed_at is not None and version.build_claimed_at > now - _CLAIM_LEASE:
        return version, False
    rec = conn.execute(
        f"""
        UPDATE agent_versions
           SET build_claimed_at = %(now)s, builder_task_id = %(task_id)s
         WHERE id = %(id)s
        RETURNING {AgentVersion.returning()}
        """,
        {"id": version_id, "now": now, "task_id": task_id},
    ).fetchone()
    return AgentVersion.from_row(rec), True


def _fail(version_id: uuid.UUID, message: str, stage: str) -> dict:
    with db.transaction() as conn:
        version = _load_version(conn, version_id, for_update=True)
        if version is None:
            return {"agent_version_id": str(version_id), "status": "NOT_FOUND"}
        if AgentVersionStatus(version.status).is_terminal:
            return {"agent_version_id": str(version_id), "status": version.status,
                    "idempotent_skip": True}
        conn.execute(
            """
            UPDATE agent_versions SET
                status = %(status)s, error = %(error)s,
                manifest = NULL, entrypoints = NULL, published_at = NULL
            WHERE id = %(id)s
            """,
            {
                "id": version_id,
                "status": str(AgentVersionStatus.FAILED),
                "error": Jsonb(_build_error(message, stage)),
            },
        )
    return {"agent_version_id": str(version_id), "status": str(AgentVersionStatus.FAILED)}


def _is_latest(agent_id: uuid.UUID, version_id: uuid.UUID) -> bool:
    """這一筆是不是這個 Agent 現在的最新 PUBLISHED（依 semver，與 API 解析版本同規則）。"""
    from apps.api.services.agents import _semver_key

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, semver FROM agent_versions WHERE agent_id = %(a)s AND status = %(s)s",
            {"a": agent_id, "s": str(AgentVersionStatus.PUBLISHED)},
        ).fetchall()
    if not rows:
        return False
    latest = max(rows, key=lambda r: _semver_key(r["semver"]))
    return latest["id"] == version_id


@celery_app.task(name="zentra.build.execute", bind=True, acks_late=True)
def execute_build(self, agent_version_id: str) -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    manager = _manager(redis)
    handle: SandboxHandle | None = None
    token: str | None = None
    stage = "PULL"
    try:
        version_id = uuid.UUID(agent_version_id)
        with db.transaction() as conn:
            version, claimed = _claim(conn, version_id, self.request.id)
        if version is None:
            return {"agent_version_id": agent_version_id, "status": "NOT_FOUND"}
        if not claimed:
            return {"agent_version_id": agent_version_id, "status": version.status,
                    "idempotent_skip": True}

        # image_ref comes exclusively from the locked DB row. Celery payload
        # cannot choose an image, command, network, mount, or credential.
        image_ref = version.image_ref
        metadata = manager.prepare_image(image_ref)
        stage = "INSPECT"
        if metadata.digest != version.image_digest or image_ref != metadata.image_ref:
            raise ValueError("inspected RepoDigest does not match the registered digest")

        stage = "VALIDATE"
        token = validationtoken.issue_sync(redis, version_id, 600)
        handle = manager.create_validation(agent_version_id, token, image_ref)
        result = manager.driver.wait(handle, timeout=600)
        with db.connection() as conn:
            current = _load_version(conn, version_id)
        payload = current.validation_payload if current else None
        if result.timed_out:
            raise ValueError("validation container timed out")
        if result.exit_code != 0:
            raise ValueError(f"validation container exited {result.exit_code}: {(result.logs or '')[-500:]}")
        if not payload:
            raise ValueError("validation container exited without a validation payload")
        if not payload.get("valid"):
            message = (payload.get("error") or {}).get("message", "image validation failed")
            raise ValueError(message)

        # Re-inspect after user code has finished validation. Publication is
        # allowed only while the local image still resolves to the exact
        # immutable reference registered in PostgreSQL.
        stage = "INSPECT"
        verified = manager.inspect_image(image_ref)
        if verified.digest != version.image_digest or verified.image_ref != image_ref:
            raise ValueError("post-validation RepoDigest no longer matches the registered digest")

        with db.transaction() as conn:
            locked = _load_version(conn, version_id, for_update=True)
            if locked is None or AgentVersionStatus(locked.status).is_terminal:
                return {"agent_version_id": agent_version_id,
                        "status": locked.status if locked else "NOT_FOUND",
                        "idempotent_skip": True}
            # ADR-0016：同版號的舊現役在**同一個交易**裡讓位。先讓位再發布，
            # 不然「一個版號至多一筆 PUBLISHED」的 unique index 會擋下這次 UPDATE。
            now = datetime.now(UTC)
            replaced = conn.execute(
                """
                UPDATE agent_versions SET status = %(superseded)s, superseded_at = %(now)s
                WHERE agent_id = %(agent_id)s AND semver = %(semver)s
                  AND status = %(published)s AND id <> %(id)s
                RETURNING id
                """,
                {"superseded": str(AgentVersionStatus.SUPERSEDED), "now": now,
                 "agent_id": locked.agent_id, "semver": locked.semver,
                 "published": str(AgentVersionStatus.PUBLISHED), "id": version_id},
            ).fetchone()
            conn.execute(
                """
                UPDATE agent_versions SET
                    image_platform = %(platform)s,
                    image_size_bytes = %(size)s,
                    runtime_contract = %(contract)s,
                    manifest = %(manifest)s,
                    entrypoints = %(entrypoints)s,
                    error = NULL,
                    status = %(status)s,
                    published_at = %(now)s
                WHERE id = %(id)s
                """,
                {
                    "id": version_id,
                    "platform": verified.platform,
                    "size": verified.size_bytes,
                    "contract": payload["runtime_contract"],
                    "manifest": Jsonb(payload["manifest"]),
                    "entrypoints": Jsonb(payload["entrypoints"]),
                    "status": str(AgentVersionStatus.PUBLISHED),
                    "now": now,
                },
            )
        # commit 之後才派：這些任務要看得到這次的結果，否則會以為沒事可做。
        from apps.worker.tasks.container import drain_other_versions, retire_version

        if replaced is not None:
            retire_version.apply_async(args=[str(replaced["id"])])
        if _is_latest(locked.agent_id, version_id):
            # 新請求從現在起都走這個版本，其他版本的容器跑完手上的就停（一個 Agent 一個容器）。
            drain_other_versions.apply_async(args=[str(locked.agent_id), str(version_id)])
        return {"agent_version_id": agent_version_id,
                "status": str(AgentVersionStatus.PUBLISHED),
                "superseded": str(replaced["id"]) if replaced is not None else None}
    except Exception as exc:  # noqa: BLE001 - stage is mapped to the public build error
        log.exception("agent version %s failed at %s", agent_version_id, stage)
        mapped_stage = getattr(exc, "stage", stage)
        if mapped_stage not in {"PULL", "INSPECT", "VALIDATE"}:
            mapped_stage = stage
        try:
            return _fail(uuid.UUID(agent_version_id), str(exc), mapped_stage)
        except Exception:
            log.exception("failed to mark agent version %s FAILED", agent_version_id)
            raise
    finally:
        if token:
            validationtoken.revoke_version_sync(redis, agent_version_id)
        if handle:
            try:
                manager.driver.destroy(handle)
            except Exception:
                log.exception("failed to destroy validation container for %s", agent_version_id)
        redis.close()
