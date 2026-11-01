"""常駐 Agent 容器的生命週期執行端（ZEN-015 / ADR-0012）。

api 只寫 DB 的期望狀態並發這個任務；**實際碰 Docker 的只有 worker** ——
`docker.sock` 仍然只掛在 worker 上，那條規則一個字都沒改。

三個動作共用一個任務，因為它們是同一件事的三個方向：
把某個容器帶到期望狀態。分成三個任務會讓「停止」與「重啟」有機會亂序。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

import docker
import httpx
from redis import Redis

from apps.api.config import get_settings
from apps.api.enums import ErrorCode
from apps.api.rows import AgentContainer, AgentVersion
from apps.api.services import containertoken, work
from apps.api.services.containers import (
    MAX_RESTARTS_IN_WINDOW, RESTART_WINDOW_SECONDS, given_up_message,
)
from apps.sandbox.drivers.docker_driver import DockerSandboxDriver
from apps.sandbox.manager import SandboxManager
from apps.worker import db
from apps.worker.celery_app import celery_app

log = logging.getLogger(__name__)
settings = get_settings()

#: container token 的 TTL。給得比預期壽命長很多 —— 容器還活著而 token 過期的話，
#: 它會取不到工作**而且沒有任何錯誤訊息**，那是最難查的一種故障。
#: 正常的撤銷是停止容器時主動做，TTL 只防「忘了撤銷」的殘留。
_TOKEN_TTL_SECONDS = 30 * 24 * 3600


def _manager(redis: Redis) -> SandboxManager:
    return SandboxManager(
        DockerSandboxDriver(docker.from_env()), redis,
        network=settings.sandbox_network,
        gateway_url=settings.sandbox_gateway_url,
        max_concurrent=settings.sandbox_max_concurrent,
    )


def _load_container(conn, container_uuid: uuid.UUID) -> AgentContainer | None:
    rec = conn.execute(
        f"SELECT {AgentContainer.returning()} FROM agent_containers WHERE id = %(id)s",
        {"id": container_uuid},
    ).fetchone()
    return AgentContainer.from_row(rec) if rec is not None else None


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
    cid = uuid.UUID(container_uuid)

    with db.connection() as conn:
        row = _load_container(conn, cid)
    if row is None:
        return {"container_id": container_uuid, "status": "MISSING"}

    # 停止與重啟都要先拆掉現有的東西。撤銷 token 排在銷毀容器之前：
    # 先撤銷的話，就算銷毀失敗，那個容器也已經拿不到任何新工作了。
    if action in ("stop", "restart"):
        # 只撤銷這一個容器的 token —— 照 agent 撤會把兄弟容器一起弄啞
        containertoken.revoke_container_sync(redis, row.id)
        _destroy(manager, row.id, row.container_id)
        if action == "stop":
            with db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE agent_containers SET
                        container_id = NULL, status = 'STOPPED',
                        inflight_runs = 0
                    WHERE id = %(id)s
                    """,
                    {"id": cid},
                )
            return {"container_id": container_uuid, "status": "STOPPED"}
        with db.transaction() as conn:
            conn.execute(
                "UPDATE agent_containers SET container_id = NULL WHERE id = %(id)s",
                {"id": cid},
            )

    status = start_container(redis, manager, cid)
    with db.connection() as conn:
        row = _load_container(conn, cid)
    return {
        "container_id": container_uuid,
        "status": status,
        "docker_id": row.container_id if row else None,
    }


