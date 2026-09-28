"""常駐 Agent 容器的生命週期（ZEN-015 / ADR-0012）。

    發布版本 ──▶ PUBLISHED ──(啟動)──▶ STARTING ──▶ READY ──(停止)──▶ STOPPED
                                          │           │
                                          └─(失敗)────┴──▶ UNHEALTHY ──(重啟)──▶ STARTING

**啟動是明確動作，不是註冊的副作用。** 使用者要能只註冊不啟動 —— 註冊是「這個版本存在」，
啟動是「現在開始佔資源」。把兩者綁在一起會讓「發十個版本」變成「開十個容器」。

這個模組只管 DB 狀態與裁決，**真正建容器的是 worker**（它才有 docker.sock）。
api 不掛 docker.sock，那條規則一個字都不改。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import AgentVersionStatus, ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.ids import uuid7
from apps.api.models import AgentContainer, AgentVersion, Run

#: 這些狀態表示容器正在或應該在服務。
LIVE_STATUSES = ("STARTING", "READY", "UNHEALTHY")


async def latest_published_version(session: AsyncSession, agent_id: uuid.UUID) -> AgentVersion:
    version = (await session.execute(
        select(AgentVersion)
        .where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.status == AgentVersionStatus.PUBLISHED,
        )
        .order_by(AgentVersion.published_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if version is None:
        raise ZentraError(
            ErrorCode.AGENT_NOT_PUBLISHED,
            "this agent has no published version to start a container from",
            409,
        )
    return version


async def list_containers(session: AsyncSession, agent_id: uuid.UUID) -> list[AgentContainer]:
    return list((await session.execute(
        select(AgentContainer)
        .where(AgentContainer.agent_id == agent_id)
        .order_by(AgentContainer.created_at.desc())
    )).scalars())


async def live_containers(session: AsyncSession, agent_id: uuid.UUID) -> list[AgentContainer]:
    return [c for c in await list_containers(session, agent_id) if c.status in LIVE_STATUSES]


async def request_start(
    session: AsyncSession, agent_id: uuid.UUID, version: AgentVersion | None = None,
) -> AgentContainer:
    """登記一個「要啟動」的容器，回傳待建立的那一列。

    **不在這裡建容器** —— 只寫 `STARTING`，實際建立由 worker 接手。
    這樣 API 不會被 image pull 卡住，而且 docker.sock 仍然只有 worker 掛。
    """
    version = version or await latest_published_version(session, agent_id)

    existing = await live_containers(session, agent_id)
    same_version = [c for c in existing if c.agent_version_id == version.id]
    if same_version:
        # 已經有同版本的容器在服務 —— 重複啟動不是錯誤，但也不該再開一個。
        # 要多開是「並發滿了溢出」的職責，不是使用者按兩次按鈕。
        return same_version[0]

    container = AgentContainer(
        id=uuid7(),
        agent_id=agent_id,
        agent_version_id=version.id,
        status="STARTING",
    )
    session.add(container)
    await session.commit()
    await session.refresh(container)
    return container


async def load_container(session: AsyncSession, container_uuid: str) -> AgentContainer:
    try:
        parsed = uuid.UUID(container_uuid)
    except ValueError:
        raise not_found("container") from None
    container = await session.get(AgentContainer, parsed)
    if container is None:
        raise not_found("container")
    return container


async def request_stop(session: AsyncSession, container: AgentContainer) -> AgentContainer:
    """標記為要停止。實際銷毀由 worker 做。"""
    container.status = "STOPPED"
    await session.commit()
    await session.refresh(container)
    return container


async def request_restart(session: AsyncSession, container: AgentContainer) -> AgentContainer:
    """重啟：回到 `STARTING` 並把 `restart_count` 加一。

    **計數要留著。** 一直重啟的容器是壞掉了，不是需要再重啟一次 ——
    沒有這個數字，那件事在畫面上看起來跟「剛啟動」一模一樣。
    """
    container.status = "STARTING"
    container.restart_count += 1
    container.error = None
    container.container_id = None
    container.inflight_runs = 0
    await session.commit()
    await session.refresh(container)
    return container


async def mark_ready(
    session: AsyncSession, container_uuid: uuid.UUID, docker_id: str,
) -> None:
    container = await session.get(AgentContainer, container_uuid)
    if container is None:
        return
    container.container_id = docker_id
    container.status = "READY"
    container.started_at = datetime.now(UTC)
    container.last_health_at = datetime.now(UTC)
    container.error = None
    await session.commit()


async def mark_failed(
    session: AsyncSession, container_uuid: uuid.UUID, message: str,
) -> None:
    container = await session.get(AgentContainer, container_uuid)
    if container is None:
        return
    container.status = "FAILED"
    container.error = message[:2000]
    await session.commit()


async def touch_health_by_agent(session: AsyncSession, agent_id: uuid.UUID) -> None:
    """容器來取工作時呼叫。用 agent_id 找是因為容器只知道自己屬於哪個 Agent ——
    它不知道自己在 DB 裡的那一列 id，也不需要知道。"""
    rows = await live_containers(session, agent_id)
    if not rows:
        return
    now = datetime.now(UTC)
    for row in rows:
        row.last_health_at = now
        if row.status in ("STARTING", "UNHEALTHY"):
            row.status = "READY"
    await session.commit()


async def touch_health(session: AsyncSession, container_uuid: uuid.UUID) -> None:
    """容器 long-poll 取工作時呼叫 —— **取工作本身就是最好的健康訊號**。

    另外做一條 `/healthz` 是多餘的：容器能來要工作，就表示它的 event loop 沒被卡死、
    網路通、憑證有效。那正是我們想知道的全部。
    """
    container = await session.get(AgentContainer, container_uuid)
    if container is None:
        return
    container.last_health_at = datetime.now(UTC)
    if container.status in ("STARTING", "UNHEALTHY"):
        container.status = "READY"
    await session.commit()


async def attribute_run(
    session: AsyncSession, taker_uuid: uuid.UUID, run_id: str | None,
) -> None:
    """把一筆 Run 的佔用從「派工端猜的容器」搬到「真正取走它的容器」（ZEN-015）。

    ## 為什麼需要校正

    工作佇列是 **per-agent** 的：同一個 Agent 的多個容器共用一條 FIFO，
    誰先空出來誰先拿。派工端在推工作時已經先把 `inflight_runs` 記在某個容器上了
    （它得挑一個來判斷容量），但實際取走的可能是另一個。

    不校正的結果不是數字差一點，是**方向完全相反**：畫面會顯示一個閒著的容器滿載、
    一個滿載的容器閒著，而擴容判斷就建立在那個數字上。

    單容器時 `taker` 跟猜的是同一個，這裡什麼都不做。
    """
    if not run_id:
        return
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        return

    run = await session.get(Run, rid)
    if run is None or run.container_id == taker_uuid:
        return

    previous = run.container_id
    if previous is not None:
        await session.execute(
            update(AgentContainer)
            .where(AgentContainer.id == previous, AgentContainer.inflight_runs > 0)
            .values(inflight_runs=AgentContainer.inflight_runs - 1)
        )
    await session.execute(
        update(AgentContainer)
        .where(AgentContainer.id == taker_uuid)
        .values(inflight_runs=AgentContainer.inflight_runs + 1)
    )
    run.container_id = taker_uuid
    await session.commit()
