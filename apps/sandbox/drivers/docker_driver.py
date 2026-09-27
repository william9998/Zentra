"""Exact-digest Docker implementation for disposable OCI Agent containers."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import re
from typing import Any

import docker
from docker.errors import APIError, ImageNotFound, NotFound
from docker.types import LogConfig

from apps.sandbox.drivers.base import (
    DriverCapabilities,
    ImageInspectError,
    ImageMetadata,
    ImagePullError,
    OCI_RUNTIME_CONTRACT,
    OCI_RUNTIME_EXECUTABLE,
    SandboxCreateError,
    SandboxExit,
    SandboxHandle,
    SandboxMode,
    SandboxSpec,
    parse_digest_reference,
)

log = logging.getLogger(__name__)
_MAX_LOG_BYTES = 10 * 1024 * 1024
_DEFAULT_REGISTRY_AUTH_FILE = "/run/secrets/zentra_registry_auth"
_REDACT = re.compile(
    r"(?i)(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]+|"
    r"(?P<scheme>https?://)[^/@\s:]+:[^/@\s]+@"
)


def _safe_error(exc: BaseException, secrets: tuple[str, ...] = ()) -> str:
    """Keep Docker diagnostics useful without reflecting credentials."""

    message = _REDACT.sub(
        lambda match: (match.groupdict().get("scheme") or "") + "[redacted]@",
        str(exc),
    )
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return message


_DEFAULT_REGISTRY_HOSTS = frozenset({"docker.io", "index.docker.io"})
_CANONICAL_DEFAULT_REGISTRY = "docker.io"
_OFFICIAL_NAMESPACE = "library"


def _split_registry(repository: str) -> tuple[str | None, str]:
    """Split ``repository`` into ``(registry_host, remainder)``.

    SPEC §7.1: the first segment is a registry host when it contains ``.`` or
    ``:``, or equals ``localhost``. A host segment is authoritative and is
    never rewritten anywhere in this module.
    """

    head, separator, rest = repository.partition("/")
    if separator and ("." in head or ":" in head or head == "localhost"):
        return head, rest
    return None, repository


def _registry_for(repository: str) -> str:
    host, _ = _split_registry(repository)
    if host is None:
        return "https://index.docker.io/v1/"
    return host


def _normalized_repository(repository: str) -> str:
    """Fully qualify a repository so that equivalent spellings compare equal.

    Only the default registry is normalized, because only there does Docker
    rewrite what we registered: ``docker.io/library/alpine`` = ``docker.io/alpine``
    = ``alpine``. Every other host is returned verbatim -- normalizing
    ``localhost:6000/x`` into ``x`` would silently point an internal-registry
    version at a Docker Hub image (SPEC §7.1, ZEN-009 spec §5.6).
    """

    host, remainder = _split_registry(repository)
    if host is not None and host not in _DEFAULT_REGISTRY_HOSTS:
        return repository
    path = remainder if host is not None else repository
    if "/" not in path:
        path = f"{_OFFICIAL_NAMESPACE}/{path}"
    return f"{_CANONICAL_DEFAULT_REGISTRY}/{path}"


def _comparison_key(reference: str) -> str | None:
    """Normalized ``repository@digest`` key, or ``None`` if not a digest ref.

    This exists purely to compare the registered reference with what the daemon
    reports. The registered string itself is never rewritten: it is the stored
    identity of the version (P5).
    """

    repository, separator, digest = reference.rpartition("@")
    if not separator or not repository or ":" not in digest:
        return None
    return f"{_normalized_repository(repository)}@{digest}"


class RegistryCredentialStore:
    """Read-only compose secret containing a standard Docker ``auths`` map.

    The returned dict exists only for the daemon pull request. It is never put
    in a SandboxSpec, environment, mount, database payload or log record.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        configured = path or os.getenv(
            "ZENTRA_REGISTRY_AUTH_FILE", _DEFAULT_REGISTRY_AUTH_FILE
        )
        self._path = Path(configured)

    def resolve(self, repository: str) -> dict[str, str] | None:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise ImagePullError("registry credential secret is unreadable") from exc

        auths = document.get("auths", document) if isinstance(document, dict) else {}
        if not isinstance(auths, dict):
            raise ImagePullError("registry credential secret must contain an auths object")

        registry = _registry_for(repository)
        aliases = [
            registry,
            registry.removeprefix("https://"),
            registry.removeprefix("http://"),
        ]
        if registry == "https://index.docker.io/v1/":
            aliases.extend(["index.docker.io", "registry-1.docker.io"])
        entry = next((auths[key] for key in aliases if key in auths), None)
        if entry is None:
            return None
        if not isinstance(entry, dict):
            raise ImagePullError("registry credential entry must be an object")

        if isinstance(entry.get("identitytoken"), str) and entry["identitytoken"]:
            return {"identitytoken": entry["identitytoken"]}
        if isinstance(entry.get("registrytoken"), str) and entry["registrytoken"]:
            return {"registrytoken": entry["registrytoken"]}
        if isinstance(entry.get("username"), str) and isinstance(
            entry.get("password"), str
        ):
            return {"username": entry["username"], "password": entry["password"]}
        encoded = entry.get("auth")
        if isinstance(encoded, str) and encoded:
            try:
                decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
                username, password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ImagePullError(
                    "registry credential entry has invalid auth encoding"
                ) from exc
            return {"username": username, "password": password}
        raise ImagePullError("registry credential entry has no supported credential")


