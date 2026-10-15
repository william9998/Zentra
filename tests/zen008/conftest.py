"""ZEN-008 驗收套件的共用 fixture。

全部對**跑著的 compose**（postgres / redis / api / worker）與**真 Docker daemon** 執行。
沒有 mock 後端、沒有 in-process TestClient —— acceptance §1 明講 fake driver 的結果
不能當隔離或發布的證據。

環境（實測過的，不要改成別的）：

    ZENTRA_TEST_BASE_URL   http://127.0.0.1:8000
    ZENTRA_TEST_DB_URL     postgresql://zentra:zentra@127.0.0.1:15432/zentra
    ZENTRA_TEST_REDIS_URL  redis://127.0.0.1:16379/0
    ZENTRA_TEST_REGISTRY   localhost:5010

15432/16379 是 socat 代理：主機原生 Postgres 佔住 5432，容器的 postgres 被遮蔽。
代理與測試 Registry 都是**測試期間**的容器，跑完要刪。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from typing import Any

import httpx
import pytest

from tests.zen008.fixtures import images

pytest_plugins = ("pytest_asyncio",)

POLL_TIMEOUT = float(os.getenv("ZENTRA_TEST_POLL_TIMEOUT", "120"))


# --------------------------------------------------------------------------- env


def base_url() -> str:
    value = os.getenv("ZENTRA_TEST_BASE_URL")
    if not value:
        pytest.skip("Set ZENTRA_TEST_BASE_URL to run live ZEN-008 tests")
    return value.rstrip("/")


def db_url() -> str:
    value = os.getenv("ZENTRA_TEST_DB_URL")
    if not value:
        pytest.skip("Set ZENTRA_TEST_DB_URL to run PostgreSQL assertions")
    from apps.api.config import normalize_database_url

    return normalize_database_url(value)


async def pg_connect():
    import json

    import asyncpg

    conn = await asyncpg.connect(db_url())
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(
            typ, encoder=json.dumps, decoder=json.loads,
            schema="pg_catalog", format="text")
    return conn


def redis_url() -> str:
    value = os.getenv("ZENTRA_TEST_REDIS_URL")
    if not value:
        pytest.skip("Set ZENTRA_TEST_REDIS_URL to run token assertions")
    return value


def require_docker() -> None:
    probe = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("Docker daemon is unavailable; integration evidence cannot be produced")


def require_registry() -> None:
    require_docker()
    running = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", images.REGISTRY_CONTAINER],
        capture_output=True, text=True)
    if running.stdout.strip() != "true":
        pytest.skip(
            f"Start the test registry first: docker run -d --name {images.REGISTRY_CONTAINER} "
            "-p 5010:5000 registry:2"
        )


# --------------------------------------------------------------------------- http


@pytest.fixture(scope="session")
def api() -> httpx.Client:
    with httpx.Client(base_url=base_url(), timeout=90) as client:
        yield client


def new_agent(api: httpx.Client, prefix: str = "zen008") -> str:
    response = api.post("/v1/agents", json={
        "name": f"{prefix}-{uuid.uuid4().hex[:10]}",
        "description": "ZEN-008 acceptance fixture",
    })
    assert response.status_code == 201, response.text
    return response.json()["agent_id"]


def register_version(api: httpx.Client, agent_id: str, semver: str, image_ref: str):
    return api.post(f"/v1/agents/{agent_id}/versions",
                    json={"semver": semver, "image_ref": image_ref})


def wait_version(api: httpx.Client, agent_id: str, semver: str,
                 *, timeout: float = POLL_TIMEOUT) -> dict[str, Any]:
    """輪詢到版本進入終態（PUBLISHED/FAILED）。逾時就是逾時，不要當成通過。"""
    deadline = time.time() + timeout
    body: dict[str, Any] = {}
    while time.time() < deadline:
        response = api.get(f"/v1/agents/{agent_id}/versions/{semver}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in {"PUBLISHED", "FAILED"}:
            return body
        time.sleep(1)
    raise AssertionError(f"version {semver} stayed {body.get('status')} for {timeout}s")


def wait_run(api: httpx.Client, run_id: str, *, timeout: float = POLL_TIMEOUT) -> dict[str, Any]:
    deadline = time.time() + timeout
    body: dict[str, Any] = {}
    while time.time() < deadline:
        response = api.get(f"/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return body
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} stayed {body.get('status')} for {timeout}s")


# --------------------------------------------------------------------------- MCP


def mcp(api: httpx.Client, agent_id: str, method: str,
        params: dict[str, Any] | None = None, *, request_id: int = 1) -> httpx.Response:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return api.post(
        f"/mcp/agents/{agent_id}",
        json=body,
        headers={"Accept": "application/json, text/event-stream"},
    )


def mcp_payload(response: httpx.Response) -> dict[str, Any]:
    """Streamable HTTP 回的是 SSE frame；取出唯一的 JSON-RPC message。"""
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"no SSE data frame in MCP response: {response.text[:400]}")


# --------------------------------------------------------------------------- images


@pytest.fixture(scope="session")
def image_refs() -> dict[str, str]:
    require_registry()
    return images.build_all()


@pytest.fixture(scope="session")
def published_agent(api: httpx.Client, image_refs: dict[str, str]) -> dict[str, Any]:
    """一個真的走完 register → pull → inspect → validate → PUBLISHED 的 Agent。

    session 範圍：發布一次要 20 秒上下，每條測試重發布只會讓套件變慢，
    不會多擋住任何錯誤。需要獨立版本的測試自己建。
    """
    agent_id = new_agent(api, "zen008-main")
    created = register_version(api, agent_id, "1.0.0", image_refs["valid_v1"])
    assert created.status_code == 202, created.text
    version = wait_version(api, agent_id, "1.0.0")
    assert version["status"] == "PUBLISHED", version
    return {"agent_id": agent_id, "semver": "1.0.0", "version": version,
            "image_ref": image_refs["valid_v1"]}


# --------------------------------------------------------------------------- docker


def docker_json(args: list[str]) -> Any:
    raw = subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout
    return json.loads(raw) if raw.strip() else None


def containers_with_label(label: str) -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label={label}", "--format", "{{.ID}}"],
        capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def wait_gone(label: str, *, timeout: float = 30) -> list[str]:
    deadline = time.time() + timeout
    remaining = containers_with_label(label)
    while remaining and time.time() < deadline:
        time.sleep(0.5)
        remaining = containers_with_label(label)
    return remaining
