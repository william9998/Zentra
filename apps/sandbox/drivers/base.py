"""Platform-neutral sandbox lifecycle contract (ADR-0004 current interface)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class DriverCapabilities:
    driver: str
    l1_network_isolation: bool
    l2_kernel_isolation: bool
    l3_resource_limits: bool
    l4_filesystem_isolation: bool
    l5_sdk_interception: bool

    def __getitem__(self, key: str) -> str | bool:
        """Expose names used by QA while preserving the existing attributes."""
        aliases = {
            "l1_internal_network": "l1_network_isolation",
            "l4_read_only_filesystem": "l4_filesystem_isolation",
            "l5_application_controls": "l5_sdk_interception",
        }
        field_name = aliases.get(key, key)
        if field_name not in self.__dataclass_fields__:
            raise KeyError(key)
        return getattr(self, field_name)

    def missing_layers(self) -> list[str]:
        pairs = (
            ("L1", self.l1_network_isolation),
            ("L2", self.l2_kernel_isolation),
            ("L3", self.l3_resource_limits),
            ("L4", self.l4_filesystem_isolation),
            ("L5", self.l5_sdk_interception),
        )
        return [name for name, enabled in pairs if not enabled]


@dataclass(frozen=True)
class SandboxSpec:
    run_id: str
    image: str
    network: str
    # Worker supplies env; direct driver callers can supply token/gateway.
    env: dict[str, str] = field(default_factory=dict)
    token: str | None = None
    gateway: str | None = None
    mem_limit: str = "2g"
    pids_limit: int = 256
    nano_cpus: int = 2_000_000_000
    workspace_size: str = "512m"

    def sandbox_environment(self) -> dict[str, str]:
        if self.token is not None or self.gateway is not None:
            if self.token is None or self.gateway is None or self.env:
                raise ValueError("spec must provide token and gateway together, or env")
            values = {
                "ZENTRA_RUN_TOKEN": self.token,
                "ZENTRA_GATEWAY": self.gateway,
                "ZENTRA_RUN_ID": self.run_id,
            }
        else:
            values = dict(self.env)
        if set(values) != {"ZENTRA_RUN_TOKEN", "ZENTRA_GATEWAY", "ZENTRA_RUN_ID"}:
            raise ValueError("sandbox environment must contain exactly three ZENTRA_* values")
        if values["ZENTRA_RUN_ID"] != self.run_id:
            raise ValueError("sandbox run ID does not match spec")
        return values


@dataclass(frozen=True)
class SandboxHandle:
    id: str
    name: str

    @property
    def container_id(self) -> str:
        return self.id


@dataclass(frozen=True)
class SandboxExit:
    exit_code: int
    timed_out: bool = False
    logs: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SandboxDriver(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...

    async def wait(self, handle: SandboxHandle, timeout: float) -> SandboxExit: ...

    async def logs(self, handle: SandboxHandle, tail: int = 200) -> str: ...

    async def destroy(self, handle: SandboxHandle) -> None: ...

    async def capabilities(self) -> DriverCapabilities: ...
