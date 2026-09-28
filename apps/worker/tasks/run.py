"""runner-worker —— 一次 Run 就是一個 Celery task（ADR-0002）。

╔══════════════════════════════════════════════════════════════════════════╗
║ P1：這個檔案**永遠不得 import 或 exec 使用者程式碼**。                      ║
║ 它只做四件事：申請沙盒 → 注入 run token → 等它結束 → 回寫狀態。             ║
║ 連「檢查 build() 在不在」都不行 —— import 使用者模組就是執行使用者程式碼。   ║
║ 要驗證：`grep -rn "apps.sandbox.runtime" apps/worker/` 必須沒有結果。       ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import logging
import time
import uuid
from datetime import UTC, datetime

import docker
from redis import Redis
from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import NoResultFound

from apps.api.config import get_settings
from apps.api.ids import uuid7
from apps.api.enums import BUILTIN_AGENT_VERSION_ID, AgentVersionStatus, ErrorCode, RunStatus
from apps.api.models import AgentContainer, AgentLlmCredential, AgentVersion, Run
from apps.api.services import agent_credentials, runtoken, work
from apps.api.services import events as async_events
from apps.sandbox.drivers.base import SandboxHandle, SandboxPlatformError
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


def _sweep_timeouts(session: Session, redis: Redis) -> int:
    """把逾時的常駐 Run 收成 FAILED（ZEN-015 / spec §5.3）。

    一次性沙盒的逾時是 `driver.wait(timeout=…)` 抓的 —— 容器被殺、任務收尾。
    **常駐容器不會被殺**，所以那個機制不存在：沙盒裡的 Run 若卡住或崩潰而沒回報，
    這一列會永遠停在 RUNNING，而且畫面上看不出哪裡不對。

    這裡刻意在「每次派工時順手掃」而不是開 celery beat ——
    多一個排程服務要維運，而派工本來就會發生，掃描成本又極低。
    代價誠實講：**沒有流量時不會掃**，所以最後幾筆可能停在 RUNNING 直到下次派工。

    對「`async def` 裡阻塞」那種情況這條也救不了執行緒（ADR-0012 已寫明），
    它只是讓**帳面上**的狀態誠實。
    """
    from datetime import timedelta

    now = datetime.now(UTC)
    # 兩種都要掃：
    #   container_id 有值 → 某個容器取走了但沒回報（卡住或崩潰）
    #   兩個 id 都沒有   → 排進佇列後沒有任何容器取走（全滿且一直沒空出來）
    # 一次性沙盒不會落入第二種：它一定有 sandbox_id，而且逾時由 driver.wait 抓。
    stale = session.execute(
        select(Run).where(
            Run.status == RunStatus.RUNNING,
            Run.started_at.isnot(None),
            or_(
                Run.container_id.isnot(None),
                and_(Run.container_id.is_(None), Run.sandbox_id.is_(None)),
            ),
        ).limit(50)
    ).scalars().all()

    swept = 0
    for run in stale:
        deadline = run.started_at + timedelta(seconds=run.timeout_seconds)
        if now < deadline:
            continue
        _transition(
            session, redis, run.id, RunStatus.FAILED, terminal=True,
            error=_error(
                ErrorCode.SANDBOX_TIMEOUT,
                f"the run exceeded its {run.timeout_seconds}s limit inside a resident "
                "container and never reported a result",
                str(run.id), True,
            ),
        )
        runtoken.revoke_run_sync(redis, str(run.id))
        if run.container_id:
            session.execute(
                update(AgentContainer)
                .where(AgentContainer.id == run.container_id,
                       AgentContainer.inflight_runs > 0)
                .values(inflight_runs=AgentContainer.inflight_runs - 1)
            )
        swept += 1
    if swept:
        session.commit()
        log.warning("swept %d timed-out resident runs", swept)
    return swept


@celery_app.task(name="zentra.run.finalize", bind=True, max_retries=0)
def finalize_run(self, run_id: str) -> dict:
    """常駐容器回寫結果後，由 worker 決定終態（ZEN-015）。

    一次性沙盒的終態是 `_finish_sync` 在容器退出後決定的。常駐容器不會退出，
    所以那個訊號不存在 —— 改由「結果進來了」觸發這個任務。

    **終態的裁決權仍在 worker**（契約 §4：`/result` 只保存，不轉狀態）。
    Gateway 收到結果就自己轉狀態的話，沙盒等於能宣告自己成功了。
    """
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    with SessionSync() as session:
        rid = uuid.UUID(run_id)
        run = session.get(Run, rid)
        if run is None or RunStatus(run.status).is_terminal:
            return {"run_id": run_id, "status": run.status if run else "NOT_FOUND"}

        # 容器把結果寫進來了，所以 exit code 用 0 或 1 表示「它說成功／失敗」。
        # 判斷仍然看 run.error 有沒有東西 —— 那是沙盒回報的內容，不是它的主張。
        exit_code = 1 if run.error else 0
        result = _finish_sync(session, redis, rid, exit_code, None)

        # 常駐路徑的 token 是在這裡撤銷的 —— execute_run 那邊不能撤，
        # 因為它返回時容器才正要用（P4：跑完立即撤銷，只是「跑完」的時機不同）。
        runtoken.revoke_run_sync(redis, run_id)

        # 讓出容器的名額。**一定要做** —— 不減的話 inflight_runs 只增不減，
        # 容器很快就會被當成滿載而所有 Run 都退回一次性沙盒。
        if run.container_id:
            session.execute(
                update(AgentContainer)
                .where(AgentContainer.id == run.container_id,
                       AgentContainer.inflight_runs > 0)
                .values(inflight_runs=AgentContainer.inflight_runs - 1)
            )
            session.commit()
        return result


def _default_model(session: Session, agent_id) -> str:
    """這個 Agent 的預設 model，送進常駐容器的工作項（ZEN-015 / ZEN-012）。

    **每個工作項都重算一次，不在容器啟動時決定。** 憑證可以中途被改
    （ADR-0011：改了所有版本立刻一起變），而容器可能已經跑了好幾天 ——
    啟動時算一次會讓那個承諾在常駐模式下悄悄失效。

    只查 model 名稱，不碰密文。
    """
    row = session.execute(
        select(AgentLlmCredential.default_model)
        .where(AgentLlmCredential.agent_id == agent_id)
    ).scalar_one_or_none()
    return agent_credentials.pick_default_model(row)


def _ready_container(session: Session, agent_id, version_id) -> AgentContainer | None:
    """這個 Agent 有沒有還有空位的 READY 容器（ZEN-015）。

    **必須同時比對版本** —— 容器綁定單一 `agent_version_id`（P5）。
    發新版之後舊容器還在跑舊版本，把新版的 Run 派進去會執行到錯的程式碼。

    滿載（`inflight_runs >= N`）的容器不算。回 `None` 不代表沒有容器 ——
    可能是全都滿了，那是 `_live_containers` 要回答的問題（溢出，見 `_overflow`）。
    """
    limit = settings.sandbox_runs_per_container
    row = session.execute(
        select(AgentContainer)
        .where(
            AgentContainer.agent_id == agent_id,
            AgentContainer.agent_version_id == version_id,
            AgentContainer.status == "READY",
            AgentContainer.inflight_runs < limit,
        )
        .order_by(AgentContainer.inflight_runs.asc())
        .with_for_update()
        .limit(1)
    ).scalar_one_or_none()
    return row


def _claim_container_slot(session: Session, agent_id, version) -> tuple[AgentContainer | None, bool]:
    """在同一個交易裡決定「誰去啟動容器」。回傳 `(容器列, 是不是我要去啟動)`。

    ## 為什麼要鎖

    十個請求同時打進一個還沒啟動的 Agent，十個 worker 會同時發現「沒有容器」。
    沒有鎖的話它們會開十個容器 —— 而這台機器開兩個就滿了。

    用 advisory lock 而不是唯一索引，是因為**鎖只需要罩住「檢查加寫入」那一瞬間**，
    不該罩住 image pull（那要幾十秒）。交易一結束鎖就放掉，真正的啟動在鎖外面做。

    鎖的 key 用 `agent_version_id`：容器綁定單一版本（P5），所以「該不該再開一個」
    本來就是 per-version 的問題。
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": str(version.id)},
    )

    # 拿到鎖之後**要重查一次** —— 等鎖的期間別人可能已經啟動好了。
    ready = _ready_container(session, agent_id, version.id)
    if ready is not None:
        session.commit()
        return ready, False

    starting = session.execute(
        select(AgentContainer).where(
            AgentContainer.agent_id == agent_id,
            AgentContainer.agent_version_id == version.id,
            AgentContainer.status == "STARTING",
        ).limit(1)
    ).scalar_one_or_none()
    if starting is not None:
        # 別人正在啟動。我只要等它好。
        session.commit()
        return starting, False

    row = AgentContainer(
        id=uuid7(), agent_id=agent_id, agent_version_id=version.id, status="STARTING",
    )
    session.add(row)
    session.commit()
    return row, True


