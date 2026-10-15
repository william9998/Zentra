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

import asyncpg

from apps.api.enums import AgentVersionStatus, ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.ids import uuid7
from apps.api.rows import AgentContainer, AgentVersion, Run

#: 這些狀態表示容器正在或應該在服務。
LIVE_STATUSES = ("STARTING", "READY", "UNHEALTHY")


async def latest_published_version(
    conn: asyncpg.Connection, agent_id: uuid.UUID,
) -> AgentVersion:
    rec = await conn.fetchrow(
        f"SELECT {AgentVersion.returning()} FROM agent_versions "
        "WHERE agent_id = $1 AND status = $2 "
        "ORDER BY published_at DESC NULLS LAST LIMIT 1",
        agent_id, str(AgentVersionStatus.PUBLISHED),
    )
    if rec is None:
        raise ZentraError(
            ErrorCode.AGENT_NOT_PUBLISHED,
            "this agent has no published version to start a container from",
            409,
        )
    return AgentVersion.from_row(rec)


async def list_containers(
    conn: asyncpg.Connection, agent_id: uuid.UUID,
) -> list[AgentContainer]:
    rows = await conn.fetch(
        f"SELECT {AgentContainer.returning()} FROM agent_containers "
        "WHERE agent_id = $1 ORDER BY created_at DESC",
        agent_id,
    )
    return [AgentContainer.from_row(row) for row in rows]


async def live_containers(
    conn: asyncpg.Connection, agent_id: uuid.UUID,
) -> list[AgentContainer]:
    return [c for c in await list_containers(conn, agent_id) if c.status in LIVE_STATUSES]


async def request_start(
    conn: asyncpg.Connection, agent_id: uuid.UUID, version: AgentVersion | None = None,
) -> AgentContainer:
    """登記一個「要啟動」的容器，回傳待建立的那一列。

    **不在這裡建容器** —— 只寫 `STARTING`，實際建立由 worker 接手。
    這樣 API 不會被 image pull 卡住，而且 docker.sock 仍然只有 worker 掛。
    """
    version = version or await latest_published_version(conn, agent_id)

    existing = await live_containers(conn, agent_id)
    same_version = [c for c in existing if c.agent_version_id == version.id]
    if same_version:
        # 已經有同版本的容器在服務 —— 重複啟動不是錯誤，但也不該再開一個。
        # 要多開是「並發滿了溢出」的職責，不是使用者按兩次按鈕。
        return same_version[0]

    rec = await conn.fetchrow(
        f"""
        INSERT INTO agent_containers (id, agent_id, agent_version_id, status)
        VALUES ($1, $2, $3, $4)
        RETURNING {AgentContainer.returning()}
        """,
        uuid7(), agent_id, version.id, "STARTING",
    )
    return AgentContainer.from_row(rec)


async def load_container(conn: asyncpg.Connection, container_uuid: str) -> AgentContainer:
    try:
        parsed = uuid.UUID(container_uuid)
    except ValueError:
        raise not_found("container") from None
    rec = await conn.fetchrow(
        f"SELECT {AgentContainer.returning()} FROM agent_containers WHERE id = $1",
        parsed,
    )
    if rec is None:
        raise not_found("container")
    return AgentContainer.from_row(rec)


async def request_stop(
    conn: asyncpg.Connection, container: AgentContainer,
) -> AgentContainer:
    """標記為要停止。實際銷毀由 worker 做。"""
    rec = await conn.fetchrow(
        f"UPDATE agent_containers SET status = $2 WHERE id = $1 "
        f"RETURNING {AgentContainer.returning()}",
        container.id, "STOPPED",
    )
    return AgentContainer.from_row(rec)


async def request_restart(
    conn: asyncpg.Connection, container: AgentContainer,
) -> AgentContainer:
    """重啟：回到 `STARTING` 並把 `restart_count` 加一。

    **計數要留著。** 一直重啟的容器是壞掉了，不是需要再重啟一次 ——
    沒有這個數字，那件事在畫面上看起來跟「剛啟動」一模一樣。
    """
    rec = await conn.fetchrow(
        f"""
        UPDATE agent_containers SET
            status = $2,
            restart_count = restart_count + 1,
            error = NULL,
            container_id = NULL,
            inflight_runs = 0
        WHERE id = $1
        RETURNING {AgentContainer.returning()}
        """,
        container.id, "STARTING",
    )
    return AgentContainer.from_row(rec)


