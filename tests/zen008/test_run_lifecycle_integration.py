"""Z8-31 / Z8-33 / Z8-34 / Z8-35 / Z8-36：一 Run 一盒、失敗收斂、逾時、重送冪等、配額。

全部用真容器與真 Celery delivery。冪等測試直接對 broker 重送同一個 task ——
「重送會不會開第二個盒」不是讀程式碼能回答的問題。
"""

from __future__ import annotations

import subprocess
import time
import uuid

import httpx
import pytest
from celery import Celery
from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.enums import ErrorCode, RunStatus
from apps.api.models import Run, RunEvent
from apps.sandbox.manager import INFLIGHT_KEY
from tests.zen008.conftest import (
    containers_with_label,
    db_url,
    docker_json,
    new_agent,
    redis_url,
    register_version,
    require_registry,
    wait_gone,
    wait_run,
    wait_version,
)

RUN_TASK = "zentra.run.execute"
BUILD_TASK = "zentra.build.execute"


@pytest.fixture(autouse=True)
def _needs_registry() -> None:
    require_registry()


@pytest.fixture(scope="module")
def broker() -> Celery:
    app = Celery("zen008-qa", broker=redis_url(), backend=redis_url())
    app.conf.task_routes = {RUN_TASK: {"queue": "q.run"},
                            BUILD_TASK: {"queue": "q.build"}}
    return app


def _engine():
    return create_async_engine(db_url())


async def _run_row(run_id: str) -> Run:
    engine = _engine()
    try:
        async with async_sessionmaker(engine)() as session:
            return (await session.execute(select(Run).where(
                Run.id == uuid.UUID(run_id)))).scalar_one()
    finally:
        await engine.dispose()


async def _events(run_id: str) -> list[RunEvent]:
    engine = _engine()
    try:
        async with async_sessionmaker(engine)() as session:
            return list((await session.execute(select(RunEvent).where(
                RunEvent.run_id == uuid.UUID(run_id)).order_by(RunEvent.seq))).scalars().all())
    finally:
        await engine.dispose()


def _publish(api: httpx.Client, image_ref: str, prefix: str) -> str:
    agent_id = new_agent(api, prefix)
    assert register_version(api, agent_id, "1.0.0", image_ref).status_code == 202
    version = wait_version(api, agent_id, "1.0.0")
    assert version["status"] == "PUBLISHED", version
    return agent_id


# --------------------------------------------------------------------- Z8-31


async def test_each_run_gets_its_own_container_with_the_registered_digest(
    api: httpx.Client, published_agent: dict
) -> None:
    """三筆並發 Run：三個不同的 container、都用同一個 DB digest、固定 `zentra-agent run`。

    擋住的錯：容器被重用（前一 Run 的 /workspace 與行程狀態外洩），
    或 LangGraph node 被拆成多個 Celery task（ADR-0002 的反面）。
    """
    agent_id = published_agent["agent_id"]
    run_ids = []
    for index in range(3):
        created = api.post(f"/v1/agents/{agent_id}/runs", json={
            "entrypoint": "slow",
            "input": {"question": f"concurrent-{index}", "sleep_seconds": 12},
            "config": {"timeout_seconds": 180},
        })
        assert created.status_code == 202, created.text
        run_ids.append(created.json()["run_id"])

    observed: dict[str, dict] = {}
    deadline = time.time() + 120
    while time.time() < deadline and len(observed) < len(run_ids):
        for run_id in run_ids:
            if run_id in observed:
                continue
            for cid in containers_with_label(f"zentra.run_id={run_id}"):
                inspect = docker_json(["inspect", cid])[0]
                if inspect["State"]["Running"]:
                    observed[run_id] = inspect
        time.sleep(0.5)
    assert len(observed) == len(run_ids), f"只觀察到 {len(observed)}/3 個沙盒"

    ids = {inspect["Id"] for inspect in observed.values()}
    assert len(ids) == 3, "兩筆 Run 共用了同一個容器"
    for run_id, inspect in observed.items():
        assert inspect["Path"] == "/opt/zentra/bin/zentra-agent"
        assert inspect["Args"] == ["run"], inspect["Args"]
        assert inspect["Config"]["Image"] == published_agent["image_ref"]
        assert inspect["Config"]["Labels"]["zentra.role"] == "run"
        assert inspect["Config"]["Labels"]["zentra.run_id"] == run_id

    for run_id in run_ids:
        run = wait_run(api, run_id, timeout=240)
        assert run["status"] == RunStatus.SUCCEEDED, run
        row = await _run_row(run_id)
        assert str(row.sandbox_id) == observed[run_id]["Id"]
        assert not wait_gone(f"zentra.run_id={run_id}", timeout=60)


# --------------------------------------------------------------------- Z8-34


