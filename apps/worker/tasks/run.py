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

from dataclasses import replace

import docker
from redis import Redis
from apps.api.config import get_settings
from apps.api.ids import uuid7
from apps.api.enums import BUILTIN_AGENT_VERSION_ID, AgentVersionStatus, ErrorCode, RunStatus
from apps.api.rows import AgentContainer, AgentVersion
from apps.api.rows import Run as RunRow
from apps.api.services import agent_credentials, runtoken, work
from apps.api.services import events as async_events
from apps.sandbox.drivers.base import SandboxHandle, SandboxPlatformError
from apps.sandbox.drivers.docker_driver import DockerSandboxDriver
from apps.sandbox.manager import QuotaExceeded, SandboxManager
from apps.worker.celery_app import celery_app
from apps.worker import db as worker_db
from apps.worker.events import RunNotFound, add_status_event, lock_run, publish_pending
from psycopg.types.json import Jsonb

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


def _jsonb(value):
    return None if value is None else Jsonb(value)


def _transition(
    conn, run_id: uuid.UUID, status: RunStatus,
    *, terminal: bool = False, error: dict | None = None, **fields,
) -> RunRow:
    """狀態 + 它的 run.status 事件在同一個 transaction 裡寫入。

    呼叫端在交易結束後才 `publish_pending`。反過來的話 Redis 發出去了但
    commit 失敗，消費端會看到一個資料庫裡不存在的事件。
    """
    run = lock_run(conn, run_id)
    finished_at = datetime.now(UTC) if terminal else run.finished_at
    next_error = error if error is not None else run.error
    sandbox_id = fields.get("sandbox_id", run.sandbox_id)
    started_at = fields.get("started_at", run.started_at)
    celery_task_id = fields.get("celery_task_id", run.celery_task_id)
    container_id = fields.get("container_id", run.container_id)
    rec = conn.execute(
        f"""
        UPDATE runs SET
            status = %(status)s, error = %(error)s, finished_at = %(finished_at)s,
            sandbox_id = %(sandbox_id)s, started_at = %(started_at)s,
            celery_task_id = %(celery_task_id)s, container_id = %(container_id)s
        WHERE id = %(id)s
        RETURNING {RunRow.returning()}
        """,
        {
            "id": run_id,
            "status": str(status),
            "error": _jsonb(next_error),
            "finished_at": finished_at,
            "sandbox_id": sandbox_id,
            "started_at": started_at,
            "celery_task_id": celery_task_id,
            "container_id": container_id,
        },
    ).fetchone()
    run = RunRow.from_row(rec)
    add_status_event(conn, run, terminal=terminal)
    return run


def _error(code: ErrorCode, message: str, run_id: str, retryable: bool) -> dict:
    return {"code": str(code), "message": message, "run_id": run_id, "retryable": retryable}


def _claim_run(
    conn, run_id: uuid.UUID, task_id: str | None,
) -> tuple[RunRow | None, bool]:
    """Acquire the single execution right for a Run.

    The lock and status recheck must happen immediately before reserving a
    sandbox. A duplicate Celery delivery can otherwise observe the old
    ``QUEUED`` value and create a second container while the first delivery is
    already running.

    Redis 發布由呼叫端在交易結束後做。
    """
    try:
        run = lock_run(conn, run_id)
    except RunNotFound:
        return None, False
    if RunStatus(run.status).is_terminal:
        return run, False
    if RunStatus(run.status) is not RunStatus.QUEUED:
        return run, False
    run = _transition(conn, run_id, RunStatus.SCHEDULING, celery_task_id=task_id)
    return run, True