def start_container(
    redis: Redis, manager: SandboxManager, container_uuid: uuid.UUID,
) -> str:
    """把一列 `agent_containers` 實際帶到 READY（或 FAILED）。回傳最終狀態。

    **抽出來是因為有兩個呼叫端**：使用者按「啟動」走 `apply_container`，
    而第一筆 Run 會直接在 `q.run` 的任務裡呼叫它（ZEN-015 的自動啟動）。
    那條路徑不能改走 `q.build` —— worker 的兩個佇列共用同一個行程池，
    等待中的 Run 佔滿位子時，`q.build` 的啟動任務就永遠排不到，會死鎖。

    Docker／token 都在工作單元外。
    """
    with db.connection() as conn:
        row = _load_container(conn, container_uuid)
        version = None
        if row is not None:
            rec = conn.execute(
                f"SELECT {AgentVersion.returning()} FROM agent_versions WHERE id = %(id)s",
                {"id": row.agent_version_id},
            ).fetchone()
            version = AgentVersion.from_row(rec) if rec is not None else None
    if row is None:
        return "MISSING"
    if version is None or not version.image_ref:
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE agent_containers SET
                    status = 'FAILED',
                    error = %(error)s
                WHERE id = %(id)s
                """,
                {"id": container_uuid,
                 "error": "the agent version this container targets no longer exists"},
            )
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
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE agent_containers SET
                    status = 'FAILED',
                    error = %(error)s
                WHERE id = %(id)s
                """,
                {"id": container_uuid, "error": f"{type(exc).__name__}: {exc}"[:2000]},
            )
        log.warning("starting container for agent %s failed: %s", row.agent_id, exc)
        return "FAILED"

    now = datetime.now(UTC)
    alive, detail = _liveness(manager, handle.id)
    if alive:
        # inflight_runs **重算**，不是歸零：重啟（手動或存活檢查）時，已經派給這個容器、
        # 還在佇列裡的 Run 仍然掛在它身上，歸零的話它們跑完要減時會被 `> 0` 擋住，數字一路偏低。
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE agent_containers SET
                    container_id = %(docker_id)s, started_at = %(now)s,
                    inflight_runs = (
                        SELECT count(*) FROM runs
                        WHERE container_id = %(id)s AND status IN ('SCHEDULING', 'RUNNING')
                    ),
                    error = NULL,
                    status = 'READY', last_health_at = %(now)s
                WHERE id = %(id)s
                """,
                {"id": container_uuid, "docker_id": handle.id, "now": now},
            )
        return "READY"

    containertoken.revoke_container_sync(redis, row.id)
    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE agent_containers SET
                container_id = %(docker_id)s, started_at = %(now)s,
                inflight_runs = 0, status = 'FAILED', error = %(error)s
            WHERE id = %(id)s
            """,
            {
                "id": container_uuid, "docker_id": handle.id, "now": now,
                "error": detail[:2000],
            },
        )
    log.warning("container for agent %s exited immediately: %s", row.agent_id, detail)
    return "FAILED"


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

    同一個排程接著做兩件事，不多養排程：
      · 存活檢查（`_check_liveness`）
      · 常駐 Run 的逾時清掃（`run._sweep_timeouts`）。以前是「每次派工時順手掃」，
        沒有流量就不會掃 —— 被放棄的版本不會再有派工，它的 Run 就永遠卡著。
      · 補派停在 QUEUED 的 Run（`run._redispatch_orphans`）。
    """
    from apps.worker.tasks.run import _redispatch_orphans, _sweep_timeouts

    redis = Redis.from_url(settings.redis_url)
    manager = _manager(redis)
    try:
        result = _sweep_idle(redis, manager)
        # 各自包起來：一件壞掉不該讓其他件跟著不做，但要大聲記下來 ——
        # 吞掉的話容器死了、Run 卡住都沒人知道，那正是這些排程要解決的事。
        try:
            result["liveness"] = _check_liveness(redis, manager)
        except Exception:                          # noqa: BLE001
            log.exception("liveness check failed")
        try:
            result["timed_out_runs"] = _sweep_timeouts(redis)
        except Exception:                          # noqa: BLE001
            log.exception("sweeping timed-out runs failed")
        try:
            result["redispatched_runs"] = _redispatch_orphans()
        except Exception:                          # noqa: BLE001
            log.exception("re-dispatching orphaned runs failed")
        return result
    finally:
        redis.close()


def _sweep_idle(redis: Redis, manager: SandboxManager) -> dict:
    idle_seconds = settings.container_idle_timeout_seconds
    if idle_seconds <= 0:
        return {"swept": 0, "reason": "disabled"}

    cutoff = datetime.now(UTC) - timedelta(seconds=idle_seconds)
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id
            FROM agent_containers c
            WHERE c.status = 'READY'
              AND c.inflight_runs <= 0
              AND COALESCE(
                    (SELECT max(r.created_at) FROM runs r WHERE r.container_id = c.id),
                    c.started_at
                  ) < %(cutoff)s
            """,
            {"cutoff": cutoff},
        ).fetchall()

    swept = []
    for rec in rows:
        container_uuid = rec["id"]
        with db.connection() as conn:
            row = _load_container(conn, container_uuid)
        if row is None or row.status != "READY" or row.inflight_runs > 0:
            continue          # 掃描與停止之間又有工作進來了
        containertoken.revoke_container_sync(redis, row.id)
        _destroy(manager, row.id, row.container_id)
        with db.transaction() as conn:
            updated = conn.execute(
                """
                UPDATE agent_containers SET
                    status = 'STOPPED', container_id = NULL,
                    inflight_runs = 0, error = NULL
                WHERE id = %(id)s AND status = 'READY' AND inflight_runs <= 0
                RETURNING id
                """,
                {"id": container_uuid},
            ).fetchone()
        if updated is None:
            continue
        swept.append(str(container_uuid))
        log.info("stopped idle container %s (agent %s)", container_uuid, row.agent_id)
    return {"swept": len(swept), "containers": swept}


