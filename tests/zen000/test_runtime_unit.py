"""ZEN-000 runtime contract checks that never call an external service."""

import asyncio
import json
from uuid import UUID

import httpx
import pytest

pytest.importorskip("langgraph", reason="Runtime image provides LangGraph")

from apps.sandbox.runtime.__main__ import _execute, _post_with_retry
from apps.sandbox.runtime.builtin_agent import build


RUN_ID = "01930000-0000-7000-8000-000000000011"
GATEWAY = "http://app:8000/internal"


def test_builtin_build_returns_uncompiled_graph_and_echoes() -> None:
    graph = build()
    assert type(graph).__name__ == "StateGraph"
    assert not hasattr(graph, "ainvoke")
    result = asyncio.run(graph.compile().ainvoke({"question": "hello"}))
    assert result["echo"] == "hello"


def test_runtime_posts_node_events_and_result_as_gateway_contract() -> None:
    received: list[tuple[str, object]] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/bootstrap"):
            return httpx.Response(200, json={
                "run_id": RUN_ID, "entrypoint": "default",
                "input": {"question": "hello"}, "manifest": {},
            })
        body = json.loads(request.content)
        received.append((request.url.path, body))
        return httpx.Response(202, json={"accepted": 1})

    async def invoke() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as client:
            await _execute(client, GATEWAY, RUN_ID)

    asyncio.run(invoke())
    event_batches = [body for path, body in received if path.endswith("/events")]
    assert len(event_batches) == 4
    assert all(isinstance(batch, list) and len(batch) == 1 for batch in event_batches)
    events = [batch[0] for batch in event_batches]
    assert {e["type"] for e in events} == {"node.start", "node.end"}
    assert [(e["payload"]["node"], e["type"]) for e in events] == [
        ("echo_node", "node.start"),
        ("echo_node", "node.end"),
        ("finalize_node", "node.start"),
        ("finalize_node", "node.end"),
    ]
    assert len({e["event_id"] for e in events}) == 4
    assert all(UUID(e["event_id"]).version == 7 for e in events)
    assert all(set(e) == {"event_id", "type", "payload"} for e in events)
    result_posts = [body for path, body in received if path.endswith("/result")]
    assert len(result_posts) == 1
    assert result_posts[0]["output"] == {"echo": "hello"}
    assert result_posts[0]["error"] is None
    assert result_posts[0]["usage"]["cost_usd"] == "0"
    assert all(e["type"] != "run.status" for e in events)


def test_retry_reuses_identical_event_id_and_payload() -> None:
    payload = [{
        "event_id": "01930000-0000-7000-8000-000000000012",
        "type": "node.start", "payload": {"node": "echo_node"},
    }]
    attempts: list[object] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        attempts.append(json.loads(request.content))
        return httpx.Response(503 if len(attempts) == 1 else 202)

    async def invoke() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as client:
            await _post_with_retry(client, f"{GATEWAY}/runs/{RUN_ID}/events", payload)

    asyncio.run(invoke())
    assert attempts == [payload, payload]
