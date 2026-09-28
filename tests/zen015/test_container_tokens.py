"""ZEN-015 §7：一個 Agent 多個容器時，container token 的隔離。

每條擋的是一個具體的錯：

  T-1  停一個容器把兄弟一起弄啞  → 它們還活著、還在 long-poll，但每次都拿到 401，
                                  而畫面上它們看起來完全正常。這是多容器最容易犯的錯。
  T-2  token 認不出是哪個容器    → 「誰取走了這筆工作」答不出來，`inflight_runs`
                                  就只能用猜的，而猜錯的方向是「閒的顯示滿、滿的顯示閒」
  T-3  跨 Agent 取工作           → 唯一有實質後果的濫用方式
"""

from __future__ import annotations

import os
import uuid

import pytest

REDIS_URL = os.environ.get("ZENTRA_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="需要 ZENTRA_TEST_REDIS_URL")

TTL = 60


@pytest.fixture
def redis():
    from redis import Redis

    client = Redis.from_url(REDIS_URL)
    yield client
    client.close()


def test_token_identifies_the_container_not_just_the_agent(redis) -> None:
    """T-2：解出來要有容器 id，否則沒辦法把工作記到正確的容器上。"""
    import asyncio

    from redis.asyncio import Redis as AsyncRedis

    from apps.api.services import containertoken

    agent_id, container_id = uuid.uuid4(), uuid.uuid4()
    token = containertoken.issue_sync(redis, agent_id, TTL, container_uuid=container_id)

    async def resolve():
        client = AsyncRedis.from_url(REDIS_URL)
        try:
            return await containertoken.resolve(client, token)
        finally:
            await client.aclose()

    bound = asyncio.run(resolve())
    assert bound == (str(agent_id), str(container_id))
    containertoken.revoke_agent_sync(redis, agent_id)


def test_stopping_one_container_leaves_its_siblings_working(redis) -> None:
    """T-1：這是多容器的承重牆。撤銷必須只打到那一個容器。"""
    import asyncio

    from redis.asyncio import Redis as AsyncRedis

    from apps.api.services import containertoken

    agent_id = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    token_a = containertoken.issue_sync(redis, agent_id, TTL, container_uuid=first)
    token_b = containertoken.issue_sync(redis, agent_id, TTL, container_uuid=second)

    containertoken.revoke_container_sync(redis, first)

    async def resolve_both():
        client = AsyncRedis.from_url(REDIS_URL)
        try:
            return (await containertoken.resolve(client, token_a),
                    await containertoken.resolve(client, token_b))
        finally:
            await client.aclose()

    gone, alive = asyncio.run(resolve_both())
    assert gone is None, "被停掉的容器 token 該失效"
    assert alive == (str(agent_id), str(second)), "兄弟容器不該被連坐"
    containertoken.revoke_agent_sync(redis, agent_id)


def test_revoking_the_agent_still_takes_everything(redis) -> None:
    """刪 Agent 時仍然要一次撤乾淨 —— 只是它不再是「停止單一容器」的做法。"""
    import asyncio

    from redis.asyncio import Redis as AsyncRedis

    from apps.api.services import containertoken

    agent_id = uuid.uuid4()
    tokens = [
        containertoken.issue_sync(redis, agent_id, TTL, container_uuid=uuid.uuid4())
        for _ in range(3)
    ]
    containertoken.revoke_agent_sync(redis, agent_id)

    async def resolve_all():
        client = AsyncRedis.from_url(REDIS_URL)
        try:
            return [await containertoken.resolve(client, t) for t in tokens]
        finally:
            await client.aclose()

    assert asyncio.run(resolve_all()) == [None, None, None]


def test_tokens_of_different_agents_never_collide(redis) -> None:
    """T-3：跨 Agent 是這個 token 唯一有意義的濫用方式，所以隔離要獨立驗。"""
    import asyncio

    from redis.asyncio import Redis as AsyncRedis

    from apps.api.services import containertoken

    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    token_a = containertoken.issue_sync(redis, agent_a, TTL, container_uuid=uuid.uuid4())
    token_b = containertoken.issue_sync(redis, agent_b, TTL, container_uuid=uuid.uuid4())

    containertoken.revoke_agent_sync(redis, agent_a)

    async def resolve_b():
        client = AsyncRedis.from_url(REDIS_URL)
        try:
            return await containertoken.resolve(client, token_b)
        finally:
            await client.aclose()

    assert asyncio.run(resolve_b())[0] == str(agent_b)
    assert token_a != token_b
    containertoken.revoke_agent_sync(redis, agent_b)