# ── 存活檢查（specs/container-orchestration.md §存活檢查）────────────────
#
# 常駐容器的 restart policy 是 `no`：死掉就停在那裡，Docker 不會拉它。
# 沒有這段的話 DB 會一直寫著 READY，新工作照樣派進它的佇列卻沒人取。

# 自動重啟上限（10 分鐘內 3 次）定義在 apps.api.services.containers，api 判定「放棄」也用同一份。

#: 卡死判定：容器有空位卻這麼久沒來要工作。長輪詢最多 25 秒，90 秒 ≈ 錯過 3 次。
_HUNG_AFTER_SECONDS = 90


def _hung_reason(manager: SandboxManager, row: AgentContainer, now: datetime) -> str | None:
    """行程還在、但卡住了（例如在 `async def` 裡做阻塞呼叫，整個 event loop 停住）→ 回原因。

    **只有「有空位卻不來」才算。** 容器只在有空位時才長輪詢，N 個名額都在跑的容器
    本來就不會來 —— 那是在忙，不是卡死。`inflight_runs` 是派給它、還沒結束的 Run 數，
    它 < N 就表示容器一定有空位、一定該來要工作。
    """
    last_seen = row.last_health_at or row.started_at
    if last_seen is None:
        return None
    silent = (now - last_seen).total_seconds()
    limit = settings.sandbox_runs_per_container
    if silent < _HUNG_AFTER_SECONDS or row.inflight_runs >= limit:
        return None
    logs = manager.driver_logs(row.container_id)
    return (
        f"stopped taking work for {int(silent)}s while it had free slots "
        f"({row.inflight_runs}/{limit} in use) - the process is alive but stuck, "
        f"most likely a blocking call inside async code. Output: {logs.strip()[-1200:]}"
    )


def _app_healthy() -> bool:
    """app 活著嗎。**卡死判定的前提**：app 掛了或在重啟時，所有容器都要不到工作、心跳一起停，
    不先確認的話會把每一個容器都當成卡死砍掉重建。"""
    url = settings.sandbox_gateway_url.rsplit("/internal", 1)[0] + "/healthz"
    try:
        return httpx.get(url, timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def _death_reason(manager: SandboxManager, docker_id: str) -> str | None:
    """容器死了回死因，還在跑回 `None`。

    **問不到 Docker 時當成活著。** 誤判死亡會砍掉跑到一半的 Run，
    晚一分鐘發現只是多等一輪排程 —— 前者比較糟。
    """
    try:
        attrs = manager.driver_inspect(docker_id)
    except Exception as exc:                      # noqa: BLE001
        log.warning("liveness: could not inspect container %s: %s", docker_id, exc)
        return None
    state = attrs.get("State") or {}
    if state.get("Running"):
        return None
    oom = " (OOMKilled: it hit the container memory limit)" if state.get("OOMKilled") else ""
    logs = manager.driver_logs(docker_id)
    return f"exit code {state.get('ExitCode')}{oom}. Output: {logs.strip()[-1200:]}"


def _check_liveness(redis: Redis, manager: SandboxManager) -> dict:
    """找出死掉的常駐容器並處理。

    **每個 READY 的都直接問 Docker，不先看心跳。** 容器只在有空位時才長輪詢，
    N 個名額都在跑的容器也會一直不來 —— 心跳停了分不出在忙還是死了，最後還是要問 Docker。
    而問 Docker 很便宜，直接問就能在一個排程間隔（60 秒）內發現。
    """
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id FROM agent_containers WHERE status = 'READY' AND container_id IS NOT NULL"
        ).fetchall()

    handled: dict[str, str] = {}
    app_ok: bool | None = None            # 需要時才問，一輪最多問一次
    for rec in rows:
        with db.connection() as conn:
            row = _load_container(conn, rec["id"])
        if row is None or row.status != "READY" or not row.container_id:
            continue
        reason = _death_reason(manager, row.container_id)
        if reason is None:
            # 活著 —— 但可能卡死了。卡死跟死掉同樣處理：砍掉重建，也算進重啟上限。
            reason = _hung_reason(manager, row, datetime.now(UTC))
            if reason is not None:
                if app_ok is None:
                    app_ok = _app_healthy()
                if not app_ok:
                    log.warning("liveness: app is unhealthy; not judging container %s as stuck", row.id)
                    reason = None
        if reason is None:
            continue
        outcome = _handle_dead(redis, manager, row, reason)
        if outcome is not None:
            handled[str(row.id)] = outcome
    return {"dead": len(handled), "containers": handled}


