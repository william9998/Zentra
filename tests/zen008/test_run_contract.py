"""Z8-25 / Z8-26 / Z8-32：版本選擇、REST 建 Run、trigger 權威來源與事件契約。

契約 v0.3.1 §1 明文：「只接受上表定義的 top-level 欄位；包含 `trigger` 在內的未知欄位
回 `422 VALIDATION_ERROR` 且不建立 Run。任何 client header（包含 `X-Zentra-Trigger`、
`Origin`、`Referer`）都不能改寫 Run trigger。」Q-008 的裁決就落在這些斷言上。
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.enums import ErrorCode, EventType, RunStatus, RunTrigger
from apps.api.models import Run, RunEvent
from tests.zen008.conftest import (
    db_url,
    new_agent,
    register_version,
    wait_run,
    wait_version,
)

REMOVED_VALUES = ("WAITING_INPUT", "tool.call", "tool.result", "interrupt", "tier")


def _db():
    return create_async_engine(db_url())


async def _count_runs(agent_id: str) -> int:
    engine = _db()
    try:
        async with async_sessionmaker(engine)() as session:
            return await session.scalar(select(func.count()).select_from(Run)
                                        .where(Run.agent_id == uuid.UUID(agent_id)))
    finally:
        await engine.dispose()


# --------------------------------------------------------------------- Z8-26


def test_create_run_response_matches_contract(api: httpx.Client, published_agent: dict) -> None:
    agent_id = published_agent["agent_id"]
    response = api.post(f"/v1/agents/{agent_id}/runs",
                        json={"entrypoint": "default", "input": {"question": "shape"}})
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"run_id", "agent_id", "agent_version", "status",
                         "created_at", "events_url"}
    assert body["status"] == RunStatus.QUEUED
    assert body["agent_id"] == agent_id
    assert body["agent_version"] == published_agent["semver"]
    assert body["events_url"] == f"/v1/runs/{body['run_id']}/events"
    assert uuid.UUID(body["run_id"]).version == 7


def test_run_object_keeps_every_nullable_contract_field(
    api: httpx.Client, published_agent: dict
) -> None:
    """契約 §1：「`output`、`error`、`usage`、`started_at`、`finished_at` 在尚未產生時為
    `null`，**欄位仍須出現**」。"""
    created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs",
                       json={"input": {"question": "nullable"}}).json()
    fresh = api.get(f"/v1/runs/{created['run_id']}").json()
    assert set(fresh) == {"run_id", "agent_id", "agent_version", "status", "trigger",
                          "input", "output", "error", "usage",
                          "created_at", "started_at", "finished_at"}
    terminal = wait_run(api, created["run_id"])
    assert terminal["status"] == RunStatus.SUCCEEDED, terminal
    assert terminal["error"] is None
    assert terminal["usage"]["llm_calls"] == 0
    assert terminal["usage"]["cost_usd"] == "0", "cost_usd 必須是 decimal 字串"
    assert isinstance(terminal["usage"]["cost_usd"], str)


async def test_trigger_in_request_body_is_rejected(
    api: httpx.Client, published_agent: dict
) -> None:
    """Q-008：trigger 是平台接入協定，不是 client 可以宣告的東西。"""
    agent_id = published_agent["agent_id"]
    before = await _count_runs(agent_id)
    for value in ("MCP", "API", "CONSOLE", "anything"):
        response = api.post(f"/v1/agents/{agent_id}/runs", json={
            "entrypoint": "default", "input": {"question": "t"}, "trigger": value,
        })
        assert response.status_code == 422, (value, response.text)
        assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert await _count_runs(agent_id) == before, "被拒絕的請求仍然建立了 Run"


@pytest.mark.parametrize("headers", [
    {"X-Zentra-Trigger": "MCP"},
    {"Origin": "http://localhost:8000"},
    {"Referer": "http://localhost:8000/console/agents"},
    {"X-Trigger": "CONSOLE", "X-Zentra-Ingress": "MCP", "User-Agent": "mcp-client/1.0"},
])
def test_no_client_header_can_rewrite_trigger(
    api: httpx.Client, published_agent: dict, headers: dict
) -> None:
    created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs",
                       json={"input": {"question": "hdr"}}, headers=headers)
    assert created.status_code == 202, created.text
    assert api.get(f"/v1/runs/{created.json()['run_id']}").json()["trigger"] == RunTrigger.API


@pytest.mark.parametrize("body, reason", [
    ({"input": {}}, "缺 required"),
    ({"input": {"question": 1}}, "型別錯"),
    ({"input": {"question": "x", "extra": 1}}, "additionalProperties"),
    ({"input": {"question": "x"}, "entrypoint": "nope"}, "未知 entrypoint"),
    ({"input": {"question": "x"}, "config": {"timeout_seconds": 0}}, "timeout 0"),
    ({"input": {"question": "x"}, "config": {"timeout_seconds": 901}}, "timeout 901"),
    ({"input": {"question": "x"}, "config": {"timeout_seconds": "60"}}, "timeout 型別"),
    ({"entrypoint": "default"}, "缺 input"),
    ({"input": {"question": "x"}, "image_ref": "r/x@sha256:" + "ab" * 32}, "夾帶 image_ref"),
    ({"input": {"question": "x"}, "command": ["id"]}, "夾帶 command"),
], ids=lambda value: value if isinstance(value, str) else "")
async def test_invalid_run_requests_create_nothing(
    api: httpx.Client, published_agent: dict, body: dict, reason: str
) -> None:
    agent_id = published_agent["agent_id"]
    before = await _count_runs(agent_id)
    response = api.post(f"/v1/agents/{agent_id}/runs", json=body)
    assert response.status_code == 422, (reason, response.text)
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert await _count_runs(agent_id) == before, reason


def test_invoke_uses_the_same_path_and_returns_a_full_run(
    api: httpx.Client, published_agent: dict
) -> None:
    marker = f"invoke-{uuid.uuid4().hex[:6]}"
    response = api.post(f"/v1/agents/{published_agent['agent_id']}/runs:invoke",
                        json={"input": {"question": marker}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == RunStatus.SUCCEEDED
    assert body["trigger"] == RunTrigger.API
    assert body["output"] == {"echo": marker, "mark": "v1"}


# --------------------------------------------------------------------- Z8-25


def test_explicit_published_semver_is_honoured(api: httpx.Client, published_agent: dict) -> None:
    created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs", json={
        "input": {"question": "pin"}, "version": published_agent["semver"],
    })
    assert created.status_code == 202, created.text
    assert created.json()["agent_version"] == published_agent["semver"]


async def test_omitted_version_uses_the_latest_published(
    api: httpx.Client, image_refs: dict
) -> None:
    """兩個 PUBLISHED 版本時，省略 version 必須選到 semver 最大的那個。

    擋住的錯：用 created_at 或字串比大小排序（"1.10.0" < "1.9.0"）。
    """
    agent_id = new_agent(api, "zen008-latest")
    assert register_version(api, agent_id, "1.9.0", image_refs["valid_v1"]).status_code == 202
    assert wait_version(api, agent_id, "1.9.0")["status"] == "PUBLISHED"
    assert register_version(api, agent_id, "1.10.0", image_refs["valid_v2"]).status_code == 202
    assert wait_version(api, agent_id, "1.10.0")["status"] == "PUBLISHED"

    created = api.post(f"/v1/agents/{agent_id}/runs", json={"input": {"question": "latest"}})
    assert created.status_code == 202, created.text
    assert created.json()["agent_version"] == "1.10.0"
    run = wait_run(api, created.json()["run_id"])
    assert run["output"]["mark"] == "v2", "選到的版本與實際執行的 image 不一致"


@pytest.mark.parametrize("version", ["2.0.0", "0.0.1", None])
async def test_unpublished_versions_are_agent_not_published(
    api: httpx.Client, version: str | None
) -> None:
    """BUILDING / FAILED / 未知版本一律 409，且不得建立 Run 或派工。"""
    agent_id = new_agent(api, "zen008-unpub")
    # 這個 digest 在 Registry 不存在 → 會停在 FAILED；註冊瞬間則是 BUILDING
    register_version(api, agent_id, "2.0.0", "registry.example.com/x/y@sha256:" + "ab" * 32)
    body: dict = {"input": {"question": "x"}}
    if version is not None:
        body["version"] = version
    response = api.post(f"/v1/agents/{agent_id}/runs", json=body)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == ErrorCode.AGENT_NOT_PUBLISHED
    assert await _count_runs(agent_id) == 0


def test_run_on_unknown_agent_is_not_found(api: httpx.Client) -> None:
    response = api.post(f"/v1/agents/{uuid.uuid4()}/runs", json={"input": {"question": "x"}})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND


# --------------------------------------------------------------------- Z8-32


async def test_terminal_run_has_exactly_one_terminal_event_and_legal_types(
    api: httpx.Client, published_agent: dict
) -> None:
    created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs",
                       json={"input": {"question": "events"}}).json()
    run = wait_run(api, created["run_id"])
    assert run["status"] == RunStatus.SUCCEEDED

    engine = _db()
    try:
        async with async_sessionmaker(engine)() as session:
            rows = (await session.execute(select(RunEvent).where(
                RunEvent.run_id == uuid.UUID(created["run_id"]))
                .order_by(RunEvent.seq))).scalars().all()
    finally:
        await engine.dispose()

    assert rows, "Run 沒有任何事件"
    assert sum(1 for row in rows if row.terminal) == 1, "終局事件不是恰好一個"
    assert rows[-1].terminal is True, "終局事件不是最後一筆"
    legal = {value.value for value in EventType}
    assert {row.type for row in rows} <= legal, {row.type for row in rows} - legal
    assert len({row.event_id for row in rows}) == len(rows), "同一 Run 出現重複 event_id"
    assert [row.seq for row in rows] == sorted(row.seq for row in rows)

    statuses = [row.payload.get("status") for row in rows if row.type == EventType.RUN_STATUS]
    assert statuses[-1] == RunStatus.SUCCEEDED
    assert set(statuses) <= {value.value for value in RunStatus}


def test_sse_stream_carries_no_removed_enum_values(
    api: httpx.Client, published_agent: dict
) -> None:
    created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs",
                       json={"input": {"question": "sse"}}).json()
    wait_run(api, created["run_id"])
    with api.stream("GET", f"/v1/runs/{created['run_id']}/events",
                    headers={"Accept": "text/event-stream"}, timeout=30) as stream:
        chunks, terminal = [], False
        for line in stream.iter_lines():
            chunks.append(line)
            if line.startswith("data:") and '"SUCCEEDED"' in line:
                terminal = True
            if terminal and not line:
                break
    raw = "\n".join(chunks)
    assert "run.status" in raw, raw[:400]
    for removed in REMOVED_VALUES:
        assert removed not in raw, f"SSE 位元組裡出現已移除的 {removed}"


def test_run_list_only_exposes_contract_trigger_values(api: httpx.Client) -> None:
    body = api.get("/v1/runs", params={"limit": 100}).json()
    assert "next_cursor" in body, f"Run 列表缺 next_cursor：keys={sorted(body)}"
    for item in body["items"]:
        assert item["trigger"] in {RunTrigger.API, RunTrigger.MCP}, item
        assert item["status"] in {value.value for value in RunStatus}, item
        assert "input" not in item and "output" not in item, "列表不得含 input/output"
    assert REMOVED_VALUES[0] not in json.dumps(body)


def test_internal_gateway_is_absent_from_public_openapi(api: httpx.Client) -> None:
    """Z8-11：`/internal/*` 是沙盒專用契約，不得出現在公開 OpenAPI。"""
    document = api.get("/openapi.json").json()
    leaked = [path for path in document["paths"] if path.startswith("/internal")]
    assert not leaked, leaked
    assert "validation" not in json.dumps(document["paths"])