def _live_containers(session: Session, agent_id, version_id) -> list[AgentContainer]:
    """這個版本目前**在服務**的容器，不管滿不滿。

    跟 `_ready_container` 的差別就是容量條件 —— 有它才分得出
    「一個容器都沒有」（要冷啟動，得等）與「都滿了」（排隊就好，不用等）。
    """
    return list(session.execute(
        select(AgentContainer).where(
            AgentContainer.agent_id == agent_id,
            AgentContainer.agent_version_id == version_id,
            AgentContainer.status.in_(("READY", "STARTING")),
        )
    ).scalars())


def _overflow(session: Session, agent_id, version) -> None:
    """全滿了就在**背景**再開一個容器（ADR-0012 選項 C 的後半）。

    ## 為什麼不等它

    冷啟動要等，是因為沒有容器就沒人能接這筆工作。溢出不一樣：
    **工作已經排進佇列了**，現有容器一空出位子就會拿走它。
    再讓這筆 Run 去等一個全新容器（含 image pull）只是無謂地加幾十秒延遲。
    新容器是為了**後面**的請求擴容，不是為了這一筆。

    所以這裡把啟動丟給 `q.build`（跟使用者按按鈕走同一條路），立刻返回。

    ## 為什麼有上限

    沒有上限的話，一波尖峰會讓平台一直開容器，每個 2 GB，直到主機被 OOM killer
    清場 —— 那比讓請求排隊糟得多。排隊只是慢，OOM 是整台機器上的東西一起死。
    """
    from apps.api.tasks import dispatch_container_start

    cap = settings.sandbox_max_containers_per_agent
    live = _live_containers(session, agent_id, version.id)
    if len(live) >= cap:
        log.info("agent %s is at its container cap (%d); queueing instead", agent_id, cap)
        return
    if any(row.status == "STARTING" for row in live):
        return          # 已經有一個在路上了，不要一筆工作開一個容器

    container, mine = _claim_container_slot(session, agent_id, version)
    if container is None or not mine:
        return
    log.info("agent %s containers are full; starting #%d in the background",
             agent_id, len(live) + 1)
    dispatch_container_start(str(container.id))