def _handle_dead(
    redis: Redis, manager: SandboxManager, row: AgentContainer, reason: str,
) -> str | None:
    """死掉的容器：記死因 → 收掉跑到一半的 Run → 重啟，或超過上限就放棄。回傳最終狀態。"""
    now = datetime.now(UTC)

    # 先搶到它：條件帶上看到的 Docker id，跟同時進來的停止／重啟不會互踩。
    with db.transaction() as conn:
        claimed = conn.execute(
            """
            UPDATE agent_containers SET
                status = 'UNHEALTHY', last_died_at = %(now)s, last_death_reason = %(reason)s
            WHERE id = %(id)s AND status = 'READY' AND container_id = %(docker_id)s
            RETURNING id
            """,
            {"id": row.id, "docker_id": row.container_id, "now": now, "reason": reason[:2000]},
        ).fetchone()
    if claimed is None:
        return None
    log.warning("resident container %s (agent %s) died: %s", row.id, row.agent_id, reason)

    containertoken.revoke_container_sync(redis, row.id)
    _destroy(manager, row.id, row.container_id)

    # 跑到一半的一律失敗，不自動重跑：平台不知道 Agent 做了什麼副作用（例如寄信）。
    with db.connection() as conn:
        running = [rec["id"] for rec in conn.execute(
            "SELECT id FROM runs WHERE container_id = %(cid)s AND status = 'RUNNING'",
            {"cid": row.id},
        ).fetchall()]
    _fail_runs(redis, running, "RUNNING", ErrorCode.SANDBOX_ERROR, True,
               f"the resident container died while running it: {reason}")

    window_start = row.restart_window_started_at
    restarts = row.restarts_in_window
    if window_start is None or (now - window_start).total_seconds() > RESTART_WINDOW_SECONDS:
        window_start, restarts = now, 0

    if restarts >= MAX_RESTARTS_IN_WINDOW:
        with db.transaction() as conn:
            rec = conn.execute(
                f"""
                UPDATE agent_containers SET
                    status = 'FAILED', container_id = NULL, inflight_runs = 0,
                    error = %(error)s
                WHERE id = %(id)s
                RETURNING {AgentContainer.returning()}
                """,
                {"id": row.id, "error": f"gave up: {reason}"[:2000]},
            ).fetchone()
        given_up = AgentContainer.from_row(rec)
        # 佇列裡的每一筆都收掉：放棄之後不會再有人來取。
        # **讀佇列本身，不查 DB** —— DB 分不出「已經推進佇列」和「還在派工途中」的 SCHEDULING，
        # 後者由派工端自己發現版本被放棄。讀和刪在同一個 MULTI，中間不會漏掉新推進來的。
        key = work.queue_key(row.agent_id, row.agent_version_id)
        with redis.pipeline(transaction=True) as pipe:
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            raw_items, _ = pipe.execute()
        queued = []
        for raw in raw_items:
            try:
                queued.append(uuid.UUID(json.loads(raw)["run_id"]))
            except (ValueError, KeyError, TypeError):
                continue
        _fail_runs(redis, queued, "SCHEDULING", ErrorCode.AGENT_NOT_RUNNING, False,
                   given_up_message(given_up))
        log.error("gave up on resident container %s (agent %s); failed %d queued runs",
                  row.id, row.agent_id, len(queued))
        return "FAILED"

    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE agent_containers SET
                status = 'STARTING', container_id = NULL, inflight_runs = 0,
                restart_count = restart_count + 1,
                restart_window_started_at = %(window_start)s,
                restarts_in_window = %(restarts)s
            WHERE id = %(id)s
            """,
            {"id": row.id, "window_start": window_start, "restarts": restarts + 1},
        )
    # 啟動流程會把 inflight_runs 重算成「還掛在它身上、沒結束的 Run 數」，排隊中的那些不會被歸零。
    status = start_container(redis, manager, row.id)
    log.warning("restarted resident container %s (agent %s): %s (restart %d/%d in window)",
                row.id, row.agent_id, status, restarts + 1, MAX_RESTARTS_IN_WINDOW)
    return status


def _fail_runs(
    redis: Redis, run_ids: list[uuid.UUID], status: str,
    code: ErrorCode, retryable: bool, message: str,
) -> int:
    """把這些 Run 收成 FAILED —— 只收**現在仍是 `status`** 的，中間已經結束或被取走的跳過。"""
    from apps.api.enums import RunStatus
    from apps.api.services import runtoken
    from apps.worker.events import lock_run, publish_pending
    from apps.worker.tasks.run import _error, _transition

    failed = 0
    for run_id in run_ids:
        with db.transaction() as conn:
            current = lock_run(conn, run_id)
            if current.status != status:
                continue          # 剛好在這之間結束或被取走了
            _transition(
                conn, run_id, RunStatus.FAILED, terminal=True,
                error=_error(code, message[:2000], str(run_id), retryable),
            )
        publish_pending(redis, run_id)
        runtoken.revoke_run_sync(redis, str(run_id))
        failed += 1
    return failed


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


#: 舊版本被取代後，等綁著它的 Run 跑完的輪詢間隔與上限。
#: Run 的 wall-clock 上限是 15 分鐘，等一小時還沒跑完就是卡住了 —— 那時寧可留著 image，
#: 也不要刪掉一個還有人在用的東西。
_RETIRE_RETRY_SECONDS = 30
_RETIRE_MAX_RETRIES = 120


@celery_app.task(name="zentra.version.retire", bind=True,
                 max_retries=_RETIRE_MAX_RETRIES, default_retry_delay=_RETIRE_RETRY_SECONDS)
def retire_version(self, agent_version_id: str) -> dict:
    """同版號被取代後，收掉舊的那一版（ADR-0016）。

    停舊容器、刪主機上的 image、刪 registry 的 manifest —— 跟刪 Agent 同一套（ADR-0013）。

    ## 兩個不能刪的情況

    - **還有未終結的 Run 綁著它。** Run 在建立時就綁定了版本，排隊中的那些要跑的就是舊 image。
      等它們跑完再收；收的是容器與 image，那一列紀錄永遠留著。
    - **還有別的版本（任何 Agent）用同一個 digest。** 同一個 image 可以被不同 Agent、
      不同版號註冊，刪掉 manifest 會讓那些還在服務的版本拉不回 image。
    """
    vid = uuid.UUID(agent_version_id)
    with db.connection() as conn:
        rec = conn.execute(
            f"SELECT {AgentVersion.returning()} FROM agent_versions WHERE id = %(id)s",
            {"id": vid},
        ).fetchone()
        version = AgentVersion.from_row(rec) if rec is not None else None
        if version is None or version.status != "SUPERSEDED":
            return {"agent_version_id": agent_version_id, "status": "SKIPPED"}
        busy = conn.execute(
            "SELECT count(*) AS n FROM runs WHERE agent_version_id = %(id)s "
            "AND status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED')",
            {"id": vid},
        ).fetchone()["n"]
    if busy:
        try:
            raise self.retry()
        except self.MaxRetriesExceededError:
            log.warning("version %s still has %d unfinished runs; leaving its image in place",
                        agent_version_id, busy)
            return {"agent_version_id": agent_version_id, "status": "GAVE_UP", "busy_runs": busy}

    with db.connection() as conn:
        containers = [
            AgentContainer.from_row(r) for r in conn.execute(
                f"SELECT {AgentContainer.returning()} FROM agent_containers "
                "WHERE agent_version_id = %(id)s AND status IN ('STARTING', 'READY', 'UNHEALTHY')",
                {"id": vid},
            ).fetchall()
        ]
        shared = conn.execute(
            "SELECT count(*) AS n FROM agent_versions "
            "WHERE image_digest = %(digest)s AND status <> 'SUPERSEDED'",
            {"digest": version.image_digest},
        ).fetchone()["n"]

    redis = Redis.from_url(settings.redis_url)
    manager = _manager(redis)
    notes = []
    try:
        notes.extend(_stop_containers(redis, manager, containers))

        if shared:
            notes.append(f"{version.image_ref}: 還有 {shared} 個版本用同一個 digest，不刪")
        else:
            notes.append(_delete_from_registry(version.image_ref))
            try:
                manager.driver.remove_image(version.image_ref)
                notes.append(f"{version.image_ref}: 主機上的 image 已移除")
            except Exception as exc:              # noqa: BLE001
                notes.append(f"{version.image_ref}: 主機上的 image 沒移除（{type(exc).__name__}）")
    finally:
        redis.close()

    log.info("retired version %s: %s", agent_version_id, "; ".join(notes))
    return {"agent_version_id": agent_version_id, "status": "RETIRED", "notes": notes}


def _stop_containers(redis: Redis, manager: SandboxManager, rows: list[AgentContainer]) -> list[str]:
    """撤銷 token → 銷毀 → 標 STOPPED。每個獨立處理，一個失敗不擋其他的。"""
    notes = []
    for row in rows:
        try:
            containertoken.revoke_container_sync(redis, row.id)
            _destroy(manager, row.id, row.container_id)
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE agent_containers SET container_id = NULL, status = 'STOPPED', "
                    "inflight_runs = 0 WHERE id = %(id)s",
                    {"id": row.id},
                )
            notes.append(f"容器 {row.id} 已移除")
        except Exception as exc:              # noqa: BLE001
            notes.append(f"容器 {row.id} 移除失敗：{type(exc).__name__}")
    return notes


@celery_app.task(name="zentra.container.drain_other_versions", bind=True,
                 max_retries=_RETIRE_MAX_RETRIES, default_retry_delay=_RETIRE_RETRY_SECONDS)
def drain_other_versions(self, agent_id: str, keep_version_id: str) -> dict:
    """新版本成為最新版之後，停掉同一個 Agent **其他版本**的常駐容器。

    一個 Agent 平常只該有一個常駐容器。換版之後新請求都走新版本的容器，
    舊容器只剩手上的 Run —— 等它們跑完就停，不等閒置回收的 30 分鐘。

    只停容器，**不刪 image**：舊版本仍是 PUBLISHED，指定舊版號的呼叫照樣能跑
    （會再拉起它的容器，閒置後被回收）。被同版號取代的版本由 retire_version 收 image。
    """
    with db.connection() as conn:
        containers = [
            AgentContainer.from_row(r) for r in conn.execute(
                f"SELECT {AgentContainer.returning()} FROM agent_containers "
                "WHERE agent_id = %(agent)s AND agent_version_id <> %(keep)s "
                "AND status IN ('STARTING', 'READY', 'UNHEALTHY')",
                {"agent": uuid.UUID(agent_id), "keep": uuid.UUID(keep_version_id)},
            ).fetchall()
        ]
        if not containers:
            return {"agent_id": agent_id, "status": "NOTHING_TO_DRAIN"}
        busy = conn.execute(
            "SELECT count(*) AS n FROM runs WHERE agent_version_id = ANY(%(ids)s) "
            "AND status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED')",
            {"ids": list({c.agent_version_id for c in containers})},
        ).fetchone()["n"]
    if busy:
        try:
            raise self.retry()
        except self.MaxRetriesExceededError:
            log.warning("agent %s: old-version containers still have %d unfinished runs; "
                        "leaving them to idle reclamation", agent_id, busy)
            return {"agent_id": agent_id, "status": "GAVE_UP", "busy_runs": busy}

    redis = Redis.from_url(settings.redis_url)
    try:
        notes = _stop_containers(redis, _manager(redis), containers)
    finally:
        redis.close()
    log.info("drained old-version containers of agent %s: %s", agent_id, "; ".join(notes))
    return {"agent_id": agent_id, "status": "DRAINED", "notes": notes}
