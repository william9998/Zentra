"""常駐容器的工作佇列（ZEN-015 / ADR-0012）。

    派工端（worker）  ──▶ LPUSH agent_work:{agent_id}
    容器（long-poll） ──▶ GET /internal/agents/{id}/work ──▶ Gateway BRPOP

**容器碰不到 Redis** —— L1 的沙盒網段只連得到 `app`。所以 Redis 連線握在 Gateway 手裡，
容器只看得到一條 HTTP 路徑。這是「容器拉、平台不推」的實作：
沙盒仍然只會**主動打 Gateway**，網路拓撲一個字都沒改（ADR-0012）。

工作項裡帶著**那一個 Run 的 run token**。容器啟動時沒有任何 run token，
它是跟著工作進來的 —— 這是 P4 在常駐模式下的實作方式。
"""

import json
import uuid
from typing import Any

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis

_PREFIX = "agent_work:"

#: long-poll 等多久。比一般 HTTP 逾時短，讓連線定期回來 ——
#: 長連線掛在中間層（proxy、docker 網路）久了會被無聲切斷，
#: 而容器不會知道自己已經聾了。定期回來重連比「等到天荒地老」可靠。
LONG_POLL_SECONDS = 25


def queue_key(agent_id: uuid.UUID | str) -> str:
    return _PREFIX + str(agent_id)


def push_sync(redis: SyncRedis, agent_id: uuid.UUID | str, item: dict[str, Any]) -> None:
    """派工端用（Celery worker 是同步的）。"""
    redis.lpush(queue_key(agent_id), json.dumps(item))


async def pop(
    redis: AsyncRedis, agent_id: uuid.UUID | str, timeout: int = LONG_POLL_SECONDS,
) -> dict[str, Any] | None:
    """阻塞取一筆工作，逾時回 None。

    用 BRPOP 而不是輪詢：有工作時延遲趨近於零，沒工作時不燒 CPU。
    LPUSH + BRPOP 讓佇列是 FIFO —— 先進的 Run 先被處理，
    不然尖峰時早到的請求會被後到的餓死。
    """
    raw = await redis.brpop(queue_key(agent_id), timeout=timeout)
    if raw is None:
        return None
    _, payload = raw
    if isinstance(payload, bytes):
        payload = payload.decode()
    try:
        return json.loads(payload)
    except ValueError:
        # 壞掉的項目丟掉就好，不要讓整個容器因為一筆爛資料停止取工作。
        return None


async def depth(redis: AsyncRedis, agent_id: uuid.UUID | str) -> int:
    """佇列長度。使用者問「我排在第幾個」時要答得出來。"""
    return int(await redis.llen(queue_key(agent_id)))


def clear_sync(redis: SyncRedis, agent_id: uuid.UUID | str) -> None:
    """停止容器時清空佇列 —— 沒有人會來取了，留著只會讓那些 Run 永遠 QUEUED。"""
    redis.delete(queue_key(agent_id))
