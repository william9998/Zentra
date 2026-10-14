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
from datetime import UTC, datetime, timedelta

import docker
import httpx
from redis import Redis
from sqlalchemy import text
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


def _destroy(manager: SandboxManager, container_uuid, docker_id: str | None) -> None:
    """銷毀這個 Agent 的常駐容器。**必須冪等** —— 重啟、停止、失敗清理都會走到這裡。

    **不能只靠 `row.container_id`。** 上一次啟動若在建立之後、寫回 DB 之前失敗
    （或容器一啟動就退出），DB 那欄是空的但 Docker 裡有一個同名的殘骸，
    下次啟動就會撞名 409。所以用**固定的容器名**再補刀一次 ——
    實測踩過：重啟直接失敗，錯誤是 "The container name ... is already in use"。
    """
    from apps.sandbox.drivers.base import SandboxHandle

    targets = [t for t in (docker_id, f"zentra-serve-{container_uuid}") if t]
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
            # 只撤銷這一個容器的 token —— 照 agent 撤會把兄弟容器一起弄啞
            containertoken.revoke_container_sync(redis, row.id)
            _destroy(manager, row.id, row.container_id)
            row.container_id = None
            if action == "stop":
                row.status = "STOPPED"
                row.inflight_runs = 0
                session.commit()
                return {"container_id": container_uuid, "status": "STOPPED"}

        status = start_container(session, redis, manager, row)
        return {
            "container_id": container_uuid,
            "status": status,
            "docker_id": row.container_id,
        }


def start_container(
    session: Session, redis: Redis, manager: SandboxManager, row: AgentContainer,
) -> str:
    """把一列 `agent_containers` 實際帶到 READY（或 FAILED）。回傳最終狀態。

    **抽出來是因為有兩個呼叫端**：使用者按「啟動」走 `apply_container`，
    而第一筆 Run 會直接在 `q.run` 的任務裡呼叫它（ZEN-015 的自動啟動）。
    那條路徑不能改走 `q.build` —— worker 的兩個佇列共用同一個行程池，
    等待中的 Run 佔滿位子時，`q.build` 的啟動任務就永遠排不到，會死鎖。
    """
    version = session.get(AgentVersion, row.agent_version_id)
    if version is None or not version.image_ref:
        row.status = "FAILED"
        row.error = "the agent version this container targets no longer exists"
        session.commit()
        return "FAILED"

    # 啟動前先清殘骸。`start` 也要做，不只 `restart`：從 FAILED 重新啟動時
    # DB 沒有 container_id，但 Docker 裡可能留著上次那個退出的容器。
    _destroy(manager, row.id, row.container_id)

    try:
        token = containertoken.issue_sync(
            redis, row.agent_id, _TOKEN_TTL_SECONDS, container_uuid=row.id)
        manager.prepare_image(version.image_ref)
        handle = manager.create_serve(
            container_uuid=str(row.id),
            agent_id=str(row.agent_id),
            container_token=token,
            image_ref=version.image_ref,
            max_concurrent_runs=settings.sandbox_runs_per_container,
        )
    except Exception as exc:                  # noqa: BLE001
        # 建不起來就撤銷剛發的 token —— 留著一把沒有容器在用的憑證沒有意義，
        # 而且它會在索引裡累積成雜訊。
        containertoken.revoke_container_sync(redis, row.id)
        row.status = "FAILED"
        row.error = f"{type(exc).__name__}: {exc}"[:2000]
        session.commit()
        log.warning("starting container for agent %s failed: %s", row.agent_id, exc)
        return "FAILED"

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
        containertoken.revoke_container_sync(redis, row.id)
        row.status = "FAILED"
        row.error = detail[:2000]
        log.warning("container for agent %s exited immediately: %s", row.agent_id, detail)
    session.commit()
    return row.status


@celery_app.task(name="zentra.container.sweep_idle", max_retries=0)
def sweep_idle_containers() -> dict:
    """停掉閒置太久的常駐容器（ZEN-015）。

    ## 為什麼需要它

    容器現在會被第一筆 Run 自動拉起來。**自動產生的東西必須自動消失**，
    不然「叫過一次」就等於「永久佔 2 GB」，而使用者從來沒按過啟動，
    也就不會想到要去按停止。

    ## 閒置怎麼判斷

    **不能看 `last_health_at`。** 容器每 25 秒 long-poll 一次取工作，
    所以那個欄位永遠是新的 —— 它量的是活著，不是有事做。

    這裡看的是「最後一次派工給它是什麼時候」，沒派過就從啟動時間算。
    `inflight_runs > 0` 一律不動：正在跑的容器不管多久都不算閒置。
    """
    idle_seconds = settings.container_idle_timeout_seconds
    if idle_seconds <= 0:
        return {"swept": 0, "reason": "disabled"}

    cutoff = datetime.now(UTC) - timedelta(seconds=idle_seconds)
    swept = []
    with SessionSync() as session:
        rows = session.execute(text("""
            SELECT c.id
            FROM agent_containers c
            WHERE c.status = 'READY'
              AND c.inflight_runs <= 0
              AND COALESCE(
                    (SELECT max(r.created_at) FROM runs r WHERE r.container_id = c.id),
                    c.started_at
                  ) < :cutoff
        """), {"cutoff": cutoff}).scalars().all()

        redis = Redis.from_url(settings.redis_url)
        manager = _manager(redis)
        try:
            for container_uuid in rows:
                row = session.get(AgentContainer, container_uuid)
                if row is None or row.status != "READY" or row.inflight_runs > 0:
                    continue          # 掃描與停止之間又有工作進來了
                containertoken.revoke_container_sync(redis, row.id)
                _destroy(manager, row.id, row.container_id)
                row.status = "STOPPED"
                row.container_id = None
                row.inflight_runs = 0
                row.error = None
                session.commit()
                swept.append(str(container_uuid))
                log.info("stopped idle container %s (agent %s)", container_uuid, row.agent_id)
        finally:
            redis.close()
    return {"swept": len(swept), "containers": swept}