def _wait_ready(session: Session, container_uuid, deadline: float) -> AgentContainer | None:
    """等某個容器變成 READY。逾時或 FAILED 回 `None`。

    輪詢 DB 而不是等 Celery 的結果：啟動可能是**別的 worker** 發動的，
    那個 AsyncResult 我拿不到。DB 那一列才是所有人都看得到的真相。
    """
    while time.monotonic() < deadline:
        session.expire_all()
        row = session.get(AgentContainer, container_uuid)
        if row is None or row.status == "FAILED":
            return None
        if row.status == "READY":
            return row
        time.sleep(0.5)
    return None


def _ensure_container(
    session: Session, redis: Redis, agent_id, version,
) -> AgentContainer | None:
    """沒有可用的常駐容器就開一個，並**等它起來**（ZEN-015）。

    第一筆 Run 會付出啟動成本（含 image pull），之後的 Run 直接進熱容器。
    使用者可以先按「啟動」把這筆成本挪到自己方便的時候，但不按也能用。

    **起不來就回 `None`，由呼叫端退回一次性沙盒。** 常駐化是效能手段，
    它壞掉不該讓整個平台不能跑 —— 舊路徑留著當安全網。
    """
    from apps.worker.tasks.container import start_container

    container, mine = _claim_container_slot(session, agent_id, version)
    if container is None:
        return None
    if container.status == "READY":
        return container

    budget = float(settings.container_start_timeout_seconds)
    deadline = time.monotonic() + budget
    if mine:
        log.info("no resident container for agent %s; starting one inline", agent_id)
        manager = _manager(redis)
        try:
            status = start_container(session, redis, manager, container)
        except Exception:                          # noqa: BLE001
            log.exception("inline container start failed for agent %s", agent_id)
            session.rollback()
            return None
        if status != "READY":
            return None
        return container
    return _wait_ready(session, container.id, deadline)


