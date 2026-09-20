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


def test_capabilities_do_not_claim_kernel_or_full_filesystem_isolation() -> None:
    caps = asyncio.run(DockerSandboxDriver(client=FakeClient()).capabilities())
    assert caps["l1_internal_network"] is True
    assert caps["l2_kernel_isolation"] is False
    assert caps["l4_read_only_filesystem"] is False
    assert caps["l5_application_controls"] is False