def _registry_target(image_ref: str) -> tuple[str, str] | None:
    """把 `image_ref` 拆成 `(repository, digest)`，**只認我們自己的 registry**。

    `localhost:6000/stock-agent@sha256:…` → `("stock-agent", "sha256:…")`

    別人的 registry 一律回 None：平台沒有權限、也沒有立場去刪外部的 image。
    只比對前綴而不是「有沒有 `@`」，是因為誤刪外部 registry 的 manifest
    是那種會波及到平台以外的錯。
    """
    prefix = settings.internal_registry_host + "/"
    if not image_ref.startswith(prefix) or "@" not in image_ref:
        return None
    repository, _, digest = image_ref[len(prefix):].partition("@")
    return (repository, digest) if repository and digest else None


def _delete_from_registry(image_ref: str) -> str:
    """刪掉 registry 裡的 manifest。回一句話說結果，**絕不拋例外**。

    ## 為什麼刪了還不會馬上釋放空間

    `DELETE /v2/<repo>/manifests/<digest>` 只是把 manifest 標成可回收，
    blob 要等 `registry garbage-collect` 才真的消失，而那要停機做
    （ADR-0009 的維運程序）。**畫面上不要說「已釋放空間」** —— 沒有。

    ## 為什麼失敗不算失敗

    Agent 在 DB 裡已經刪掉了，這裡再怎麼樣都救不回來。刪不掉 manifest 的
    後果是「多佔一點磁碟」，讓整個刪除任務失敗重試的後果是「一直重跑」。
    """
    target = _registry_target(image_ref)
    if target is None:
        return f"{image_ref}: 不是內部 registry 的 image，跳過"
    repository, digest = target
    url = f"{settings.internal_registry_url}/v2/{repository}/manifests/{digest}"
    try:
        response = httpx.delete(url, timeout=30)
    except Exception as exc:                      # noqa: BLE001
        return f"{image_ref}: 連不上 registry（{type(exc).__name__}）"
    if response.status_code in (202, 404):
        # 404 = 已經不在了，對「把它弄不見」這個目的來說跟成功一樣
        return f"{image_ref}: manifest 已刪除"
    if response.status_code == 405:
        return (f"{image_ref}: registry 不允許刪除 —— "
                "REGISTRY_STORAGE_DELETE_ENABLED 沒有設成 true")
    return f"{image_ref}: registry 回 {response.status_code}"


@celery_app.task(name="zentra.agent.purge", max_retries=0)
def purge_agent(payload: dict) -> dict:
    """刪掉 Agent 之後，把平台外的東西一起收掉（ADR-0013）。

    **DB 的部分在 api 那側已經做完了**，這裡只處理碰得到 Docker 與 registry 的事：
    停容器、刪主機上的 image、刪 registry 裡的 manifest。

    每一步都獨立包起來：其中一步失敗不該讓其他步驟不做。
    使用者已經看到「刪除成功」了，這裡再拋例外只會變成重試風暴。
    """
    redis = Redis.from_url(settings.redis_url)
    manager = _manager(redis)
    notes = []
    try:
        for container_uuid in payload.get("container_ids") or []:
            try:
                containertoken.revoke_container_sync(redis, container_uuid)
                _destroy(manager, container_uuid, None)
                notes.append(f"容器 {container_uuid} 已移除")
            except Exception as exc:              # noqa: BLE001
                notes.append(f"容器 {container_uuid} 移除失敗：{type(exc).__name__}")

        for image_ref in dict.fromkeys(payload.get("image_refs") or []):
            notes.append(_delete_from_registry(image_ref))
            try:
                manager.driver.remove_image(image_ref)
                notes.append(f"{image_ref}: 主機上的 image 已移除")
            except Exception as exc:              # noqa: BLE001
                # 還有容器在用、或本來就沒 pull 過 —— 兩種都不是問題
                notes.append(f"{image_ref}: 主機上的 image 沒移除（{type(exc).__name__}）")
    finally:
        redis.close()

    log.info("purged agent %s: %s", payload.get("agent_id"), "; ".join(notes))
    return {"agent_id": payload.get("agent_id"), "notes": notes}
