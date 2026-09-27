"""常駐 Agent 容器的生命週期執行端（ZEN-015 / ADR-0012）。

api 只寫 DB 的期望狀態並發這個任務；**實際碰 Docker 的只有 worker** ——
`docker.sock` 仍然只掛在 worker 上，那條規則一個字都沒改。

三個動作共用一個任務，因為它們是同一件事的三個方向：
把某個容器帶到期望狀態。分成三個任務會讓「停止」與「重啟」有機會亂序。
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

import docker
from redis import Redis
from sqlalchemy.orm import Session

from apps.api.config import get_settings
from apps.api.models import AgentContainer, AgentVersion
from apps.api.services import containertoken
from apps.sandbox.drivers.docker_driver import DockerSandboxDriver
from apps.sandbox.manager import SandboxManager
from apps.worker.celery_app import celery_app
from apps.worker.db import SessionSync

log = logging.getLogger(__name__)
settings = get_settings()

#: container token 的 TTL。給得比預期壽命長很多 —— 容器還活著而 token 過期的話，
#: 它會取不到工作**而且沒有任何錯誤訊息**，那是最難查的一種故障。
#: 正常的撤銷是停止容器時主動做，TTL 只防「忘了撤銷」的殘留。
_TOKEN_TTL_SECONDS = 30 * 24 * 3600


def _manager(redis: Redis) -> SandboxManager:
    return SandboxManager(
        DockerSandboxDriver(docker.from_env()), redis,
        image=settings.sandbox_image,
        network=settings.sandbox_network,
        gateway_url=settings.sandbox_gateway_url,
        max_concurrent=settings.sandbox_max_concurrent,
    )


def _destroy(manager: SandboxManager, agent_id, docker_id: str | None) -> None:
    """銷毀這個 Agent 的常駐容器。**必須冪等** —— 重啟、停止、失敗清理都會走到這裡。

    **不能只靠 `row.container_id`。** 上一次啟動若在建立之後、寫回 DB 之前失敗
    （或容器一啟動就退出），DB 那欄是空的但 Docker 裡有一個同名的殘骸，
    下次啟動就會撞名 409。所以用**固定的容器名**再補刀一次 ——
    實測踩過：重啟直接失敗，錯誤是 "The container name ... is already in use"。
    """
    from apps.sandbox.drivers.base import SandboxHandle

    targets = [t for t in (docker_id, f"zentra-serve-{agent_id}") if t]
    for target in targets:
        try:
            manager.driver_destroy(SandboxHandle(id=target, name=target))
        except Exception as exc:                  # noqa: BLE001
            log.warning("destroying container %s failed: %s", target, exc)


#: 建立後等多久再確認它還活著。短到不拖慢啟動，長到抓得住「一啟動就退出」。
_LIVENESS_GRACE_SECONDS = 2.0


def _liveness(manager: SandboxManager, docker_id: str) -> tuple[bool, str]:
    """回 `(還活著嗎, 說明)`。死了的話說明帶上容器自己的輸出 —— 那是唯一的線索。"""
    time.sleep(_LIVENESS_GRACE_SECONDS)
    try:
        container = manager.driver_inspect(docker_id)
    except Exception as exc:                      # noqa: BLE001
        return False, f"could not inspect the container after starting it: {exc}"
    state = (container.get("State") or {})
    if state.get("Running"):
        return True, ""
    logs = manager.driver_logs(docker_id)
    return False, (
        f"the container exited with code {state.get('ExitCode')} right after starting. "
        f"Output: {logs.strip()[-1200:]}"
    )


@celery_app.task(name="zentra.container.apply", bind=True, max_retries=0)
def apply_container(self, container_uuid: str, action: str) -> dict:
    """把容器帶到期望狀態。`action` 是 start | restart | stop。"""
    redis = Redis.from_url(settings.redis_url)
    manager = _manager(redis)

    with SessionSync() as session:
        row = session.get(AgentContainer, uuid.UUID(container_uuid))
        if row is None:
            return {"container_id": container_uuid, "status": "MISSING"}

        # 停止與重啟都要先拆掉現有的東西。撤銷 token 排在銷毀容器之前：
        # 先撤銷的話，就算銷毀失敗，那個容器也已經拿不到任何新工作了。
        if action in ("stop", "restart"):
            containertoken.revoke_agent_sync(redis, row.agent_id)
            _destroy(manager, row.agent_id, row.container_id)
            row.container_id = None
            if action == "stop":
                row.status = "STOPPED"
                row.inflight_runs = 0
                session.commit()
                return {"container_id": container_uuid, "status": "STOPPED"}

        version = session.get(AgentVersion, row.agent_version_id)
        if version is None or not version.image_ref:
            row.status = "FAILED"
            row.error = "the agent version this container targets no longer exists"
            session.commit()
            return {"container_id": container_uuid, "status": "FAILED"}

        # 啟動前先清殘骸。`start` 也要做，不只 `restart`：從 FAILED 重新啟動時
        # DB 沒有 container_id，但 Docker 裡可能留著上次那個退出的容器。
        _destroy(manager, row.agent_id, row.container_id)

        try:
            token = containertoken.issue_sync(redis, row.agent_id, _TOKEN_TTL_SECONDS)
            manager.prepare_image(version.image_ref)
            handle = manager.create_serve(
                agent_id=str(row.agent_id),
                container_token=token,
                image_ref=version.image_ref,
                max_concurrent_runs=settings.sandbox_runs_per_container,
            )
        except Exception as exc:                  # noqa: BLE001
            # 建不起來就撤銷剛發的 token —— 留著一把沒有容器在用的憑證沒有意義，
            # 而且它會在 `container_tokens:` 裡累積成雜訊。
            containertoken.revoke_agent_sync(redis, row.agent_id)
            row.status = "FAILED"
            row.error = f"{type(exc).__name__}: {exc}"[:2000]
            session.commit()
            log.warning("starting container for agent %s failed: %s", row.agent_id, exc)
            return {"container_id": container_uuid, "status": "FAILED"}

        row.container_id = handle.id
        row.started_at = datetime.now(UTC)
        row.inflight_runs = 0
        row.error = None

        # **建立成功 ≠ 活著。** 容器可能一啟動就退出（image 太舊、沒有 serve 模式、
        # 程式碼在 import 階段就炸）。標成 READY 而它其實死了，是最誤導的狀態 ——
        # 畫面說好的，派工卻永遠沒人接。所以這裡實際回頭看一眼。
        alive, detail = _liveness(manager, handle.id)
        if alive:
            row.status = "READY"
            row.last_health_at = datetime.now(UTC)
        else:
            containertoken.revoke_agent_sync(redis, row.agent_id)
            row.status = "FAILED"
            row.error = detail[:2000]
            log.warning("container for agent %s exited immediately: %s", row.agent_id, detail)
        session.commit()
        return {
            "container_id": container_uuid,
            "status": row.status,
            "docker_id": handle.id,
        }
