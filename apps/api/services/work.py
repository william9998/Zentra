"""常駐容器的工作佇列（ZEN-015 / ADR-0012）。

    派工端（worker）  ──▶ LPUSH agent_work:{agent_id}:{agent_version_id}
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


def queue_key(agent_id: uuid.UUID | str, version_id: uuid.UUID | str) -> str:
    """**一個版本一條佇列，不是一個 Agent 一條。**

    容器綁定單一版本（P5）。共用一條佇列的話，切換版本的那段期間新舊容器並存，
    舊容器會把新版本的工作撈走 —— 那筆 Run 說好跑 1.0.2，實際跑的是 1.0.0，
    而畫面上完全看不出來。實測踩過：新容器閒著，舊容器把新版的請求全接走了。

    版本放進 key 而不是在取出後檢查，是因為「取出來發現不是我的」沒有好的處理方式：
    塞回去會打亂 FIFO，丟掉會讓那筆 Run 消失。
    """
    return f"{_PREFIX}{agent_id}:{version_id}"


def push_sync(
    redis: SyncRedis,
    agent_id: uuid.UUID | str,
    version_id: uuid.UUID | str,
    item: dict[str, Any],
) -> None:
    """派工端用（Celery worker 是同步的）。"""
    redis.lpush(queue_key(agent_id, version_id), json.dumps(item))


async def pop(
    redis: AsyncRedis,
    agent_id: uuid.UUID | str,
    version_id: uuid.UUID | str,
    timeout: int = LONG_POLL_SECONDS,
) -> dict[str, Any] | None:
    """阻塞取一筆工作，逾時回 None。

    用 BRPOP 而不是輪詢：有工作時延遲趨近於零，沒工作時不燒 CPU。
    LPUSH + BRPOP 讓佇列是 FIFO —— 先進的 Run 先被處理，
    不然尖峰時早到的請求會被後到的餓死。
    """
    raw = await redis.brpop(queue_key(agent_id, version_id), timeout=timeout)
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


async def push_front(
    redis: AsyncRedis,
    agent_id: uuid.UUID | str,
    version_id: uuid.UUID | str,
    item: dict[str, Any],
) -> None:
    """把取出來卻交不出去的工作放回**最前面**（BRPOP 從右邊取，所以 RPUSH）。

    放回尾巴會讓它重新排隊 —— 它本來就是最早來的，不該被後到的插隊。
    """
    await redis.rpush(queue_key(agent_id, version_id), json.dumps(item))


async def depth(
    redis: AsyncRedis, agent_id: uuid.UUID | str, version_id: uuid.UUID | str,
) -> int:
    """佇列長度。使用者問「我排在第幾個」時要答得出來。"""
    return int(await redis.llen(queue_key(agent_id, version_id)))


def clear_sync(
    redis: SyncRedis, agent_id: uuid.UUID | str, version_id: uuid.UUID | str,
) -> None:
    """停止該版本最後一個容器時清空佇列 —— 沒有人會來取了，
    留著只會讓那些 Run 永遠停在 RUNNING 直到逾時。"""
    redis.delete(queue_key(agent_id, version_id))