@celery_app.task(name="zentra.run.execute", bind=True, acks_late=True)
def execute_run(self, run_id: str) -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    manager = _manager(redis)
    session: Session = SessionSync()

    handle: SandboxHandle | None = None
    token: str | None = None
    reserved = False
    # 常駐容器（ZEN-015）：任務把工作丟進佇列就返回，但容器**才正要用那把 token**。
    # 一次性沙盒是「任務結束 = 沙盒結束」，所以 finally 撤銷是對的；
    # 常駐路徑不是。撤銷改由 finalize_run 在終態時做。
    # 實測踩過：不區分的話容器回寫事件會 401，而錯誤只出現在容器日誌裡。
    dispatched_to_container = False

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

        version = session.get(AgentVersion, run.agent_version_id)
        if version is None or version.status != AgentVersionStatus.PUBLISHED:
            _transition(
                session, redis, rid, RunStatus.FAILED, terminal=True,
                error=_error(
                    ErrorCode.SANDBOX_ERROR, "published image metadata is unavailable", run_id, True
                ),
            )
            return {"run_id": run_id, "status": str(RunStatus.FAILED)}

        # ── ZEN-015：有常駐容器就派進去，沒有才開一次性沙盒 ──────────
        #
        # 這是新舊兩條路的分岔點。**沒啟動容器的 Agent 行為完全不變** ——
        # 舊 image（沒有 serve 模式）也照樣能跑，因為它們走的還是下面那條。
        # 順手掃逾時的常駐 Run。放在這裡是為了不用多養一個 celery beat ——
        # 代價是沒有流量時不會掃（見 _sweep_timeouts 的說明）。
        try:
            _sweep_timeouts(session, redis)
        except Exception:                          # noqa: BLE001
            # 掃描失敗不該影響這次派工。
            log.exception("sweeping timed-out runs failed")
            session.rollback()

        container = _ready_container(session, run.agent_id, version.id)
        #: True = 全部容器都滿了，這筆只是排進佇列，還沒有歸屬到任何容器。
        queued_only = False
        if container is None and str(version.id) != BUILTIN_AGENT_VERSION_ID:
            # ZEN-000 的內建 fixture 沒有 serve 模式，跳過它（ZEN-010 會退休它）。
            #
            # 「沒有可用容器」有兩種，處理方式相反：
            #   全滿      → 工作排進佇列就好，先空出來的容器會拿走；背景加開下一個
            #   一個都沒有 → 沒人能接，只能等一個起來（冷啟動）
            serving = [
                row for row in _live_containers(session, run.agent_id, version.id)
                if row.status == "READY"
            ]
            if serving:
                _overflow(session, run.agent_id, version)
                # **不預留給任何容器。** 全都滿了，硬記在誰頭上都會讓
                # `inflight_runs` 超過 N，畫面上變成「2 / 1」那種看起來像超收的數字。
                # 這筆工作就是在排隊 —— 等某個容器空出來取走它，
                # 那時 take_work 會把它記到真正執行的容器上。
                queued_only = True
                container = serving[0]
            else:
                container = _ensure_container(session, redis, run.agent_id, version)
        if container is not None:
            item = {
                "run_id": run_id,
                "run_token": token,                # ← P4：token 跟著工作項走
                "entrypoint": run.entrypoint,
                "input": run.input,
                "manifest": version.manifest or {},
                "default_model": _default_model(session, run.agent_id),
            }
            if not queued_only:
                # 有空位才預留。`_ready_container` 只挑 inflight < N 的，
                # 所以加一之後仍然不會超過 N。
                container.inflight_runs += 1
            _transition(session, redis, rid, RunStatus.RUNNING,
                        started_at=datetime.now(UTC))
            if not queued_only:
                session.execute(
                    update(Run).where(Run.id == rid).values(container_id=container.id)
                )
            session.commit()

            # **推工作一定要在 commit 之後。** 反過來的話，容器可能在 DB 寫完之前
            # 就取走這筆工作並做歸屬校正 —— 那時它看到的 `run.container_id` 還是空的，
            # 於是它加一次、派工端再加一次，`inflight_runs` 變成實際的兩倍。
            # 實測踩過：一筆 Run 跑完，容器顯示 inflight=2。
            #
            # 反向的風險是「commit 了但沒推出去」（行程正好在這裡死掉），
            # 那筆 Run 會停在 RUNNING 直到 _sweep_timeouts 收掉它 —— 慢，但帳是對的。
            work.push_sync(redis, run.agent_id, item)
            # **不等結果就返回。** 常駐容器同時跑 N 個 Run，而 Celery worker 只有
            # 十個行程 —— 在這裡等會讓 Celery 變成新的瓶頸，N 就白設了。
            # 結果由容器自己回寫 /internal/runs/{id}/result，終態由 finalize_run 裁決。
            manager.release(run_id)
            reserved = False
            dispatched_to_container = True
            return {"run_id": run_id, "status": str(RunStatus.RUNNING), "dispatched": True}

        # Keep the ZEN-000 fixture runnable during the migration. Every user
        # registered version uses the exact DB-resolved digest path.
        if str(version.id) == BUILTIN_AGENT_VERSION_ID:
            handle = manager.create(run_id, token)
        else:
            handle = manager.create_run(run_id, token, version.image_ref)
        run = _transition(session, redis, rid, RunStatus.RUNNING,
                          sandbox_id=handle.id, started_at=datetime.now(UTC))

        timeout = run.timeout_seconds
        result = manager.driver.wait(handle, timeout=timeout)

        # 沙盒已經透過 Internal Gateway 回寫了 output / error，重讀一次拿最新值
        session.expire_all()
        run = session.get(Run, rid)

        return _finish_sync(session, redis, rid, result.exit_code, result.logs,
                            timed_out=result.timed_out)

    except SandboxPlatformError as exc:
        log.exception("run %s sandbox platform failure", run_id)
        session.rollback()
        try:
            current = session.get(Run, uuid.UUID(run_id))
            if current and not RunStatus(current.status).is_terminal:
                _transition(session, redis, current.id, RunStatus.FAILED, terminal=True,
                            error=_error(ErrorCode.SANDBOX_ERROR, str(exc), run_id, True))
        except Exception:
            log.exception("failed to mark run %s SANDBOX_ERROR", run_id)
        return {"run_id": run_id, "status": str(RunStatus.FAILED)}

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
        if token and not dispatched_to_container:
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