def _finish_sync(
    conn,
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

    Redis 發布由呼叫端在交易結束後做。
    """
    run = lock_run(conn, run_id)
    if RunStatus(run.status).is_terminal:
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

    run = _transition(conn, run_id, status, terminal=True, error=error)
    return {"run_id": str(run_id), "status": str(run.status)}


async def finish(
    run_id: uuid.UUID,
    exit_code: int,
    logs: str | None,
    *,
    timed_out: bool = False,
) -> dict:
    """Async DB equivalent used by Gateway integration tests and async callers.

    Keeping this small adapter beside the sync Celery implementation lets the
    worker remain synchronous while preserving the exact terminal-state rules
    for async verification code. Frozen `rows.Run` cannot be mutated.
    """
    from apps.api import db

    async with db.transaction() as conn:
        run = await async_events.lock_run(conn, run_id)
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

        finished_at = datetime.now(UTC)
        next_error = error if error is not None else run.error
        await conn.execute(
            """
            UPDATE runs
               SET status = $2, error = $3, finished_at = $4
             WHERE id = $1
            """,
            run_id, str(status), next_error, finished_at,
        )
        run = replace(run, status=str(status), error=next_error, finished_at=finished_at)
        await async_events.status_event(conn, run, terminal=True)
        return {"run_id": str(run_id), "status": str(run.status)}


def _sweep_timeouts(redis: Redis) -> int:
    """把逾時的常駐 Run 收成 FAILED（ZEN-015 / spec §5.3）。

    一次性沙盒的逾時是 `driver.wait(timeout=…)` 抓的 —— 容器被殺、任務收尾。
    **常駐容器不會被殺**，所以那個機制不存在：沙盒裡的 Run 若卡住或崩潰而沒回報，
    這一列會永遠停在 RUNNING，而且畫面上看不出哪裡不對。

    由 worker 的 60 秒排程（`zentra.container.sweep_idle`）呼叫。以前是「每次派工時順手掃」，
    代價是沒有流量就不會掃 —— 而被存活檢查放棄的版本不會再有派工，它的 Run 就永遠卡著。
    排程本來就為了閒置回收存在，搬過去不用多養東西。

    對「`async def` 裡阻塞」那種情況這條也救不了執行緒（ADR-0012 已寫明），
    它只是讓**帳面上**的狀態誠實。
    """
    from datetime import timedelta

    now = datetime.now(UTC)
    # 兩種都要掃：
    #   container_id 有值 → 某個容器取走了但沒回報（卡住或崩潰）
    #   兩個 id 都沒有   → 排進佇列後沒有任何容器取走（全滿且一直沒空出來）
    # 一次性沙盒不會落入第二種：它一定有 sandbox_id，而且逾時由 driver.wait 抓。
    with worker_db.connection() as conn:
        stale = conn.execute(
            f"""
            SELECT {RunRow.returning()} FROM runs
            WHERE status = %(status)s
              AND started_at IS NOT NULL
              AND (container_id IS NOT NULL
                   OR (container_id IS NULL AND sandbox_id IS NULL))
            LIMIT 50
            """,
            {"status": str(RunStatus.RUNNING)},
        ).fetchall()

    swept = _sweep_queue_timeouts(redis, now)
    for rec in stale:
        run = RunRow.from_row(rec)
        deadline = run.started_at + timedelta(seconds=run.timeout_seconds)
        if now < deadline:
            continue
        with worker_db.transaction() as conn:
            _transition(
                conn, run.id, RunStatus.FAILED, terminal=True,
                error=_error(
                    ErrorCode.SANDBOX_TIMEOUT,
                    f"the run exceeded its {run.timeout_seconds}s limit inside a resident "
                    "container and never reported a result",
                    str(run.id), True,
                ),
            )
        publish_pending(redis, run.id)
        runtoken.revoke_run_sync(redis, str(run.id))
        if run.container_id:
            with worker_db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE agent_containers
                       SET inflight_runs = inflight_runs - 1
                     WHERE id = %(id)s AND inflight_runs > 0
                    """,
                    {"id": run.container_id},
                )
        swept += 1
    if swept:
        log.warning("swept %d timed-out resident runs", swept)
    return swept