async def mark_ready(
    conn: asyncpg.Connection, container_uuid: uuid.UUID, docker_id: str,
) -> None:
    now = datetime.now(UTC)
    await conn.execute(
        """
        UPDATE agent_containers SET
            container_id = $2, status = $3, started_at = $4, last_health_at = $4, error = NULL
        WHERE id = $1
        """,
        container_uuid, docker_id, "READY", now,
    )


async def mark_failed(
    conn: asyncpg.Connection, container_uuid: uuid.UUID, message: str,
) -> None:
    await conn.execute(
        "UPDATE agent_containers SET status = $2, error = $3 WHERE id = $1",
        container_uuid, "FAILED", message[:2000],
    )


async def touch_health_by_agent(conn: asyncpg.Connection, agent_id: uuid.UUID) -> None:
    """容器來取工作時呼叫。用 agent_id 找是因為容器只知道自己屬於哪個 Agent ——
    它不知道自己在 DB 裡的那一列 id，也不需要知道。"""
    now = datetime.now(UTC)
    await conn.execute(
        """
        UPDATE agent_containers SET
            last_health_at = $2,
            status = CASE WHEN status IN ('STARTING', 'UNHEALTHY') THEN 'READY' ELSE status END
        WHERE agent_id = $1 AND status = ANY($3::text[])
        """,
        agent_id, now, list(LIVE_STATUSES),
    )


async def touch_health(conn: asyncpg.Connection, container_uuid: uuid.UUID) -> None:
    """容器 long-poll 取工作時呼叫 —— **取工作本身就是最好的健康訊號**。

    另外做一條 `/healthz` 是多餘的：容器能來要工作，就表示它的 event loop 沒被卡死、
    網路通、憑證有效。那正是我們想知道的全部。
    """
    now = datetime.now(UTC)
    await conn.execute(
        """
        UPDATE agent_containers SET
            last_health_at = $2,
            status = CASE WHEN status IN ('STARTING', 'UNHEALTHY') THEN 'READY' ELSE status END
        WHERE id = $1
        """,
        container_uuid, now,
    )


async def attribute_run(
    conn: asyncpg.Connection, taker_uuid: uuid.UUID, run_id: str | None,
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

    rec = await conn.fetchrow(
        f"SELECT {Run.returning()} FROM runs WHERE id = $1", rid)
    if rec is None:
        return
    run = Run.from_row(rec)
    if run.container_id == taker_uuid:
        return

    previous = run.container_id
    if previous is not None:
        await conn.execute(
            "UPDATE agent_containers SET inflight_runs = inflight_runs - 1 "
            "WHERE id = $1 AND inflight_runs > 0",
            previous,
        )
    await conn.execute(
        "UPDATE agent_containers SET inflight_runs = inflight_runs + 1 WHERE id = $1",
        taker_uuid,
    )
    await conn.execute(
        "UPDATE runs SET container_id = $2 WHERE id = $1", rid, taker_uuid,
    )


async def resolve_for_work(
    conn: asyncpg.Connection, agent_id: uuid.UUID, container_uuid: uuid.UUID | None,
) -> AgentContainer | None:
    """取工作時，把 container token 換成那一列容器。

    要它是為了拿到 `agent_version_id` —— 佇列是**一個版本一條**（P5），
    不知道版本就不知道該從哪一條取。

    `container_uuid` 為 None 是舊 token（v0.9.0 之前發的，只綁 Agent）。
    那時退回「這個 Agent 只有一個活著的容器」：那正是多版本並存還不會發生的狀態，
    所以退回去不會取到別人的工作。有多個就回 None，寧可讓它空轉也不要跑錯版本。
    """
    if container_uuid is not None:
        rec = await conn.fetchrow(
            f"SELECT {AgentContainer.returning()} FROM agent_containers WHERE id = $1",
            container_uuid,
        )
        if rec is None:
            return None
        row = AgentContainer.from_row(rec)
        return row if row.status in LIVE_STATUSES else None

    live = await live_containers(conn, agent_id)
    return live[0] if len(live) == 1 else None


async def semver_of(conn: asyncpg.Connection, agent_version_id: uuid.UUID) -> str:
    value = await conn.fetchval(
        "SELECT semver FROM agent_versions WHERE id = $1", agent_version_id)
    return value or "?"