async def test_wall_clock_timeout_produces_sandbox_timeout_and_destroys_the_box(
    api: httpx.Client, published_agent: dict
) -> None:
    created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs", json={
        "entrypoint": "slow",
        "input": {"question": "hang", "sleep_seconds": 300},
        "config": {"timeout_seconds": 5},
    })
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]

    run = wait_run(api, run_id, timeout=180)
    assert run["status"] == RunStatus.FAILED, run
    assert run["error"]["code"] == ErrorCode.SANDBOX_TIMEOUT, run["error"]
    assert run["error"]["retryable"] is True
    assert run["output"] is None
    assert run["finished_at"] is not None

    assert not wait_gone(f"zentra.run_id={run_id}", timeout=60), "逾時後沙盒沒有被銷毀"
    terminal = [event for event in await _events(run_id) if event.terminal]
    assert len(terminal) == 1, "逾時路徑產生了多個終局事件"


@pytest.mark.skip(reason="`POST /v1/runs/{id}/cancel` 屬 ZEN-004（BOARD BACKLOG），"
                         "ZEN-008 未交付；Z8-34 的取消半邊未覆蓋")
def test_cancel_transitions_to_cancelled() -> None:  # pragma: no cover
    ...


# --------------------------------------------------------------------- Z8-33


async def test_zero_exit_without_a_result_is_not_success(
    api: httpx.Client, image_refs: dict
) -> None:
    """沙盒 exit 0 卻沒有回寫 result → 必須 FAILED。

    擋住的錯：拿 exit code 當成功判準。那會讓「跑完但沒產出」被當成成功回給呼叫端。
    """
    agent_id = _publish(api, image_refs["silent_exit"], "zen008-silent")
    created = api.post(f"/v1/agents/{agent_id}/runs", json={"input": {"question": "x"}})
    run = wait_run(api, created.json()["run_id"], timeout=180)
    assert run["status"] == RunStatus.FAILED, run
    assert run["error"]["code"] == ErrorCode.SANDBOX_ERROR, run["error"]
    assert run["output"] is None
    assert not wait_gone(f"zentra.run_id={run['run_id']}", timeout=60)


async def test_user_code_exception_converges_to_a_contract_error(
    api: httpx.Client, image_refs: dict
) -> None:
    """使用者 node 拋例外時，Run 必須收斂成 FAILED，且 error.code 屬於 §0 枚舉。

    契約 §0 為這個情境留了 `USER_CODE_ERROR`；目前實際回什麼由這條測試記錄下來。
    """
    agent_id = _publish(api, image_refs["raises"], "zen008-raises")
    created = api.post(f"/v1/agents/{agent_id}/runs", json={"input": {"question": "x"}})
    run = wait_run(api, created.json()["run_id"], timeout=180)
    assert run["status"] == RunStatus.FAILED, run
    assert run["error"]["code"] in {value.value for value in ErrorCode}, run["error"]
    assert run["output"] is None
    events = await _events(run["run_id"])
    assert sum(1 for event in events if event.terminal) == 1
    assert not wait_gone(f"zentra.run_id={run['run_id']}", timeout=60)


def test_user_code_exception_is_reported_as_user_code_error(
    api: httpx.Client, image_refs: dict
) -> None:
    """契約 §0：`USER_CODE_ERROR` = 「使用者 Agent 程式碼拋出例外」。

    擋住的錯：把使用者的 bug 報成 `SANDBOX_ERROR` + `retryable=true`——
    呼叫端會一直重試一段永遠不會成功的程式碼。
    """
    agent_id = _publish(api, image_refs["raises"], "zen008-usercode")
    created = api.post(f"/v1/agents/{agent_id}/runs", json={"input": {"question": "x"}})
    run = wait_run(api, created.json()["run_id"], timeout=180)
    assert run["error"]["code"] == ErrorCode.USER_CODE_ERROR, run["error"]


# --------------------------------------------------------------------- Z8-35


async def test_duplicate_run_delivery_claims_once_and_creates_one_sandbox(
    api: httpx.Client, published_agent: dict, broker: Celery
) -> None:
    """同一個 `run_id` 兩個真 delivery。

    擋住的錯：`acks_late` 重送時開第二個沙盒、重跑一次使用者程式碼、
    或覆寫已經寫好的終態。
    """
    created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs", json={
        "entrypoint": "slow",
        "input": {"question": "redelivery", "sleep_seconds": 10},
        "config": {"timeout_seconds": 120},
    })
    run_id = created.json()["run_id"]

    extra = [broker.send_task(RUN_TASK, args=[run_id],
                              task_id=f"{run_id}:dup{index}", queue="q.run")
             for index in range(2)]
    outcomes = [task.get(timeout=240) for task in extra]
    assert all(outcome.get("idempotent_skip") for outcome in outcomes), outcomes

    run = wait_run(api, run_id, timeout=240)
    assert run["status"] == RunStatus.SUCCEEDED, run
    assert run["output"] == {"echo": "redelivery", "mark": "v1"}

    events = await _events(run_id)
    assert sum(1 for event in events if event.terminal) == 1, "重送造成多個終局事件"
    running = [event for event in events
               if event.type == "run.status" and event.payload.get("status") == "RUNNING"]
    assert len(running) == 1, "重送造成第二次 RUNNING（代表開了第二個盒）"
    assert not wait_gone(f"zentra.run_id={run_id}", timeout=60)