def _sweep_queue_timeouts(redis: Redis, now: datetime) -> int:
    """在常駐容器佇列裡等太久、一直沒被取走的 Run 收成 FAILED。

    排隊不算進 Run 的逾時（容器取走才轉 RUNNING 開始計時），所以要另一條上限：
    容器死掉時佇列裡的工作永遠不會被取走，不收的話它們會永遠停在 SCHEDULING。
    佇列裡留著的工作項之後被取到時，take_work 看到 Run 已終結會直接丟掉。
    """
    from datetime import timedelta

    cutoff = now - timedelta(seconds=settings.run_queue_timeout_seconds)
    with worker_db.connection() as conn:
        stale = conn.execute(
            f"""
            SELECT {RunRow.returning()} FROM runs
            WHERE status = %(status)s AND started_at IS NULL AND created_at < %(cutoff)s
            LIMIT 50
            """,
            {"status": str(RunStatus.SCHEDULING), "cutoff": cutoff},
        ).fetchall()
    swept = 0
    for rec in stale:
        run = RunRow.from_row(rec)
        with worker_db.transaction() as conn:
            current = lock_run(conn, run.id)
            if current.status != RunStatus.SCHEDULING or current.started_at is not None:
                continue
            _transition(
                conn, run.id, RunStatus.FAILED, terminal=True,
                error=_error(
                    ErrorCode.SANDBOX_TIMEOUT,
                    f"the run waited in the queue for over {settings.run_queue_timeout_seconds}s "
                    "without a free container slot",
                    str(run.id), True,
                ),
            )
            if current.container_id:
                conn.execute(
                    "UPDATE agent_containers SET inflight_runs = inflight_runs - 1 "
                    "WHERE id = %(id)s AND inflight_runs > 0",
                    {"id": current.container_id},
                )
        publish_pending(redis, run.id)
        runtoken.revoke_run_sync(redis, str(run.id))
        swept += 1
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
    rid = uuid.UUID(run_id)
    with worker_db.connection() as conn:
        rec = conn.execute(
            f"SELECT {RunRow.returning()} FROM runs WHERE id = %(id)s",
            {"id": rid},
        ).fetchone()
    if rec is None:
        return {"run_id": run_id, "status": "NOT_FOUND"}
    run = RunRow.from_row(rec)
    if RunStatus(run.status).is_terminal:
        return {"run_id": run_id, "status": run.status}

    # 容器把結果寫進來了，所以 exit code 用 0 或 1 表示「它說成功／失敗」。
    # 判斷仍然看 run.error 有沒有東西 —— 那是沙盒回報的內容，不是它的主張。
    exit_code = 1 if run.error else 0
    with worker_db.transaction() as conn:
        result = _finish_sync(conn, rid, exit_code, None)
    publish_pending(redis, rid)

    # 常駐路徑的 token 是在這裡撤銷的 —— execute_run 那邊不能撤，
    # 因為它返回時容器才正要用（P4：跑完立即撤銷，只是「跑完」的時機不同）。
    runtoken.revoke_run_sync(redis, run_id)

    # 讓出容器的名額。**一定要做** —— 不減的話 inflight_runs 只增不減，
    # 容器很快就會被當成滿載而所有 Run 都退回一次性沙盒。
    if run.container_id:
        with worker_db.transaction() as conn:
            conn.execute(
                """
                UPDATE agent_containers
                   SET inflight_runs = inflight_runs - 1
                 WHERE id = %(id)s AND inflight_runs > 0
                """,
                {"id": run.container_id},
            )
    return result


def _default_model(agent_id) -> str:
    """這個 Agent 的預設 model，送進常駐容器的工作項（ZEN-015 / ZEN-012）。

    **每個工作項都重算一次，不在容器啟動時決定。** 憑證可以中途被改
    （ADR-0011：改了所有版本立刻一起變），而容器可能已經跑了好幾天 ——
    啟動時算一次會讓那個承諾在常駐模式下悄悄失效。

    只查 model 名稱，不碰密文。
    """
    with worker_db.connection() as conn:
        row = conn.execute(
            "SELECT default_model FROM agent_llm_credentials WHERE agent_id = %(id)s",
            {"id": agent_id},
        ).fetchone()
    return agent_credentials.pick_default_model(row["default_model"] if row else None)


