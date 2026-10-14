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
    ImageMetadata,
    SandboxDriver,
    SandboxExecution,
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

    def prepare_image(self, image_ref: str) -> ImageMetadata:
        return asyncio.run(self._driver.prepare_image(image_ref))

    def inspect_image(self, image_ref: str) -> ImageMetadata:
        return asyncio.run(self._driver.inspect_image(image_ref))

    def remove_image(self, image_ref: str) -> None:
        return asyncio.run(self._driver.remove_image(image_ref))

    def wait(self, handle: SandboxHandle, timeout: float) -> SandboxExit:
        return asyncio.run(self._driver.wait(handle, timeout))

    def inspect_container(self, docker_id: str) -> dict:
        return asyncio.run(self._driver.inspect_container(docker_id))

    def container_logs(self, docker_id: str) -> str:
        return asyncio.run(self._driver.container_logs(docker_id))

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
        image: str | None = None,
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
        """ZEN-000 compatibility for its built-in tagged runtime image."""
        if self._image is None:
            raise ValueError("legacy sandbox image is not configured")
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

    def prepare_image(self, image_ref: str) -> ImageMetadata:
        """Ensure the exact digest is cached and return verified OCI metadata."""

        return self._blocking_driver.prepare_image(image_ref)

    def inspect_image(self, image_ref: str) -> ImageMetadata:
        """Re-check the cached exact digest without resolving or pulling a tag."""

        return self._blocking_driver.inspect_image(image_ref)

    def create_validation(
        self,
        agent_version_id: str,
        validation_token: str,
        image_ref: str,
    ) -> SandboxHandle:
        spec = SandboxSpec.for_validation(
            agent_version_id=agent_version_id,
            image_ref=image_ref,
            network=self._network,
            gateway_url=self._gateway_url,
            validation_token=validation_token,
        )
        return self._blocking_driver.create(spec)

    def driver_inspect(self, docker_id: str) -> dict:
        """容器的原始 inspect 結果。生命週期管理要看 `State.Running` 與 `ExitCode`。"""
        return self._blocking_driver.inspect_container(docker_id)

    def driver_logs(self, docker_id: str) -> str:
        """容器的輸出。它死掉時這是唯一的線索，所以不能吞掉。"""
        return self._blocking_driver.container_logs(docker_id)

    def driver_destroy(self, handle) -> None:
        """同步銷毀。常駐容器的生命週期由 worker 管，它是同步的。"""
        self._blocking_driver.destroy(handle)

    def create_serve(
        self,
        container_uuid: str,
        agent_id: str,
        container_token: str,
        image_ref: str,
        max_concurrent_runs: int,
    ) -> SandboxHandle:
        """建立常駐容器（ZEN-015）。

        **不佔併發配額** —— 配額算的是同時執行的 Run，而容器本身不是 Run。
        容器內的並發由 `max_concurrent_runs` 控（ADR-0012 的 N）。
        """
        spec = SandboxSpec.for_serve(
            container_uuid=container_uuid,
            agent_id=agent_id,
            image_ref=image_ref,
            network=self._network,
            gateway_url=self._gateway_url,
            container_token=container_token,
            max_concurrent_runs=max_concurrent_runs,
        )
        return self._blocking_driver.create(spec)

    def create_run(
        self,
        run_id: str,
        run_token: str,
        image_ref: str,
    ) -> SandboxHandle:
        spec = SandboxSpec.for_run(
            run_id=run_id,
            image_ref=image_ref,
            network=self._network,
            gateway_url=self._gateway_url,
            run_token=run_token,
        )
        return self._blocking_driver.create(spec)

    def execute_validation(
        self,
        agent_version_id: str,
        validation_token: str,
        image_ref: str,
        *,
        timeout: float = 600,
    ) -> SandboxExecution:
        """Run validation with a hard timeout and unconditional container cleanup.

        Token revocation and version state remain backend responsibilities.
        Callers which need to observe the live container id can instead use
        ``create_validation`` plus ``driver.wait`` in their own ``finally``.
        """

        image = self.prepare_image(image_ref)
        handle = self.create_validation(agent_version_id, validation_token, image_ref)
        try:
            result = self.driver.wait(handle, timeout=timeout)
            # A successful validation is publishable only if the cached image
            # still resolves to the same exact digest after user code exits.
            checked = self.inspect_image(image_ref) if result.ok else image
            if checked.digest != image.digest or checked.image_ref != image.image_ref:
                raise RuntimeError("validation image identity changed during execution")
            return SandboxExecution(image=checked, handle=handle, exit=result)
        finally:
            self.driver.destroy(handle)

    def execute_run(
        self,
        run_id: str,
        run_token: str,
        image_ref: str,
        *,
        timeout: float,
    ) -> SandboxExecution:
        """Run one exact-digest sandbox and always destroy it before returning."""

        image = self.prepare_image(image_ref)
        handle = self.create_run(run_id, run_token, image_ref)
        try:
            result = self.driver.wait(handle, timeout=timeout)
            return SandboxExecution(image=image, handle=handle, exit=result)
        finally:
            self.driver.destroy(handle)