async def test_duplicate_build_delivery_does_not_revalidate_a_terminal_version(
    api: httpx.Client, image_refs: dict, broker: Celery
) -> None:
    """Z8-08：終態版本重送 `q.build` 只回既有結果，不重拉、不再建 container、不改 metadata。"""
    agent_id = new_agent(api, "zen008-rebuild")
    assert register_version(api, agent_id, "1.0.0", image_refs["valid_v1"]).status_code == 202
    published = wait_version(api, agent_id, "1.0.0")
    assert published["status"] == "PUBLISHED"
    version_id = published["agent_version_id"]

    outcome = broker.send_task(BUILD_TASK, args=[version_id],
                               task_id=f"build:{version_id}:dup",
                               queue="q.build").get(timeout=240)
    assert outcome.get("idempotent_skip") is True, outcome
    assert outcome["status"] == "PUBLISHED"

    after = api.get(f"/v1/agents/{agent_id}/versions/1.0.0").json()
    assert after == published, "重送 build 改動了已發布版本的 metadata（違反 P5）"
    assert not wait_gone(f"zentra.agent_version_id={version_id}", timeout=30)


def test_build_and_run_are_routed_to_separate_queues(broker: Celery) -> None:
    """Z8-08 的設定面：`q.build` 與 `q.run` 必須分流，且 task 是 `acks_late`。"""
    import apps.worker.tasks.build  # noqa: F401  註冊 task 到 registry
    import apps.worker.tasks.run  # noqa: F401
    from apps.worker.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert routes[RUN_TASK]["queue"] == "q.run"
    assert routes[BUILD_TASK]["queue"] == "q.build"
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.tasks[RUN_TASK].acks_late is True
    assert celery_app.tasks[BUILD_TASK].acks_late is True


@pytest.mark.skip(reason="`Idempotency-Key` 的 24 小時 HTTP 重送保證由 ZEN-004 交付"
                         "（api-contract §0 明列）；ZEN-008 未實作，Z8-35 該半邊未覆蓋")
def test_idempotency_key_deduplicates_http_retries() -> None:  # pragma: no cover
    ...


# --------------------------------------------------------------------- Z8-36


def test_quota_decision_path_when_all_sandbox_slots_are_taken(
    api: httpx.Client, published_agent: dict
) -> None:
    """把 Redis 的併發登記填滿到上限，再發一筆 Run。

    這測的是「名額用完時會怎樣」這個決策路徑本身（登記在 Redis，不是行程內
    semaphore）。**不是**十個真沙盒的負載測試 —— 這台 Colima VM 只有 1.9 GiB，
    跑不動十個 langgraph 容器，那部分在裁定裡列為未覆蓋。
    """
    from apps.api.config import get_settings

    limit = get_settings().sandbox_max_concurrent
    redis = Redis.from_url(redis_url(), decode_responses=True)
    holders = [f"qa-quota-{uuid.uuid4().hex[:8]}" for _ in range(limit)]
    try:
        now = time.time()
        redis.zadd(INFLIGHT_KEY, {holder: now for holder in holders})
        assert redis.zcard(INFLIGHT_KEY) >= limit

        created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs",
                           json={"input": {"question": "quota"}})
        http_status, retry_after = created.status_code, created.headers.get("Retry-After")

        if http_status == 202:
            run = wait_run(api, created.json()["run_id"], timeout=180)
            assert run["status"] == RunStatus.FAILED, run
            assert run["error"]["code"] == ErrorCode.QUOTA_EXCEEDED, run["error"]
            pytest.fail(
                "契約 §0 要求超過併發上限時回 `429 QUOTA_EXCEEDED` 並帶 `Retry-After`，"
                f"實際是 HTTP {http_status} 並把 Run 收成 FAILED/QUOTA_EXCEEDED。"
                "呼叫端因此拿不到 Retry-After，也分不清是自己被限流還是 Agent 壞了。"
            )
        assert http_status == 429, created.text
        assert created.json()["error"]["code"] == ErrorCode.QUOTA_EXCEEDED
        assert retry_after, "429 缺少 Retry-After"
    finally:
        if holders:
            redis.zrem(INFLIGHT_KEY, *holders)
        redis.close()


def test_no_sandbox_or_validation_container_is_left_behind() -> None:
    """壓力跑完之後的總清理狀態。殘留容器＝洩漏的憑證與資源。"""
    leftovers = {
        "run": containers_with_label("zentra.role=run"),
        "validation": containers_with_label("zentra.role=validation"),
        "legacy": containers_with_label("zentra.managed=true"),
    }
    still_there = {role: [cid for cid in ids
                          if docker_json(["inspect", cid])[0]["State"]["Running"]]
                   for role, ids in leftovers.items()}
    assert not any(still_there.values()), still_there
    assert not leftovers["validation"], f"validation 容器殘留：{leftovers['validation']}"
