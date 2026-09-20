"""One Docker container per Run; the worker alone holds the Docker socket."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import docker
from docker.errors import APIError, NotFound
from docker.types import LogConfig

from apps.sandbox.drivers.base import (
    DriverCapabilities,
    SandboxExit,
    SandboxHandle,
    SandboxSpec,
)

log = logging.getLogger(__name__)
_MAX_LOG_BYTES = 10 * 1024 * 1024


class DockerSandboxDriver:
    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self._client = client if client is not None else docker.from_env()

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return await asyncio.to_thread(self._create, spec)

    def _create(self, spec: SandboxSpec) -> SandboxHandle:
        network = self._client.networks.get(spec.network)
        network.reload()
        if network.attrs.get("Internal") is not True:
            raise ValueError(f"sandbox network {spec.network!r} is not internal")

        name = f"zentra-run-{spec.run_id}"
        container = self._client.containers.create(
            image=spec.image,
            name=name,
            detach=True,
            network=spec.network,
            environment=spec.sandbox_environment(),
            labels={
                "zentra.managed": "true",
                "zentra.role": "sandbox",
                "zentra.run_id": spec.run_id,
            },
            working_dir="/workspace",
            user="65534:65534",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            read_only=True,
            tmpfs={
                "/workspace": f"rw,nosuid,nodev,size={spec.workspace_size},uid=65534,gid=65534",
                "/tmp": "rw,nosuid,nodev,size=64m,mode=1777",
            },
            mem_limit=spec.mem_limit,
            memswap_limit=spec.mem_limit,
            pids_limit=spec.pids_limit,
            nano_cpus=spec.nano_cpus,
            log_config=LogConfig(type="json-file", config={"max-size": "10m", "max-file": "1"}),
            command=["python", "-m", "zentra_runtime"],
        )
        try:
            container.start()
        except Exception:
            try:
                container.remove(force=True)
            except Exception:
                log.exception("failed to remove unstarted sandbox %s", name)
            raise
        log.info("sandbox created run_id=%s container=%s", spec.run_id, container.id[:12])
        return SandboxHandle(id=container.id, name=name)

    async def wait(self, handle: SandboxHandle, timeout: float) -> SandboxExit:
        try:
            container = await asyncio.to_thread(self._client.containers.get, handle.id)
        except NotFound:
            return SandboxExit(exit_code=-1, logs="container disappeared before wait()")

        if timeout <= 0:
            return await self._timeout_exit(handle, container)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(container.wait, timeout=timeout + 5), timeout=timeout
            )
            return SandboxExit(
                exit_code=int(result.get("StatusCode", -1)), logs=await self.logs(handle)
            )
        except TimeoutError:
            return await self._timeout_exit(handle, container)
        except (APIError, OSError) as exc:
            return SandboxExit(exit_code=-1, logs=f"Docker wait failed: {exc}")

    async def _timeout_exit(self, handle: SandboxHandle, container: Any) -> SandboxExit:
        # Stop execution before reading logs: a blocked logs call must not
        # extend the sandbox's wall-clock execution past the deadline.
        try:
            await asyncio.to_thread(container.kill)
        except (NotFound, APIError):
            pass
        logs = await self.logs(handle)
        await self.destroy(handle)
        return SandboxExit(exit_code=-1, timed_out=True, logs=logs)

    async def logs(self, handle: SandboxHandle, tail: int = 200) -> str:
        try:
            container = await asyncio.to_thread(self._client.containers.get, handle.id)
            raw = await asyncio.to_thread(container.logs, tail=tail)
            return raw[:_MAX_LOG_BYTES].decode("utf-8", errors="replace")
        except (NotFound, APIError):
            return ""

    async def destroy(self, handle: SandboxHandle) -> None:
        """Idempotent across normal completion, crash, timeout and cancellation."""
        try:
            container = await asyncio.to_thread(self._client.containers.get, handle.id)
            await asyncio.to_thread(container.remove, force=True)
        except NotFound:
            pass

    async def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            driver="docker",
            l1_network_isolation=True,
            l2_kernel_isolation=False,  # Docker hardening is not gVisor.
            l3_resource_limits=True,
            l4_filesystem_isolation=False,  # code/venv read-only binds are ZEN-001.
            l5_sdk_interception=False,  # application controls are ZEN-002/003.
        )
