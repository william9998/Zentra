"""Driver controls tested without claiming real Docker isolation has passed."""

import asyncio

import pytest
pytest.importorskip("docker.errors", reason="Docker SDK is required for driver unit tests")
from docker.errors import NotFound

from apps.sandbox.drivers.base import SandboxSpec
from apps.sandbox.drivers.docker_driver import DockerSandboxDriver


RUN_ID = "01930000-0000-7000-8000-000000000011"


class FakeNetwork:
    def __init__(self, internal: bool) -> None:
        self.attrs = {"Internal": internal}

    def reload(self) -> None:
        pass


class FakeContainer:
    id = "sandbox-container-id"

    def __init__(self) -> None:
        self.started = False
        self.removed = False

    def start(self) -> None:
        self.started = True

    def remove(self, *, force: bool) -> None:
        assert force is True
        self.removed = True


class FakeClient:
    def __init__(self, internal: bool = True) -> None:
        self.network = FakeNetwork(internal)
        self.container = FakeContainer()
        self.create_kwargs = None
        self.networks = self
        self.containers = self

    def get(self, name: str):
        if name == "zentra-sandbox-net":
            return self.network
        if self.container.removed:
            raise NotFound("container already removed")
        return self.container

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.container


def _spec() -> SandboxSpec:
    return SandboxSpec(
        run_id=RUN_ID, token="short-lived-token", image="zentra/agent-runtime:dev",
        network="zentra-sandbox-net", gateway="http://api:8000/internal",
    )


def test_create_uses_only_internal_network_and_run_scoped_environment() -> None:
    client = FakeClient()
    driver = DockerSandboxDriver(client=client)
    handle = driver._create(_spec())
    assert handle.container_id == client.container.id
    assert client.container.started
    kwargs = client.create_kwargs
    assert kwargs["network"] == "zentra-sandbox-net"
    assert kwargs["name"] == f"zentra-run-{RUN_ID}"
    assert kwargs["environment"] == {
        "ZENTRA_RUN_TOKEN": "short-lived-token",
        "ZENTRA_GATEWAY": "http://api:8000/internal",
        "ZENTRA_RUN_ID": RUN_ID,
    }
    assert kwargs["user"] == "65534:65534"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["read_only"] is True
    assert "docker.sock" not in str(kwargs)
    asyncio.run(driver.destroy(handle))
    asyncio.run(driver.destroy(handle))
    assert client.container.removed


def test_non_internal_network_fails_before_container_creation() -> None:
    client = FakeClient(internal=False)
    with pytest.raises(ValueError, match="not internal"):
        DockerSandboxDriver(client=client)._create(_spec())
    assert client.create_kwargs is None


def test_capabilities_do_not_claim_kernel_isolation() -> None:
    """ZEN-008 更新：L4 已經做出來了，斷言跟著改成 True。

    佐證（不是看程式碼，是實測過的真容器）：`_create()` 送 `read_only=True`，
    `docker inspect` 的 `HostConfig.ReadonlyRootfs` 為 true，沙盒內寫 `/`、`/app`、
    `/opt/zentra` 都是 PermissionError —— 見
    `tests/zen008/test_sandbox_security.py::test_sandbox_cannot_write_outside_workspace`。

    **L2 這條不准放寬。** MVP 沒有 gVisor（ADR-0005），driver 宣稱有核心隔離
    比沒有更危險。L5 同理：攔截層屬 runtime/SDK，driver 保證不了。

    ⚠️ 這裡的 L4 指「read-only rootfs + 只有 /workspace tmpfs 可寫」。
    image 自己宣告 VOLUME 造成的可寫路徑是另一個開放缺陷（ZEN-008-D1），
    由 `tests/zen008/test_image_volume_escape.py` 盯著，不在這個旗標的語意裡。
    """
    caps = asyncio.run(DockerSandboxDriver(client=FakeClient()).capabilities())
    assert caps["l1_internal_network"] is True
    assert caps["l2_kernel_isolation"] is False
    assert caps["l3_resource_limits"] is True
    assert caps["l4_read_only_filesystem"] is True
    assert caps["l5_application_controls"] is False