def _agent_config(agent_id) -> dict:
    """這個 Agent 的可變設定，送進常駐容器的工作項（ZEN-019 / 契約 §4.1）。

    **每個工作項都重算一次**，跟 `_default_model` 同一條規則：設定可以中途被改
    （改了所有版本立刻一起變），而容器可能已經跑了好幾天 ——
    啟動時算一次會讓那個承諾在常駐模式下悄悄失效。

    ⚠️ **這裡漏掉就等於整個功能沒做。** 常駐容器是預設路徑，只改 bootstrap 那條
    的話畫面上看起來跟「設定沒存進去」一模一樣。ZEN-012 的 `default_model` 與
    ZEN-017 的 `ctx.llm()` 都踩過同一個坑。
    """
    with worker_db.connection() as conn:
        row = conn.execute(
            "SELECT config FROM agent_settings WHERE agent_id = %(id)s",
            {"id": agent_id},
        ).fetchone()
    return dict((row["config"] if row else None) or {})


def _secret_names(agent_id) -> list[str]:
    """機密的**名字**，排序過。沒有值、沒有長度、沒有尾四碼（契約 §4.1 第 5 點）。

    給名字是為了讓 agent 能 fail fast 說出「缺 SMTP_PASSWORD」，
    而不是跑完整段分析才在最後一步失敗。
    """
    with worker_db.connection() as conn:
        rows = conn.execute(
            "SELECT name FROM agent_secrets WHERE agent_id = %(id)s ORDER BY name",
            {"id": agent_id},
        ).fetchall()
    return [row["name"] for row in rows]


def _ready_container(conn, agent_id, version_id) -> AgentContainer | None:
    """這個 Agent 有沒有還有空位的 READY 容器（ZEN-015）。

    **必須同時比對版本** —— 容器綁定單一 `agent_version_id`（P5）。
    發新版之後舊容器還在跑舊版本，把新版的 Run 派進去會執行到錯的程式碼。

    滿載（`inflight_runs >= N`）的容器不算。回 `None` 不代表沒有容器 ——
    可能是滿了，那是 `_live_containers` 要回答的問題。
    """
    worker_db.require_transaction(conn)
    rec = conn.execute(
        f"""
        SELECT {AgentContainer.returning()} FROM agent_containers
        WHERE agent_id = %(agent_id)s
          AND agent_version_id = %(version_id)s
          AND status = 'READY'
          AND inflight_runs < %(limit)s
        ORDER BY inflight_runs ASC
        LIMIT 1
        FOR UPDATE
        """,
        {
            "agent_id": agent_id,
            "version_id": version_id,
            "limit": settings.sandbox_runs_per_container,
        },
    ).fetchone()
    return AgentContainer.from_row(rec) if rec is not None else None


