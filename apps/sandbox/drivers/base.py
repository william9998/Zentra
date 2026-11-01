"""Platform-neutral OCI sandbox lifecycle contract.

The worker may pass only a database-resolved resource id and immutable image
reference. Container controls stay on this side of the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Protocol


OCI_RUNTIME_CONTRACT = "zentra.image/v1"
OCI_RUNTIME_EXECUTABLE = "/opt/zentra/bin/zentra-agent"

_DIGEST_REFERENCE = re.compile(
    r"^(?P<repository>"
    r"(?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/)?"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r")@(?P<digest>sha256:[0-9a-f]{64})$"
)


def parse_digest_reference(image_ref: str) -> tuple[str, str]:
    """Return ``(repository, digest)`` for a canonical OCI reference."""

    if not isinstance(image_ref, str):
        raise ValueError("image_ref must be a string")
    match = _DIGEST_REFERENCE.fullmatch(image_ref)
    if match is None:
        raise ValueError("image_ref must be canonical repository@sha256:<64 lowercase hex>")
    return match.group("repository"), match.group("digest")


class SandboxMode(StrEnum):
    VALIDATE = "validate"
    RUN = "run"
    #: ZEN-015：常駐容器。啟動後不退出，long-poll Internal Gateway 取工作，
    #: 同時處理多個 Run。與 RUN 的差別不只是生命週期 —— 它拿到的憑證也不同：
    #: **啟動時只有 container token（只能取工作），run token 跟著工作項才進來。**
    SERVE = "serve"


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
class ImageMetadata:
    image_ref: str
    digest: str
    repo_digests: tuple[str, ...]
    platform: str
    size_bytes: int | None
    runtime_contract: str
    image_id: str


class SandboxPlatformError(RuntimeError):
    """A sanitized platform failure which the worker maps to public errors."""

    stage = "SANDBOX"
    retryable = True


class ImagePullError(SandboxPlatformError):
    stage = "PULL"
    retryable = True


class ImageInspectError(SandboxPlatformError):
    stage = "INSPECT"
    retryable = False


class SandboxCreateError(SandboxPlatformError):
    def __init__(self, message: str, *, stage: str = "SANDBOX", retryable: bool = True) -> None:
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable


@dataclass(frozen=True)
class SandboxSpec:
    """Container specification without caller-controlled Docker primitives.

    Callers use :meth:`for_run`, :meth:`for_validation` or :meth:`for_serve`.
    """

    network: str
    resource_id: str
    image_ref: str
    mode: SandboxMode = SandboxMode.RUN
    env: dict[str, str] = field(default_factory=dict)
    mem_limit: str = "2g"
    pids_limit: int = 256
    nano_cpus: int = 2_000_000_000
    workspace_size: str = "512m"

    def __post_init__(self) -> None:
        if not self.resource_id or not self.image_ref:
            raise ValueError("resource_id and image_ref are required")
        parse_digest_reference(self.image_ref)
        self._validate_environment()

    @classmethod
    def for_validation(
        cls,
        *,
        agent_version_id: str,
        image_ref: str,
        network: str,
        gateway_url: str,
        validation_token: str,
    ) -> "SandboxSpec":
        return cls(
            resource_id=agent_version_id,
            image_ref=image_ref,
            network=network,
            mode=SandboxMode.VALIDATE,
            env={
                "ZENTRA_GATEWAY_URL": gateway_url,
                "ZENTRA_AGENT_VERSION_ID": agent_version_id,
                "ZENTRA_VALIDATION_TOKEN": validation_token,
            },
        )

    @classmethod
    def for_serve(
        cls,
        *,
        container_uuid: str,
        agent_id: str,
        image_ref: str,
        network: str,
        gateway_url: str,
        container_token: str,
        max_concurrent_runs: int,
    ) -> "SandboxSpec":
        """常駐容器（ZEN-015）。

        **注意這裡沒有 run token。** 容器啟動時還不知道要跑哪個 Run；
        run token 跟著工作項在 long-poll 的回應裡進來（ADR-0012 的 P4 實作）。

        **`resource_id` 是容器列的 id，不是 agent_id。** 一個 Agent 可以有多個容器
        （版本切換期間並存、並發滿了溢出），用 agent_id 當容器名會讓第二個容器
        把第一個擠掉 —— 實測踩過：啟動新版本時舊版本的容器被無聲殺掉，
        DB 卻還留著兩列都說 READY。
        """
        return cls(
            resource_id=container_uuid,
            image_ref=image_ref,
            network=network,
            mode=SandboxMode.SERVE,
            env={
                "ZENTRA_GATEWAY_URL": gateway_url,
                "ZENTRA_AGENT_ID": agent_id,
                "ZENTRA_CONTAINER_TOKEN": container_token,
                "ZENTRA_MAX_CONCURRENT_RUNS": str(max_concurrent_runs),
            },
        )

    @classmethod
    def for_run(
        cls,
        *,
        run_id: str,
        image_ref: str,
        network: str,
        gateway_url: str,
        run_token: str,
    ) -> "SandboxSpec":
        return cls(
            resource_id=run_id,
            image_ref=image_ref,
            network=network,
            mode=SandboxMode.RUN,
            env={
                "ZENTRA_GATEWAY_URL": gateway_url,
                "ZENTRA_RUN_ID": run_id,
                "ZENTRA_RUN_TOKEN": run_token,
            },
        )

    @property
    def role(self) -> str:
        return {
            SandboxMode.VALIDATE: "validation",
            SandboxMode.SERVE: "serve",
        }.get(self.mode, "run")

    def sandbox_environment(self) -> dict[str, str]:
        return dict(self.env)

    def _validate_environment(self) -> None:
        # 每種模式**恰好**這幾個變數，多一個少一個都擋。
        # 這條檢查是 P4 的第一道關：多注入一個 secret 會在建立容器時就失敗，
        # 而不是等到有人發現金鑰進了沙盒。
        expected_by_mode = {
            SandboxMode.VALIDATE: {
                "ZENTRA_GATEWAY_URL", "ZENTRA_AGENT_VERSION_ID", "ZENTRA_VALIDATION_TOKEN",
            },
            SandboxMode.RUN: {"ZENTRA_GATEWAY_URL", "ZENTRA_RUN_ID", "ZENTRA_RUN_TOKEN"},
            # SERVE 沒有 run token —— 它啟動時還不知道要跑哪個 Run。
            # container token 的權限只有「取這個 agent 的工作」，碰不到 run 與 LLM（ZEN-015）。
            SandboxMode.SERVE: {
                "ZENTRA_GATEWAY_URL", "ZENTRA_AGENT_ID", "ZENTRA_CONTAINER_TOKEN",
                "ZENTRA_MAX_CONCURRENT_RUNS",
            },
        }
        expected = expected_by_mode[self.mode]
        if set(self.env) != expected:
            raise ValueError(f"{self.mode.value} environment must contain exactly {sorted(expected)}")
        if self.mode is SandboxMode.SERVE:
            # SERVE 的 resource_id 是容器列的 id（容器名要唯一），
            # 而 env 裡的 ZENTRA_AGENT_ID 是它服務哪個 Agent —— 兩者本來就不同。
            self._validate_limits()
            return
        id_key = {
            SandboxMode.VALIDATE: "ZENTRA_AGENT_VERSION_ID",
        }.get(self.mode, "ZENTRA_RUN_ID")
        if self.env[id_key] != self.resource_id:
            raise ValueError(f"{self.mode.value} resource ID does not match spec")
        if not all(isinstance(value, str) and value for value in self.env.values()):
            raise ValueError("sandbox environment values must be non-empty strings")
        self._validate_limits()

    def _validate_limits(self) -> None:
        if self.mem_limit != "2g":
            raise ValueError("OCI sandbox memory limit is fixed at 2g")
        if self.pids_limit != 256:
            raise ValueError("OCI sandbox pids limit is fixed at 256")
        if self.nano_cpus != 2_000_000_000:
            raise ValueError("OCI sandbox CPU limit is fixed at 2 cores")
        if self.workspace_size != "512m":
            raise ValueError("OCI sandbox workspace is fixed at 512m")


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


@dataclass(frozen=True)
class SandboxExecution:
    image: ImageMetadata
    handle: SandboxHandle
    exit: SandboxExit


class SandboxDriver(Protocol):
    async def prepare_image(self, image_ref: str) -> ImageMetadata: ...

    async def inspect_image(self, image_ref: str) -> ImageMetadata: ...

    async def remove_image(self, image_ref: str) -> None: ...

    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...

    async def wait(self, handle: SandboxHandle, timeout: float) -> SandboxExit: ...

    async def logs(self, handle: SandboxHandle, tail: int = 200) -> str: ...

    async def destroy(self, handle: SandboxHandle) -> None: ...

    async def capabilities(self) -> DriverCapabilities: ...
