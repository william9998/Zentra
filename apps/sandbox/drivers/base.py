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

    New OCI callers use :meth:`for_run` or :meth:`for_validation`. ``run_id``,
    ``image``, ``token`` and ``gateway`` remain as a narrow ZEN-000 bridge.
    """

    network: str
    resource_id: str | None = None
    image_ref: str | None = None
    mode: SandboxMode = SandboxMode.RUN
    env: dict[str, str] = field(default_factory=dict)
    mem_limit: str = "2g"
    pids_limit: int = 256
    nano_cpus: int = 2_000_000_000
    workspace_size: str = "512m"
    # ZEN-000 compatibility. Remove when its built-in tagged image is gone.
    run_id: str | None = None
    image: str | None = None
    token: str | None = None
    gateway: str | None = None

    def __post_init__(self) -> None:
        if self.resource_id is not None or self.image_ref is not None:
            if any(value is not None for value in (self.run_id, self.image, self.token, self.gateway)):
                raise ValueError("OCI and legacy SandboxSpec fields cannot be mixed")
            if not self.resource_id or not self.image_ref:
                raise ValueError("resource_id and image_ref are required together")
            parse_digest_reference(self.image_ref)
            self._validate_environment()
            return

        if not self.run_id or not self.image:
            raise ValueError("resource_id/image_ref or legacy run_id/image are required")
        if self.token is not None or self.gateway is not None:
            if self.token is None or self.gateway is None or self.env:
                raise ValueError("legacy spec must provide token and gateway together, or env")
        self._validate_limits()

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
    def effective_resource_id(self) -> str:
        return self.resource_id or self.run_id or ""

    @property
    def effective_image_ref(self) -> str:
        return self.image_ref or self.image or ""

    @property
    def role(self) -> str:
        if self.resource_id is None:
            return "sandbox"
        return "validation" if self.mode is SandboxMode.VALIDATE else "run"

    @property
    def is_oci(self) -> bool:
        return self.image_ref is not None

    def sandbox_environment(self) -> dict[str, str]:
        if self.resource_id is not None:
            return dict(self.env)
        if self.token is not None or self.gateway is not None:
            if self.token is None or self.gateway is None or self.env:
                raise ValueError("legacy spec must provide token and gateway together, or env")
            values = {
                "ZENTRA_RUN_TOKEN": self.token,
                "ZENTRA_GATEWAY": self.gateway,
                "ZENTRA_RUN_ID": self.run_id,
            }
        else:
            values = dict(self.env)
        if set(values) != {"ZENTRA_RUN_TOKEN", "ZENTRA_GATEWAY", "ZENTRA_RUN_ID"}:
            raise ValueError("legacy sandbox environment must contain exactly three ZENTRA_* values")
        if values["ZENTRA_RUN_ID"] != self.run_id:
            raise ValueError("sandbox run ID does not match spec")
        return values

    def _validate_environment(self) -> None:
        expected = (
            {"ZENTRA_GATEWAY_URL", "ZENTRA_AGENT_VERSION_ID", "ZENTRA_VALIDATION_TOKEN"}
            if self.mode is SandboxMode.VALIDATE
            else {"ZENTRA_GATEWAY_URL", "ZENTRA_RUN_ID", "ZENTRA_RUN_TOKEN"}
        )
        if set(self.env) != expected:
            raise ValueError(f"{self.mode.value} environment must contain exactly {sorted(expected)}")
        id_key = "ZENTRA_AGENT_VERSION_ID" if self.mode is SandboxMode.VALIDATE else "ZENTRA_RUN_ID"
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

    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...

    async def wait(self, handle: SandboxHandle, timeout: float) -> SandboxExit: ...

    async def logs(self, handle: SandboxHandle, tail: int = 200) -> str: ...

    async def destroy(self, handle: SandboxHandle) -> None: ...

    async def capabilities(self) -> DriverCapabilities: ...