def _claim_container_slot(agent_id, version_id) -> tuple[AgentContainer | None, bool]:
    """在同一個交易裡決定「誰去啟動容器」。回傳 `(容器列, 是不是我要去啟動)`。

    ## 為什麼要鎖

    十個請求同時打進一個還沒啟動的 Agent，十個 worker 會同時發現「沒有容器」。
    沒有鎖的話它們會開十個容器 —— 而這台機器開兩個就滿了。

    用 advisory lock 而不是唯一索引，是因為**鎖只需要罩住「檢查加寫入」那一瞬間**，
    不該罩住 image pull（那要幾十秒）。交易一結束鎖就放掉，真正的啟動在鎖外面做。

    鎖的 key 用 `agent_version_id`：容器綁定單一版本（P5），所以「該不該再開一個」
    本來就是 per-version 的問題。
    """
    with worker_db.transaction() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%(k)s, 0))",
            {"k": str(version_id)},
        )
        # 拿到鎖之後**要重查一次** —— 等鎖的期間別人可能已經啟動好了。
        ready = _ready_container(conn, agent_id, version_id)
        if ready is not None:
            return ready, False
        # UNHEALTHY = 存活檢查正在處理一個死掉的容器，幾秒後會回到 STARTING。
        # 也要等它 —— 這時另開一個，同一個版本就會有兩個容器。
        starting = conn.execute(
            f"""
            SELECT {AgentContainer.returning()} FROM agent_containers
            WHERE agent_id = %(agent_id)s
              AND agent_version_id = %(version_id)s
              AND status IN ('STARTING', 'UNHEALTHY')
            LIMIT 1
            """,
            {"agent_id": agent_id, "version_id": version_id},
        ).fetchone()
        if starting is not None:
            return AgentContainer.from_row(starting), False
        if _given_up(conn, version_id) is not None:
            # 一直死掉而被放棄的版本，不再自動開常駐容器 —— 等人處理。
            # 呼叫端（execute_run）會把這筆 Run 收成 AGENT_NOT_RUNNING，不退回一次性沙盒。
            return None, False
        rec = conn.execute(
            f"""
            INSERT INTO agent_containers (id, agent_id, agent_version_id, status)
            VALUES (%(id)s, %(agent_id)s, %(version_id)s, 'STARTING')
            RETURNING {AgentContainer.returning()}
            """,
            {"id": uuid7(), "agent_id": agent_id, "version_id": version_id},
        ).fetchone()
        return AgentContainer.from_row(rec), True


