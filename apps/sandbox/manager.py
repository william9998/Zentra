"""Sandbox Manager —— 生命週期與併發配額。**它建立沙盒，不執行程式碼。**

併發上限用 Redis 的 sorted set 實作，不是行程內的 semaphore ——
Celery worker 是多行程的，行程內 semaphore 擋不住別的行程。
"""

import asyncio
import logging
import time

from redis import Redis

from apps.sandbox.drivers.base import (
    DriverCapabilities,
    SandboxDriver,
    SandboxExit,
    SandboxHandle,
    SandboxSpec,
)

log = logging.getLogger(__name__)

INFLIGHT_KEY = "sandbox:inflight"
#: 租約上限。worker 崩潰時計數器會殘留，靠這個過期時間自動清掉，
#: 所以它必須大於 Run 的最長執行時間（SPEC §10 是 900s）。
LEASE_SECONDS = 1200

# 先剪掉過期租約、再檢查上限、再登記 —— 三步必須是原子的，
# 否則兩個行程會同時通過檢查（典型的 check-then-act race）。
_RESERVE_LUA = """
local key, now, limit, lease, member = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - lease)
if redis.call('ZCARD', key) >= limit then
  return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, lease * 2)
return 1
"""


class QuotaExceeded(RuntimeError):
    """併發沙盒數已達上限（SPEC §10：預設 10）。"""


class _BlockingDriverAdapter:
    """Celery task is synchronous; keep Docker access behind async driver API."""

    def __init__(self, driver: SandboxDriver) -> None:
        self._driver = driver

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        return asyncio.run(self._driver.create(spec))

    def wait(self, handle: SandboxHandle, timeout: float) -> SandboxExit:
        return asyncio.run(self._driver.wait(handle, timeout))

    def logs(self, handle: SandboxHandle, tail: int = 200) -> str:
        return asyncio.run(self._driver.logs(handle, tail))

    def destroy(self, handle: SandboxHandle) -> None:
        asyncio.run(self._driver.destroy(handle))

    def capabilities(self) -> DriverCapabilities:
        return asyncio.run(self._driver.capabilities())


class SandboxManager:
    def __init__(
        self,
        driver: SandboxDriver,
        redis: Redis,
        *,
        image: str,
        network: str,
        gateway_url: str,
        max_concurrent: int,
    ) -> None:
        self._driver = driver
        self._blocking_driver = _BlockingDriverAdapter(driver)
        self._redis = redis
        self._image = image
        self._network = network
        self._gateway_url = gateway_url
        self._max_concurrent = max_concurrent
        self._reserve = redis.register_script(_RESERVE_LUA)

    @property
    def driver(self) -> _BlockingDriverAdapter:
        return self._blocking_driver

    def reserve(self, run_id: str) -> None:
        ok = self._reserve(keys=[INFLIGHT_KEY],
                           args=[time.time(), self._max_concurrent, LEASE_SECONDS, run_id])
        if not int(ok):
            raise QuotaExceeded(f"concurrent sandbox limit reached ({self._max_concurrent})")

    def release(self, run_id: str) -> None:
        self._redis.zrem(INFLIGHT_KEY, run_id)

    def inflight(self) -> int:
        self._redis.zremrangebyscore(INFLIGHT_KEY, "-inf", time.time() - LEASE_SECONDS)
        return int(self._redis.zcard(INFLIGHT_KEY))

    def create(self, run_id: str, run_token: str) -> SandboxHandle:
        """P4：注入沙盒的環境變數**只有這三個**。多一個 secret 就是違反立場。"""
        spec = SandboxSpec(
            run_id=run_id,
            image=self._image,
            network=self._network,
            env={
                "ZENTRA_RUN_ID": run_id,
                "ZENTRA_RUN_TOKEN": run_token,
                "ZENTRA_GATEWAY": self._gateway_url,
            },
        )
        return self._blocking_driver.create(spec)
