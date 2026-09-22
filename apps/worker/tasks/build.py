"""OCI image validation worker; user code only executes in a container."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import docker
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.config import get_settings
from apps.api.enums import AgentVersionStatus, ErrorCode
from apps.api.models import AgentVersion
from apps.api.services import validationtoken
from apps.sandbox.drivers.base import SandboxHandle
from apps.sandbox.drivers.docker_driver import DockerSandboxDriver
from apps.sandbox.manager import SandboxManager
from apps.worker.celery_app import celery_app
from apps.worker.db import SessionSync

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


def _claim(
    session: Session, version_id: uuid.UUID, task_id: str | None,
) -> tuple[AgentVersion | None, bool]:
    version = session.execute(
        select(AgentVersion).where(AgentVersion.id == version_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if version is None:
        return None, False
    if AgentVersionStatus(version.status).is_terminal:
        return version, False
    now = datetime.now(UTC)
    if version.build_claimed_at is not None and version.build_claimed_at > now - _CLAIM_LEASE:
        return version, False
    version.build_claimed_at = now
    version.builder_task_id = task_id
    session.commit()
    return version, True


def _fail(session: Session, version_id: uuid.UUID, message: str, stage: str) -> dict:
    version = session.execute(
        select(AgentVersion).where(AgentVersion.id == version_id).with_for_update()
    ).scalar_one()
    if AgentVersionStatus(version.status).is_terminal:
        return {"agent_version_id": str(version_id), "status": version.status,
                "idempotent_skip": True}
    version.status = AgentVersionStatus.FAILED
    version.error = _build_error(message, stage)
    version.manifest = None
    version.entrypoints = None
    version.published_at = None
    session.commit()
    return {"agent_version_id": str(version_id), "status": str(AgentVersionStatus.FAILED)}


@celery_app.task(name="zentra.build.execute", bind=True, acks_late=True)
def execute_build(self, agent_version_id: str) -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    session: Session = SessionSync()
    manager = _manager(redis)
    handle: SandboxHandle | None = None
    token: str | None = None
    stage = "PULL"
    try:
        version_id = uuid.UUID(agent_version_id)
        version, claimed = _claim(session, version_id, self.request.id)
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
        session.expire_all()
        version = session.get(AgentVersion, version_id)
        payload = version.validation_payload if version else None
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

        version = session.execute(
            select(AgentVersion).where(AgentVersion.id == version_id).with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        if AgentVersionStatus(version.status).is_terminal:
            return {"agent_version_id": agent_version_id, "status": version.status,
                    "idempotent_skip": True}
        version.image_platform = verified.platform
        version.image_size_bytes = verified.size_bytes
        version.runtime_contract = payload["runtime_contract"]
        version.manifest = payload["manifest"]
        version.entrypoints = payload["entrypoints"]
        version.error = None
        version.status = AgentVersionStatus.PUBLISHED
        version.published_at = datetime.now(UTC)
        session.commit()
        return {"agent_version_id": agent_version_id,
                "status": str(AgentVersionStatus.PUBLISHED)}
    except Exception as exc:  # noqa: BLE001 - stage is mapped to the public build error
        log.exception("agent version %s failed at %s", agent_version_id, stage)
        session.rollback()
        # Platform exceptions carry the authoritative PULL/INSPECT stage.
        mapped_stage = getattr(exc, "stage", stage)
        if mapped_stage not in {"PULL", "INSPECT", "VALIDATE"}:
            mapped_stage = stage
        try:
            return _fail(session, uuid.UUID(agent_version_id), str(exc), mapped_stage)
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
        session.close()
        redis.close()