def _given_up(conn, version_id) -> AgentContainer | None:
    """這個版本**最新的那一列**容器，如果是被存活檢查放棄的就回傳它。

    與 api 的 `containers.given_up_container` 同一條規則（同步版）：只看最新一列 ——
    有人手動預熱會新增一列，判定就該改看那一列，不能被更早以前放棄的舊紀錄永遠擋住。
    """
    from apps.api.services.containers import MAX_RESTARTS_IN_WINDOW

    rec = conn.execute(
        f"""
        SELECT {AgentContainer.returning()} FROM agent_containers
        WHERE agent_version_id = %(version_id)s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"version_id": version_id},
    ).fetchone()
    if rec is None:
        return None
    row = AgentContainer.from_row(rec)
    if row.status == "FAILED" and row.restarts_in_window >= MAX_RESTARTS_IN_WINDOW:
        return row
    return None


def _fail_given_up(redis: Redis, rid: uuid.UUID, row: AgentContainer) -> dict:
    """版本被放棄 → 這筆 Run 直接 FAILED + AGENT_NOT_RUNNING，不派工、不退回一次性沙盒。

    連續死掉通常是程式或記憶體真的有問題，退回一次性沙盒只會把問題藏起來。
    """
    from apps.api.services.containers import given_up_message

    with worker_db.transaction() as conn:
        _transition(conn, rid, RunStatus.FAILED, terminal=True,
                    error=_error(ErrorCode.AGENT_NOT_RUNNING, given_up_message(row),
                                 str(rid), False))
    publish_pending(redis, rid)
    return {"run_id": str(rid), "status": str(RunStatus.FAILED), "reason": "given_up"}


def _live_containers(conn, agent_id, version_id) -> list[AgentContainer]:
    """這個版本目前**在服務**的容器，不管滿不滿。

    跟 `_ready_container` 的差別就是容量條件 —— 有它才分得出
    「一個容器都沒有」（要冷啟動，得等）與「都滿了」（排隊就好，不用等）。
    """
    rows = conn.execute(
        f"""
        SELECT {AgentContainer.returning()} FROM agent_containers
        WHERE agent_id = %(agent_id)s
          AND agent_version_id = %(version_id)s
          AND status IN ('READY', 'STARTING')
        """,
        {"agent_id": agent_id, "version_id": version_id},
    ).fetchall()
    return [AgentContainer.from_row(row) for row in rows]


def _wait_ready(container_uuid, deadline: float) -> AgentContainer | None:
    """等某個容器變成 READY。逾時或 FAILED 回 `None`。

    輪詢 DB 而不是等 Celery 的結果：啟動可能是**別的 worker** 發動的，
    那個 AsyncResult 我拿不到。DB 那一列才是所有人都看得到的真相。
    """
    while time.monotonic() < deadline:
        with worker_db.connection() as conn:
            rec = conn.execute(
                f"SELECT {AgentContainer.returning()} FROM agent_containers WHERE id = %(id)s",
                {"id": container_uuid},
            ).fetchone()
        if rec is None or rec["status"] == "FAILED":
            return None
        if rec["status"] == "READY":
            return AgentContainer.from_row(rec)
        time.sleep(0.5)
    return None


def _ensure_container(redis: Redis, agent_id, version_id) -> AgentContainer | None:
    """沒有可用的常駐容器就開一個，並**等它起來**（ZEN-015）。

    第一筆 Run 會付出啟動成本（含 image pull），之後的 Run 直接進熱容器。
    使用者可以先按「啟動」把這筆成本挪到自己方便的時候，但不按也能用。

    **起不來就回 `None`，由呼叫端退回一次性沙盒。** 常駐化是效能手段，
    它壞掉不該讓整個平台不能跑 —— 舊路徑留著當安全網。
    """
    from apps.worker.tasks.container import start_container

    container, mine = _claim_container_slot(agent_id, version_id)
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
            status = start_container(redis, manager, container.id)
        except Exception:                          # noqa: BLE001
            log.exception("inline container start failed for agent %s", agent_id)
            return None
        if status != "READY":
            return None
        with worker_db.connection() as conn:
            rec = conn.execute(
                f"SELECT {AgentContainer.returning()} FROM agent_containers WHERE id = %(id)s",
                {"id": container.id},
            ).fetchone()
        return AgentContainer.from_row(rec) if rec is not None else None
    return _wait_ready(container.id, deadline)


@celery_app.task(name="zentra.run.execute", bind=True, acks_late=True)
def execute_run(self, run_id: str) -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    manager = _manager(redis)

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
        with worker_db.transaction() as conn:
            run, claimed = _claim_run(conn, rid, self.request.id)
        if run is not None:
            publish_pending(redis, rid)
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
            with worker_db.transaction() as conn:
                _transition(conn, rid, RunStatus.FAILED, terminal=True,
                            error=_error(ErrorCode.QUOTA_EXCEEDED, str(exc), run_id, True))
            publish_pending(redis, rid)
            return {"run_id": run_id, "status": str(RunStatus.FAILED)}

        # P4：這是沙盒唯一拿得到的憑證
        token = runtoken.issue_sync(
            redis, run_id, run.timeout_seconds,
        )

        with worker_db.connection() as conn:
            rec = conn.execute(
                f"SELECT {AgentVersion.returning()} FROM agent_versions WHERE id = %(id)s",
                {"id": run.agent_version_id},
            ).fetchone()
        version = AgentVersion.from_row(rec) if rec is not None else None
        # SUPERSEDED 也照跑（ADR-0016）：Run 建立時綁的就是這一筆，取代發生在它排隊期間
        # 不代表它該改跑新 image。舊 image 會等這些 Run 都結束才被收掉。
        if version is None or version.status not in (
            AgentVersionStatus.PUBLISHED, AgentVersionStatus.SUPERSEDED,
        ):
            with worker_db.transaction() as conn:
                _transition(
                    conn, rid, RunStatus.FAILED, terminal=True,
                    error=_error(
                        ErrorCode.SANDBOX_ERROR, "published image metadata is unavailable", run_id, True
                    ),
                )
            publish_pending(redis, rid)
            return {"run_id": run_id, "status": str(RunStatus.FAILED)}

        # ── ZEN-015：有常駐容器就派進去，沒有才開一次性沙盒 ──────────
        #
        # 這是新舊兩條路的分岔點。**沒啟動容器的 Agent 行為完全不變** ——
        # 舊 image（沒有 serve 模式）也照樣能跑，因為它們走的還是下面那條。
        # （常駐 Run 的逾時清掃在 worker 的 60 秒排程裡，不在這裡。）
        #
        # 版本被存活檢查放棄了：放棄前就建好、還沒派出去的 Run 走到這裡。
        with worker_db.connection() as conn:
            given_up = _given_up(conn, version.id)
        if given_up is not None:
            return _fail_given_up(redis, rid, given_up)

        live: list[AgentContainer] = []
        with worker_db.transaction() as conn:
            container = _ready_container(conn, run.agent_id, version.id)
            if container is not None:
                # 有空位才預留。`_ready_container` 只挑 inflight < N 的，
                # 所以加一之後仍然不會超過 N。與 FOR UPDATE 同一交易。
                conn.execute(
                    """
                    UPDATE agent_containers
                       SET inflight_runs = inflight_runs + 1
                     WHERE id = %(id)s
                    """,
                    {"id": container.id},
                )
            else:
                live = _live_containers(conn, run.agent_id, version.id)
        #: 上面那個交易已經幫這筆預留了名額（inflight + 1）。冷啟動拿到的容器還沒有。
        reserved = container is not None
        #: True = 全部容器都滿了，這筆只是排進佇列，還沒有歸屬到任何容器。
        queued_only = False
        if container is None and str(version.id) != BUILTIN_AGENT_VERSION_ID:
            # ZEN-000 的內建 fixture 沒有 serve 模式，跳過它（ZEN-010 會退休它）。
            #
            # 「沒有可用容器」有兩種，處理方式相反：
            #   滿了      → 工作排進 Redis 佇列就好，容器空出位子會自己來拿。
            #               **不開第二個容器** —— 一個版本就是一個容器，多的排隊。
            #   一個都沒有 → 沒人能接，只能等一個起來（冷啟動）
            serving = [row for row in live if row.status == "READY"]
            if serving:
                # **不預留給容器。** 已經滿了，硬記上去會讓 `inflight_runs` 超過 N。
                # 這筆工作就是在排隊 —— 容器取走它時，take_work 才把它記上去。
                queued_only = True
                container = serving[0]
            else:
                container = _ensure_container(redis, run.agent_id, version.id)
                if container is None and str(version.id) != BUILTIN_AGENT_VERSION_ID:
                    # 等容器的期間它被放棄了（UNHEALTHY → FAILED）：不退回一次性沙盒。
                    with worker_db.connection() as conn:
                        given_up = _given_up(conn, version.id)
                    if given_up is not None:
                        return _fail_given_up(redis, rid, given_up)
        if container is not None:
            # 常駐路徑的 run token **由 app 在容器取走工作時才發**（TTL 從那一刻算）。
            # 這裡先發的那把用不到 —— 在佇列裡等久了它會先過期 —— 直接撤掉。
            runtoken.revoke_run_sync(redis, run_id)
            token = None
            item = {
                "run_id": run_id,
                "entrypoint": run.entrypoint,
                "input": run.input,
                "manifest": version.manifest or {},
                "default_model": _default_model(run.agent_id),
                # ZEN-019：**頂層欄位，不得塞進 manifest**（契約 §4.1 第 1 點）。
                # manifest 是版本的不可變內容；把可變設定攪進去，P5 在 runtime
                # 這一側就沒有實體了。
                "config": _agent_config(run.agent_id),
                "secret_names": _secret_names(run.agent_id),
            }
            # **狀態維持 SCHEDULING、不記 started_at。** 容器取走時（app 的 take_work）
            # 才轉 RUNNING 並開始計時 —— 排隊的時間不算進 Run 的逾時。
            if not queued_only:
                with worker_db.transaction() as conn:
                    if not reserved:
                        # 冷啟動（或等別人啟動好）拿到的容器，**還沒幫這筆記上名額**。
                        # 以前漏了這一步：Run 明明在跑，inflight_runs 卻是 0，跑完要減時又被
                        # `> 0` 擋住 —— 數字一路偏低，容量判斷與卡死偵測都建立在它上面。
                        # 有條件地加：同時等同一個冷啟動的 Run 可能超過 N，超過的就當排隊。
                        reserved = conn.execute(
                            """
                            UPDATE agent_containers SET inflight_runs = inflight_runs + 1
                            WHERE id = %(id)s AND inflight_runs < %(limit)s
                            RETURNING id
                            """,
                            {"id": container.id, "limit": settings.sandbox_runs_per_container},
                        ).fetchone() is not None
                    if reserved:
                        conn.execute(
                            "UPDATE runs SET container_id = %(cid)s WHERE id = %(id)s",
                            {"cid": container.id, "id": rid},
                        )

            # **推工作一定要在 commit 之後。** 反過來的話，容器可能在 DB 寫完之前
            # 就取走這筆工作並做歸屬校正 —— 那時它看到的 `run.container_id` 還是空的，
            # 於是它加一次、派工端再加一次，`inflight_runs` 變成實際的兩倍。
            # 實測踩過：一筆 Run 跑完，容器顯示 inflight=2。
            #
            # 反向的風險是「commit 了但沒推出去」（行程正好在這裡死掉），
            # 那筆 Run 會停在 SCHEDULING 直到 _sweep_timeouts 依排隊上限收掉它 —— 慢，但帳是對的。
            work.push_sync(redis, run.agent_id, version.id, item)
            # **不等結果就返回。** 常駐容器同時跑 N 個 Run，而 Celery worker 只有
            # 十個行程 —— 在這裡等會讓 Celery 變成新的瓶頸，N 就白設了。
            # 結果由容器自己回寫 /internal/runs/{id}/result，終態由 finalize_run 裁決。
            manager.release(run_id)
            reserved = False
            dispatched_to_container = True
            return {"run_id": run_id, "status": str(RunStatus.SCHEDULING), "dispatched": True}

        # Keep the ZEN-000 fixture runnable during the migration. Every user
        # registered version uses the exact DB-resolved digest path.
        if str(version.id) == BUILTIN_AGENT_VERSION_ID:
            handle = manager.create(run_id, token)
        else:
            handle = manager.create_run(run_id, token, version.image_ref)
        with worker_db.transaction() as conn:
            run = _transition(
                conn, rid, RunStatus.RUNNING,
                sandbox_id=handle.id, started_at=datetime.now(UTC),
            )
        publish_pending(redis, rid)

        timeout = run.timeout_seconds
        result = manager.driver.wait(handle, timeout=timeout)

        with worker_db.transaction() as conn:
            finished = _finish_sync(
                conn, rid, result.exit_code, result.logs, timed_out=result.timed_out)
        publish_pending(redis, rid)
        return finished

    except SandboxPlatformError as exc:
        log.exception("run %s sandbox platform failure", run_id)
        try:
            with worker_db.transaction() as conn:
                current = lock_run(conn, uuid.UUID(run_id))
                if not RunStatus(current.status).is_terminal:
                    _transition(
                        conn, current.id, RunStatus.FAILED, terminal=True,
                        error=_error(ErrorCode.SANDBOX_ERROR, str(exc), run_id, True))
            publish_pending(redis, uuid.UUID(run_id))
        except (RunNotFound, Exception):
            log.exception("failed to mark run %s SANDBOX_ERROR", run_id)
        return {"run_id": run_id, "status": str(RunStatus.FAILED)}

    except Exception as exc:                      # noqa: BLE001 — 最後一道網，不能讓 Run 卡在 RUNNING
        log.exception("run %s crashed in worker", run_id)
        try:
            with worker_db.transaction() as conn:
                current = lock_run(conn, uuid.UUID(run_id))
                if not RunStatus(current.status).is_terminal:
                    _transition(
                        conn, current.id, RunStatus.FAILED, terminal=True,
                        error=_error(ErrorCode.INTERNAL_ERROR, str(exc), run_id, True))
            publish_pending(redis, uuid.UUID(run_id))
        except (RunNotFound, Exception):
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
        redis.close()
