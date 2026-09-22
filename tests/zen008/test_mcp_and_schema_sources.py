"""Z8-17 / Z8-27 / Z8-30：P3 —— 兩個門面、一份 entrypoint 定義。

`api-contract.md` §3 明文：「`tools/list` 的 input schema 與 REST 的 `input` schema
**必須由同一份 entrypoint 定義生成**。契約測試要驗證兩者等價 —— 這是 QA 的必測項。」

這一檔就是那一項，外加「MCP 真的走同一條 Run 路徑」的證據。
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.models import AgentVersion, Run
from tests.zen008.conftest import (
    db_url,
    mcp,
    mcp_payload,
    new_agent,
    register_version,
    wait_run,
)


def _db():
    return create_async_engine(db_url())


async def _entrypoints_from_db(agent_id: str) -> list[dict]:
    engine = _db()
    try:
        async with async_sessionmaker(engine)() as session:
            row = (await session.execute(select(AgentVersion).where(
                AgentVersion.agent_id == uuid.UUID(agent_id),
                AgentVersion.status == "PUBLISHED"))).scalars().first()
        assert row is not None, "沒有 PUBLISHED 版本可比對"
        return row.entrypoints or []
    finally:
        await engine.dispose()


def _tools(api: httpx.Client, agent_id: str) -> dict[str, dict]:
    response = mcp(api, agent_id, "tools/list")
    assert response.status_code == 200, response.text
    payload = mcp_payload(response)
    assert "error" not in payload, payload
    return {tool["name"]: tool for tool in payload["result"]["tools"]}


def _rest_input_schemas(api: httpx.Client, agent_id: str) -> dict[str, dict]:
    """從 Agent OpenAPI 取每個 entrypoint 的 input schema（$ref 解參考後）。"""
    document = api.get(f"/v1/agents/{agent_id}/openapi.json").json()
    components = document["components"]["schemas"]
    path = f"/v1/agents/{agent_id}/runs"
    body = document["paths"][path]["post"]["requestBody"]
    alternatives = body["content"]["application/json"]["schema"]["oneOf"]
    resolved: dict[str, dict] = {}
    for alternative in alternatives:
        name = alternative["properties"]["entrypoint"]["const"]
        ref = alternative["properties"]["input"]["$ref"]
        resolved[name] = components[ref.rsplit("/", 1)[-1]]
    return resolved


# --------------------------------------------------------------------- Z8-17


async def test_mcp_rest_and_db_input_schemas_are_the_same_object(
    api: httpx.Client, published_agent: dict
) -> None:
    """三處 input schema 逐欄位相同。

    擋住的錯：有人在 MCP 端「順手」重新生成一份 schema（例如把
    `additionalProperties` 丟掉、把 required 補齊、把型別放寬），
    於是同一個 Agent 用 REST 打得過、用 MCP 打不過，或反過來。
    """
    agent_id = published_agent["agent_id"]
    db_entrypoints = await _entrypoints_from_db(agent_id)
    tools = _tools(api, agent_id)
    rest = _rest_input_schemas(api, agent_id)

    assert db_entrypoints, "PUBLISHED 版本沒有 entrypoints"
    assert set(tools) == {item["name"] for item in db_entrypoints}, (
        "MCP 與 DB 的 entrypoint 名稱集合不同", sorted(tools), db_entrypoints)
    assert set(rest) == set(tools), ("只存在單一門面的 entrypoint", sorted(rest), sorted(tools))

    for item in db_entrypoints:
        name = item["name"]
        assert tools[name]["inputSchema"] == item["input_schema"], name
        assert rest[name] == item["input_schema"], name
        assert tools[name]["outputSchema"] == item["output_schema"], name
        assert tools[name]["description"] == item["description"], name


def test_tools_list_input_schema_keeps_additional_properties_false(
    api: httpx.Client, published_agent: dict
) -> None:
    """fixture 的 schema 刻意帶 `additionalProperties: false`。

    擋住的錯：MCP 層把使用者 schema 拿去當自己的參數模型重建，
    最容易掉的就是這個欄位 —— 掉了之後 MCP 端會放行 REST 端拒絕的 input。
    """
    schema = _tools(api, published_agent["agent_id"])["default"]["inputSchema"]
    assert schema.get("additionalProperties") is False
    assert schema["required"] == ["question"]


def test_openapi_is_generated_from_the_published_version(
    api: httpx.Client, published_agent: dict
) -> None:
    document = api.get(f"/v1/agents/{published_agent['agent_id']}/openapi.json").json()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == published_agent["semver"]


def test_openapi_for_unpublished_agent_is_agent_not_published(api: httpx.Client) -> None:
    agent_id = new_agent(api, "zen008-noopenapi")
    response = api.get(f"/v1/agents/{agent_id}/openapi.json")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AGENT_NOT_PUBLISHED"


# --------------------------------------------------------------------- Z8-27


async def test_mcp_tools_call_creates_a_run_with_mcp_trigger(
    api: httpx.Client, published_agent: dict
) -> None:
    agent_id = published_agent["agent_id"]
    marker = f"mcp-{uuid.uuid4().hex[:8]}"
    response = mcp(api, agent_id, "tools/call",
                   {"name": "default", "arguments": {"question": marker}})
    assert response.status_code == 200, response.text
    payload = mcp_payload(response)
    assert payload["result"]["isError"] is False, payload
    assert payload["result"]["structuredContent"] == {"echo": marker, "mark": "v1"}

    engine = _db()
    try:
        async with async_sessionmaker(engine)() as session:
            rows = (await session.execute(select(Run).where(
                Run.agent_id == uuid.UUID(agent_id)))).scalars().all()
        matching = [row for row in rows if row.input.get("question") == marker]
        assert len(matching) == 1, f"同步 tools/call 建立了 {len(matching)} 筆 Run"
        assert matching[0].trigger == "MCP"
        assert matching[0].status == "SUCCEEDED"
        assert matching[0].output == {"echo": marker, "mark": "v1"}
    finally:
        await engine.dispose()


def test_mcp_async_flag_returns_run_id_visible_on_public_api(
    api: httpx.Client, published_agent: dict
) -> None:
    marker = f"mcp-async-{uuid.uuid4().hex[:8]}"
    payload = mcp_payload(mcp(api, published_agent["agent_id"], "tools/call", {
        "name": "default", "arguments": {"question": marker, "_async": True},
    }))
    structured = payload["result"]["structuredContent"]
    assert set(structured) == {"run_id", "status", "agent_version"}
    assert structured["status"] == "QUEUED"
    assert structured["agent_version"] == published_agent["semver"]

    run = wait_run(api, structured["run_id"])
    assert run["status"] == "SUCCEEDED", run
    assert run["trigger"] == "MCP"
    assert run["output"] == {"echo": marker, "mark": "v1"}
    assert run["input"] == {"question": marker}, "`_async` 旗標不得落進 Run 的 input"


@pytest.mark.parametrize("arguments, tool", [
    ({"question": 123}, "default"),
    ({}, "default"),
    ({"question": "x", "unexpected": 1}, "default"),
    ({"question": "x"}, "no-such-tool"),
])
async def test_mcp_schema_errors_do_not_create_runs(
    api: httpx.Client, published_agent: dict, arguments: dict, tool: str
) -> None:
    agent_id = published_agent["agent_id"]
    engine = _db()
    try:
        async with async_sessionmaker(engine)() as session:
            before = len((await session.execute(select(Run.id).where(
                Run.agent_id == uuid.UUID(agent_id)))).scalars().all())
        payload = mcp_payload(mcp(api, agent_id, "tools/call",
                                  {"name": tool, "arguments": arguments}))
        is_error = payload.get("error") is not None or payload["result"].get("isError") is True
        assert is_error, payload
        async with async_sessionmaker(engine)() as session:
            after = len((await session.execute(select(Run.id).where(
                Run.agent_id == uuid.UUID(agent_id)))).scalars().all())
        assert after == before, "被拒絕的 tools/call 仍然建立了 Run"
    finally:
        await engine.dispose()


def test_mcp_unknown_agent_uses_the_platform_error_envelope(api: httpx.Client) -> None:
    response = mcp(api, str(uuid.uuid4()), "tools/list")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_mcp_paths_stay_out_of_the_public_openapi(api: httpx.Client) -> None:
    """`/mcp/*` 是 MCP 協定端點，不是 REST。混進 OpenAPI 會讓產生的 client 直接打錯。"""
    paths = api.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/mcp")], sorted(paths)


def test_mcp_on_unpublished_agent_is_agent_not_published(api: httpx.Client) -> None:
    agent_id = new_agent(api, "zen008-mcp-unpub")
    register_version(api, agent_id, "1.0.0",
                     "registry.example.com/acme/x@sha256:" + "ab" * 32)
    response = mcp(api, agent_id, "tools/list")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AGENT_NOT_PUBLISHED"


# --------------------------------------------------------------------- Z8-30


async def test_rest_and_mcp_runs_share_version_and_differ_only_by_trigger(
    api: httpx.Client, published_agent: dict
) -> None:
    """三門面同底層：REST（Console 也走這條）與 MCP 產生同一種 Run model。"""
    agent_id = published_agent["agent_id"]
    rest_marker, mcp_marker = f"rest-{uuid.uuid4().hex[:6]}", f"mcp-{uuid.uuid4().hex[:6]}"

    rest = api.post(f"/v1/agents/{agent_id}/runs",
                    json={"entrypoint": "default", "input": {"question": rest_marker}})
    assert rest.status_code == 202, rest.text
    rest_run = wait_run(api, rest.json()["run_id"])

    mcp_structured = mcp_payload(mcp(api, agent_id, "tools/call", {
        "name": "default", "arguments": {"question": mcp_marker, "_async": True},
    }))["result"]["structuredContent"]
    mcp_run = wait_run(api, mcp_structured["run_id"])

    assert rest_run["status"] == mcp_run["status"] == "SUCCEEDED"
    assert rest_run["trigger"] == "API"
    assert mcp_run["trigger"] == "MCP"
    assert rest_run["agent_version"] == mcp_run["agent_version"] == published_agent["semver"]

    engine = _db()
    try:
        async with async_sessionmaker(engine)() as session:
            rows = (await session.execute(select(Run).where(Run.id.in_([
                uuid.UUID(rest_run["run_id"]), uuid.UUID(mcp_run["run_id"])])))).scalars().all()
        assert len({row.agent_version_id for row in rows}) == 1, (
            "兩個門面指到不同的 agent_version_id")
        assert all(row.celery_task_id for row in rows), "有 Run 沒有經過 Celery 派工"
    finally:
        await engine.dispose()