class DockerSandboxDriver:
    def __init__(
        self,
        client: docker.DockerClient | None = None,
        *,
        credential_store: RegistryCredentialStore | None = None,
    ) -> None:
        self._client = client if client is not None else docker.from_env()
        self._credentials = credential_store or RegistryCredentialStore()

    async def prepare_image(self, image_ref: str) -> ImageMetadata:
        return await asyncio.to_thread(self._prepare_image, image_ref)

    def _prepare_image(self, image_ref: str) -> ImageMetadata:
        repository, digest = parse_digest_reference(image_ref)
        try:
            image = self._client.images.get(image_ref)
        except ImageNotFound:
            image = self._pull_exact(repository, digest, image_ref)
        except (APIError, OSError) as exc:
            raise ImagePullError(
                f"cannot access exact image {image_ref}: {_safe_error(exc)}"
            ) from exc
        return self._inspect_object(image, image_ref)

    def _pull_exact(self, repository: str, digest: str, image_ref: str) -> Any:
        auth_config = self._credentials.resolve(repository)
        kwargs: dict[str, Any] = {"repository": repository, "tag": digest}
        if auth_config is not None:
            kwargs["auth_config"] = auth_config
        try:
            # There is intentionally no tag lookup, fallback image or second
            # candidate. Docker SDK calls this selector ``tag`` for digests too.
            return self._client.images.pull(**kwargs)
        except (APIError, ImageNotFound, OSError) as exc:
            secret_values = tuple(auth_config.values()) if auth_config is not None else ()
            raise ImagePullError(
                f"failed to pull exact image {image_ref}: "
                f"{_safe_error(exc, secret_values)}"
            ) from exc

    async def inspect_image(self, image_ref: str) -> ImageMetadata:
        return await asyncio.to_thread(self._inspect_image, image_ref)

    def _inspect_image(self, image_ref: str) -> ImageMetadata:
        parse_digest_reference(image_ref)
        try:
            image = self._client.images.get(image_ref)
        except (APIError, ImageNotFound, OSError) as exc:
            raise ImageInspectError(
                f"exact image is not in cache: {_safe_error(exc)}"
            ) from exc
        return self._inspect_object(image, image_ref)

    def _inspect_object(self, image: Any, image_ref: str) -> ImageMetadata:
        _, expected_digest = parse_digest_reference(image_ref)
        try:
            image.reload()
            attrs = image.attrs
            repo_digests = tuple(attrs.get("RepoDigests") or ())
            # Docker strips the default-registry prefix it normalized away, so
            # compare normalized keys instead of raw strings. Non-default
            # registries (ghcr.io, localhost:6000) are compared verbatim.
            expected_key = _comparison_key(image_ref)
            reported_keys = {
                key
                for key in (_comparison_key(str(entry)) for entry in repo_digests)
                if key is not None
            }
            if expected_key is None or expected_key not in reported_keys:
                raise ImageInspectError(
                    "Docker RepoDigests does not contain registered reference "
                    f"{image_ref} (compared as {expected_key or image_ref}, "
                    f"reported {sorted(reported_keys) or list(repo_digests)})"
                )

            config = attrs.get("Config") or {}
            labels = config.get("Labels") or {}
            runtime_contract = labels.get("io.zentra.image-contract")
            if runtime_contract != OCI_RUNTIME_CONTRACT:
                raise ImageInspectError(
                    "image label io.zentra.image-contract must be "
                    f"{OCI_RUNTIME_CONTRACT}"
                )

            image_os = str(attrs.get("Os") or "").lower()
            image_arch = self._normalize_arch(str(attrs.get("Architecture") or ""))
            image_variant = str(attrs.get("Variant") or "").lower()
            host_info = self._client.info()
            host_os = str(host_info.get("OSType") or "").lower()
            host_arch = self._normalize_arch(str(host_info.get("Architecture") or ""))
            if (
                not image_os
                or not image_arch
                or image_os != host_os
                or image_arch != host_arch
            ):
                raise ImageInspectError(
                    f"image platform {image_os}/{image_arch} is incompatible with "
                    f"host {host_os}/{host_arch}"
                )
            platform = f"{image_os}/{image_arch}"
            if image_variant:
                platform = f"{platform}/{image_variant}"
            size = attrs.get("Size")
            return ImageMetadata(
                image_ref=image_ref,
                digest=expected_digest,
                repo_digests=repo_digests,
                platform=platform,
                size_bytes=int(size) if isinstance(size, int) else None,
                runtime_contract=runtime_contract,
                image_id=str(image.id),
            )
        except ImageInspectError:
            raise
        except (APIError, KeyError, TypeError, ValueError, OSError) as exc:
            raise ImageInspectError(
                f"failed to inspect exact image: {_safe_error(exc)}"
            ) from exc

    @staticmethod
    def _normalize_arch(value: str) -> str:
        normalized = value.lower()
        return {"x86_64": "amd64", "aarch64": "arm64"}.get(
            normalized, normalized
        )

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        if spec.is_oci:
            await self.prepare_image(spec.effective_image_ref)
        try:
            return await asyncio.to_thread(self._create, spec)
        except ImageNotFound:
            # Cache may be pruned between inspect and create. Re-pull only the
            # same immutable digest, verify it again, then retry once.
            if not spec.is_oci:
                raise
            repository, digest = parse_digest_reference(spec.effective_image_ref)
            await asyncio.to_thread(
                self._pull_exact, repository, digest, spec.effective_image_ref
            )
            await self.inspect_image(spec.effective_image_ref)
            return await asyncio.to_thread(self._create, spec)

    def _create(self, spec: SandboxSpec) -> SandboxHandle:
        network = self._client.networks.get(spec.network)
        network.reload()
        if network.attrs.get("Internal") is not True:
            raise ValueError(f"sandbox network {spec.network!r} is not internal")

        resource_id = spec.effective_resource_id
        name = (
            f"zentra-run-{resource_id}"
            if not spec.is_oci
            else f"zentra-{spec.role}-{resource_id}"
        )
        labels = {
            "zentra.managed": "true",
            "zentra.role": spec.role,
            "zentra.resource_id": resource_id,
        }
        if spec.mode is SandboxMode.VALIDATE:
            labels["zentra.agent_version_id"] = resource_id
        elif spec.mode is SandboxMode.SERVE:
            labels["zentra.agent_id"] = resource_id
        else:
            labels["zentra.run_id"] = resource_id

        kwargs: dict[str, Any] = {
            "image": spec.effective_image_ref,
            "name": name,
            "detach": True,
            "network": spec.network,
            "environment": spec.sandbox_environment(),
            "labels": labels,
            "working_dir": "/workspace",
            "user": "65534:65534",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only": True,
            "tmpfs": {
                "/workspace": (
                    f"rw,nosuid,nodev,noexec,size={spec.workspace_size},"
                    "uid=65534,gid=65534"
                ),
            },
            "volumes": {},
            "ports": {},
            "privileged": False,
            "mem_limit": spec.mem_limit,
            "memswap_limit": spec.mem_limit,
            "pids_limit": spec.pids_limit,
            "nano_cpus": spec.nano_cpus,
            "oom_kill_disable": False,
            "stdin_open": False,
            "tty": False,
            # 一次性沙盒不需要 healthcheck（跑完就走）。常駐容器需要 ——
            # 它掛掉時沒有「任務失敗」可以觀察，只有沒人取工作這個沉默的症狀。
            "healthcheck": {"test": ["NONE"]},
            "log_config": LogConfig(
                type="json-file", config={"max-size": "10m", "max-file": "1"}
            ),
        }
        if spec.is_oci:
            kwargs.update(
                entrypoint=[OCI_RUNTIME_EXECUTABLE], command=[spec.mode.value]
            )
            if spec.mode is SandboxMode.SERVE:
                # 常駐容器：Docker 自己不重啟它。重啟是平台的決定，因為要記
                # restart_count —— 一直重啟的容器是壞掉了，不是需要再重啟一次。
                # 交給 Docker 的 restart_policy 會讓那個訊號消失。
                kwargs["restart_policy"] = {"Name": "no"}
        else:
            # ZEN-000 的內建 fixture 走這條（非 OCI），它的 image 裡沒有 zentra-agent。
            kwargs["command"] = ["python", "-m", "zentra_runtime"]

        try:
            container = self._client.containers.create(**kwargs)
        except ImageNotFound:
            raise
        except (APIError, OSError) as exc:
            stage = "INSPECT" if spec.mode is SandboxMode.VALIDATE else "SANDBOX"
            raise SandboxCreateError(
                f"failed to create {spec.role} container: {_safe_error(exc)}",
                stage=stage,
                retryable=spec.mode is SandboxMode.RUN,
            ) from exc
        try:
            container.start()
        except Exception as exc:
            try:
                container.remove(force=True)
            except Exception:
                log.exception("failed to remove unstarted %s container", spec.role)
            stage = "INSPECT" if spec.mode is SandboxMode.VALIDATE else "SANDBOX"
            raise SandboxCreateError(
                f"failed to start {spec.role} container: {_safe_error(exc)}",
                stage=stage,
                retryable=spec.mode is SandboxMode.RUN,
            ) from exc
        log.info(
            "%s container created resource_id=%s container=%s",
            spec.role,
            resource_id,
            container.id[:12],
        )
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
            return SandboxExit(
                exit_code=-1, logs=f"Docker wait failed: {_safe_error(exc)}"
            )

    async def _timeout_exit(self, handle: SandboxHandle, container: Any) -> SandboxExit:
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

    async def inspect_container(self, docker_id: str) -> dict:
        """容器的原始 inspect（ZEN-015 的生命週期管理要看 `State`）。

        找不到容器不是例外 —— 它可能已經被清掉了。回一個「不在跑」的形狀，
        讓呼叫端用同一條路處理。
        """
        def _run() -> dict:
            try:
                return self._client.containers.get(docker_id).attrs
            except NotFound:
                return {"State": {"Running": False, "ExitCode": None}}
        return await asyncio.to_thread(_run)

    async def container_logs(self, docker_id: str, tail: int = 100) -> str:
        """容器輸出。它死掉時這是唯一的線索，所以取不到也要說為什麼。"""
        def _run() -> str:
            try:
                raw = self._client.containers.get(docker_id).logs(tail=tail)
            except NotFound:
                return "(the container is already gone; no output available)"
            except APIError as exc:
                return f"(could not read the container output: {_safe_error(exc)})"
            return raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        return await asyncio.to_thread(_run)

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
            l4_filesystem_isolation=True,
            l5_sdk_interception=False,  # Runtime/SDK owns L5; driver cannot claim it.
        )
