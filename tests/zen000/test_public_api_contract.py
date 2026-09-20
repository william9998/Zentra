"""Black-box ZEN-000 API→worker→sandbox→SSE contract tests.

Run against a live four-service compose deployment with
ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 python -m pytest tests/zen000.
An unset URL skips only the live tests; QA must report that as untested.
"""

import json
import os
import time
from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest


AGENT_ID = "01930000-0000-7000-8000-000000000001"
OTHER_AGENT_ID = "01930000-0000-7000-8000-000000000099"
POST_KEYS = {"run_id", "agent_id", "agent_version", "status", "created_at", "events_url"}
GET_KEYS = {
    "run_id", "agent_id", "agent_version", "status", "trigger", "input", "output",
    "error", "usage", "created_at", "started_at", "finished_at",
}
ALLOWED_EVENTS = {"run.status", "node.start", "node.end", "token", "log", "custom", "error"}


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    return parsed


@pytest.fixture(scope="module")
def client():
    base = os.getenv("ZENTRA_TEST_BASE_URL")
    if not base:
        pytest.skip("Set ZENTRA_TEST_BASE_URL to run live ZEN-000 API tests")
    with httpx.Client(base_url=base, timeout=httpx.Timeout(30.0, connect=3.0)) as current:
        health = current.get("/healthz")
        assert health.status_code == 200, health.text
        yield current


@pytest.fixture(scope="module")
def completed_run(client):
    response = client.post(
        f"/v1/agents/{AGENT_ID}/runs",
        json={"entrypoint": "default", "input": {"question": "hello"}},
    )
    assert response.status_code == 202, response.text
    created = response.json()
    assert set(created) == POST_KEYS
    assert UUID(created["run_id"]).version == 7
    assert created["agent_id"] == AGENT_ID
    assert created["agent_version"] == "0.0.0-builtin"
    assert created["status"] == "QUEUED"
    assert created["events_url"] == f"/v1/runs/{created['run_id']}/events"
    _utc(created["created_at"])

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        fetched = client.get(f"/v1/runs/{created['run_id']}")
        assert fetched.status_code == 200, fetched.text
        run = fetched.json()
        assert set(run) == GET_KEYS
        if run["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            assert run["status"] == "SUCCEEDED", run
            return created, run
        assert run["status"] in {"QUEUED", "SCHEDULING", "RUNNING"}
        time.sleep(0.2)
    pytest.fail(f"Run did not finish within 45s: {run}")


def test_run_response_matches_v022_contract(completed_run) -> None:
    created, run = completed_run
    assert run["run_id"] == created["run_id"]
    assert run["agent_id"] == AGENT_ID
    assert run["agent_version"] == "0.0.0-builtin"
    assert run["trigger"] == "API"
    assert run["input"] == {"question": "hello"}
    assert run["output"] == {"echo": "hello"}
    assert run["error"] is None
    assert run["usage"]["llm_calls"] == 0
    assert run["usage"]["input_tokens"] == 0
    assert run["usage"]["output_tokens"] == 0
    assert run["usage"]["cost_usd"] == "0"
    assert _utc(run["created_at"]) <= _utc(run["started_at"]) <= _utc(run["finished_at"])


def _sse_messages(response: httpx.Response):
    fields: dict[str, str] = {}
    for line in response.iter_lines():
        if line == "":
            if {"id", "event", "data"} <= fields.keys():
                yield {
                    "id": int(fields["id"]), "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            fields = {}
        elif ":" in line and not line.startswith(":"):
            name, value = line.split(":", 1)
            fields[name] = value.lstrip()


def test_sse_has_ordered_node_events_and_one_terminal_status(client, completed_run) -> None:
    created, _ = completed_run
    with client.stream("GET", created["events_url"], headers={"Accept": "text/event-stream"}) as response:
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        messages = list(_sse_messages(response))
    ids = [message["id"] for message in messages]
    assert ids == sorted(set(ids)), messages
    assert all(message["event"] in ALLOWED_EVENTS for message in messages)
    node_events = [
        (message["data"].get("node"), message["event"])
        for message in messages if message["event"] in {"node.start", "node.end"}
    ]
    assert node_events == [
        ("echo_node", "node.start"), ("echo_node", "node.end"),
        ("finalize_node", "node.start"), ("finalize_node", "node.end"),
    ]
    terminal = [m for m in messages if m["event"] == "run.status" and m["data"].get("status") == "SUCCEEDED"]
    assert len(terminal) == 1
    assert messages[-1] == terminal[0]


@pytest.mark.parametrize("body,code,status", [
    ({"entrypoint": "default"}, "VALIDATION_ERROR", 422),
    ({"input": {"question": 42}}, "VALIDATION_ERROR", 422),
    ({"input": {}}, "VALIDATION_ERROR", 422),
    ({"input": {"question": "x"}, "entrypoint": "missing"}, "VALIDATION_ERROR", 422),
    ({"input": {"question": "x"}, "version": "9.9.9"}, "AGENT_NOT_PUBLISHED", 409),
    ({"input": {"question": "x"}, "config": {"timeout_seconds": 0}}, "VALIDATION_ERROR", 422),
    ({"input": {"question": "x"}, "config": {"timeout_seconds": 901}}, "VALIDATION_ERROR", 422),
])
def test_invalid_create_uses_contract_error(client, body, code, status) -> None:
    response = client.post(f"/v1/agents/{AGENT_ID}/runs", json=body)
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert set(error) == {"code", "message", "run_id", "retryable", "details"}
    assert error["code"] == code
    assert error["retryable"] is False
    assert isinstance(error["details"], dict)


def test_unknown_agent_and_internal_openapi_exclusion(client) -> None:
    response = client.post(
        f"/v1/agents/{OTHER_AGENT_ID}/runs", json={"input": {"question": "x"}},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    schema = client.get("/openapi.json").json()
    assert all(not path.startswith("/internal/") for path in schema["paths"])
