"""ZEN-006A live HTTP tests: Console page routes, static assets, and the three
endpoints the Console is allowed to use (api-contract v0.2.2).

Run against the live four-service compose deployment:

    ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 PYTHONPATH=. pytest tests/zen006a -v

An unset URL skips the live tests. A skip is not a pass: QA must report it as
untested.
"""

import json
import os
import time
from pathlib import Path
from uuid import UUID

import httpx
import pytest


REPO = Path(__file__).resolve().parents[2]
CONSOLE = REPO / "apps" / "console"

AGENT_ID = "01930000-0000-7000-8000-000000000001"
CREATE_KEYS = {"run_id", "agent_id", "agent_version", "status", "created_at", "events_url"}
GET_KEYS = {
    "run_id", "agent_id", "agent_version", "status", "trigger", "input", "output",
    "error", "usage", "created_at", "started_at", "finished_at",
}
ALLOWED_EVENTS = {"run.status", "node.start", "node.end", "token", "log", "custom", "error"}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}

STATIC_ASSETS = [
    ("css/tokens.css", "text/css"),
    ("css/base.css", "text/css"),
    ("css/layout.css", "text/css"),
    ("icons.svg", "image/svg+xml"),
    ("js/api.js", "text/javascript"),
    ("js/dom.js", "text/javascript"),
    ("js/store.js", "text/javascript"),
    ("js/shell.js", "text/javascript"),
    ("js/components/run-timeline.js", "text/javascript"),
    ("js/pages/run.js", "text/javascript"),
    ("js/pages/runs.js", "text/javascript"),
]


@pytest.fixture(scope="module")
def client():
    base = os.getenv("ZENTRA_TEST_BASE_URL")
    if not base:
        pytest.skip("Set ZENTRA_TEST_BASE_URL to run live ZEN-006A Console tests")
    with httpx.Client(base_url=base, timeout=httpx.Timeout(30.0, connect=3.0),
                      follow_redirects=False) as current:
        assert current.get("/healthz").status_code == 200
        yield current


@pytest.fixture(scope="module")
def finished_run(client):
    """One real Run through the ZEN-000 fixture agent, used by the contract checks."""
    response = client.post(
        f"/v1/agents/{AGENT_ID}/runs",
        json={"entrypoint": "default", "input": {"question": "zen006a-qa"}},
    )
    assert response.status_code == 202, response.text
    created = response.json()
    deadline = time.monotonic() + 60
    run = None
    while time.monotonic() < deadline:
        run = client.get(f"/v1/runs/{created['run_id']}").json()
        if run["status"] in TERMINAL:
            break
        time.sleep(0.2)
    assert run is not None and run["status"] in TERMINAL, run
    return created, run


# ------------------------------------------------------------ page routes


def test_root_redirects_to_the_contract_landing_page(client) -> None:
    """ZEN-006A 的 D1 偏離（`/` → `/console/runs`）在 ZEN-008 收斂回契約了。

    agents.html 交付之後，轉址必須指回 ui-contract 表頭寫的那一頁，
    而且那一頁要真的在。"""
    response = client.get("/")
    assert response.status_code == 307
    assert response.headers["location"] == "/console/agents"
    assert client.get("/console/agents").status_code == 200, (
        "轉址目的地必須存在；ZEN-006A 當初的偏離就是為了不要轉到一個 404")


def test_console_runs_serves_the_current_runs_html(client) -> None:
    response = client.get("/console/runs")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.content == (CONSOLE / "runs.html").read_bytes(), (
        "served bytes differ from the repo: the api image is stale"
    )


@pytest.mark.parametrize("run_id", [
    "0193a7c0-0000-7000-8000-00000000abcd", "not-a-uuid", "0", "%20",
])
def test_console_run_page_serves_run_html_for_any_id(client, run_id: str) -> None:
    """Deliberate: the page fetches /v1/runs/{id} itself, so the route must not
    do an existence check (extra DB query + existence leak)."""
    response = client.get(f"/console/runs/{run_id}")
    assert response.status_code == 200, response.text
    assert response.content == (CONSOLE / "run.html").read_bytes()


def test_run_page_does_not_leak_run_existence(client, finished_run) -> None:
    created, _ = finished_run
    existing = client.get(f"/console/runs/{created['run_id']}")
    missing = client.get("/console/runs/0193a7c0-dead-7000-8000-00000000dead")
    assert existing.status_code == missing.status_code == 200
    assert existing.content == missing.content


@pytest.mark.parametrize("path", [
    # ZEN-008：/console/agents、/console/agents/{id}、/console/agents/{id}/versions/new
    # 已經是真的路由，從這張「不得存在」清單移到下面的 test_zen008_console_routes_exist。
    "/console", "/console/", "/console/runs/a/b", "/console/runs/a%2Fb",
    "/console/index.html", "/console/runs.html", "/console/static/",
])
def test_no_catch_all_or_spa_fallback(client, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404, f"{path} -> {response.status_code}"
    assert b"<!DOCTYPE html>" not in response.content, f"{path} served an HTML fallback"


@pytest.mark.parametrize("path, page", [
    ("/console/agents", "agents.html"),
    ("/console/agents/01930000-0000-7000-8000-000000000001", "agent.html"),
    ("/console/agents/01930000-0000-7000-8000-000000000001/versions/new", "version-new.html"),
])
def test_zen008_console_routes_serve_their_own_page(client, path: str, page: str) -> None:
    """五頁都必須是各自的檔案，不是一個 SPA fallback。"""
    response = client.get(path)
    assert response.status_code == 200, path
    assert response.content == (CONSOLE / page).read_bytes(), path


def test_static_mount_root_redirects_then_404s(client) -> None:
    """Starlette's mount adds a trailing-slash redirect; following it must still 404,
    i.e. the mount root is not a directory listing and not a fallback page."""
    response = client.get("/console/static")
    assert response.status_code in (307, 404)
    if response.status_code == 307:
        followed = client.get(response.headers["location"])
        assert followed.status_code == 404
        assert b"<!DOCTYPE html>" not in followed.content


def test_trailing_slash_redirect_is_harmless(client) -> None:
    response = client.get("/console/runs/")
    assert response.status_code in (200, 307)
    if response.status_code == 307:
        assert response.headers["location"].endswith("/console/runs")


# ------------------------------------------------------------ static assets


@pytest.mark.parametrize("asset,content_type", STATIC_ASSETS)
def test_static_asset_is_served_byte_identical(client, asset: str, content_type: str) -> None:
    response = client.get(f"/console/static/{asset}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(content_type)
    assert response.content == (CONSOLE / "static" / asset).read_bytes(), (
        f"{asset}: served bytes differ from the repo; rebuild the api image"
    )


@pytest.mark.parametrize("path", [
    "/console/static/js/api.js", "/console/static/icons.svg", "/console/runs",
])
def test_console_files_are_revalidated_every_time(client, path: str) -> None:
    """沒有 hash 檔名可以打破快取，只能叫瀏覽器每次回來問。

    少了這個，瀏覽器會拿新版 runs.js 配舊版 api.js —— 欄位出現了、內容卻是 `—`。
    """
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers.get("cache-control") == "no-cache"


@pytest.mark.parametrize("attempt", [
    "/console/static/../runs.html",
    "/console/static/..%2Fruns.html",
    "/console/static/%2e%2e/%2e%2e/apps/api/config.py",
    "/console/static/....//runs.html",
    "/console/static//etc/passwd",
    "/console/static/../../api/config.py",
])
def test_static_files_refuse_path_traversal(client, attempt: str) -> None:
    response = client.get(attempt)
    assert response.status_code in (400, 403, 404), f"{attempt} -> {response.status_code}"
    body = response.content
    assert b"<!DOCTYPE html>" not in body
    assert b"database_url" not in body and b"DOCTYPE" not in body


# ----------------------------------------------------- OpenAPI must stay clean


def test_openapi_contains_no_ui_routes(client) -> None:
    """P3: the OpenAPI document is the Agent's public contract; UI routes must not pollute it."""
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"])
    assert not [p for p in paths if p.startswith("/console")], paths
    assert "/" not in paths
    assert "/healthz" not in paths
    assert not [p for p in paths if p.startswith("/internal")], paths
    assert not [p for p in paths if p.startswith("/mcp")], paths
    # ZEN-008 擴張到 api-contract v0.3.1 的十條，仍是精確集合。
    assert paths == {
        "/v1/agents",
        "/v1/agents/{agent_id}",
        "/v1/agents/{agent_id}/openapi.json",
        "/v1/agents/{agent_id}/llm-credential",
        "/v1/agents/{agent_id}/api-keys",
        "/v1/agents/{agent_id}/api-keys/{key_id}",
        "/v1/agents/{agent_id}/usage",
        "/v1/agents/{agent_id}/llm-calls",
        "/v1/agents/{agent_id}/containers",
        "/v1/agents/{agent_id}/containers/{container_uuid}",
        "/v1/agents/{agent_id}/containers/{container_uuid}/restart",
        # ZEN-019（api-contract v0.12.0 §2.6–§2.10）
        "/v1/agents/{agent_id}/config",
        "/v1/agents/{agent_id}/secrets",
        "/v1/agents/{agent_id}/secrets/{name}",
        "/v1/agents/{agent_id}/egress-rules",
        "/v1/agents/{agent_id}/egress-rules/{rule_id}",
        "/v1/agents/{agent_id}/email-sender",
        "/v1/agents/{agent_id}/egress-calls",
        "/v1/agents/{agent_id}/runs",
        "/v1/agents/{agent_id}/runs:invoke",
        "/v1/agents/{agent_id}/versions",
        "/v1/agents/{agent_id}/versions/{semver}",
        "/v1/runs",
        "/v1/runs/{run_id}",
        "/v1/runs/{run_id}/events",
    }, paths


# -------------------------------------- the three endpoints the Console uses


def test_create_run_matches_contract_and_console_expectations(client) -> None:
    response = client.post(
        f"/v1/agents/{AGENT_ID}/runs",
        json={"entrypoint": "default", "input": {"question": "console-create"}},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == CREATE_KEYS
    assert UUID(body["run_id"]).version == 7
    assert body["status"] == "QUEUED"
    # api.js navigates to /console/runs/{run_id}; events_url must agree with the path
    # api.js builds for EventSource.
    assert body["events_url"] == f"/v1/runs/{body['run_id']}/events"


def test_get_run_matches_contract_null_semantics(client, finished_run) -> None:
    _, run = finished_run
    assert set(run) == GET_KEYS
    assert run["status"] in TERMINAL
    assert run["trigger"] == "API"
    if run["status"] == "SUCCEEDED":
        assert run["error"] is None
        assert isinstance(run["output"], dict)
    assert isinstance(run["usage"]["cost_usd"], str), "cost_usd must be a decimal string"
    assert run["usage"]["llm_calls"] == 0


def test_queued_run_has_null_but_present_optional_fields(client) -> None:
    created = client.post(
        f"/v1/agents/{AGENT_ID}/runs",
        json={"entrypoint": "default", "input": {"question": "console-null-check"}},
    ).json()
    run = client.get(f"/v1/runs/{created['run_id']}").json()
    assert set(run) == GET_KEYS, "fields must be present even when null (Console reads them)"


def test_invalid_input_returns_contract_error_envelope(client) -> None:
    """The dialog renders error.code / message straight from this envelope."""
    response = client.post(
        f"/v1/agents/{AGENT_ID}/runs", json={"entrypoint": "default", "input": {"question": 42}},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert set(error) == {"code", "message", "run_id", "retryable", "details"}
    assert error["code"] == "VALIDATION_ERROR"
    assert error["retryable"] is False


def test_missing_run_returns_contract_error_envelope(client) -> None:
    """The observation page renders NOT_FOUND from this envelope (acceptance R3)."""
    response = client.get("/v1/runs/0193a7c0-dead-7000-8000-00000000dead")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert set(error) == {"code", "message", "run_id", "retryable", "details"}


def _sse_messages(response: httpx.Response):
    fields: dict = {}
    for line in response.iter_lines():
        if line == "":
            if {"id", "event", "data"} <= fields.keys():
                yield {"id": int(fields["id"]), "event": fields["event"],
                       "data": json.loads(fields["data"])}
            fields = {}
        elif ":" in line and not line.startswith(":"):
            name, value = line.split(":", 1)
            fields[name] = value.lstrip()


def test_sse_stream_ends_at_terminal_event(client, finished_run) -> None:
    """The front end closes EventSource on the terminal status *because* the server
    ends the stream here and Last-Event-ID resume is not implemented (ZEN-004).
    If this invariant breaks, the Console reconnects forever and replays from seq 0.
    """
    created, _ = finished_run
    started = time.monotonic()
    with client.stream("GET", created["events_url"],
                       headers={"Accept": "text/event-stream"}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        messages = list(_sse_messages(response))
    assert time.monotonic() - started < 20, "stream did not end by itself"
    ids = [m["id"] for m in messages]
    assert ids == sorted(set(ids)), "seq must be unique and ascending (Console dedups on it)"
    assert {m["event"] for m in messages} <= ALLOWED_EVENTS
    terminal = [m for m in messages
                if m["event"] == "run.status" and m["data"].get("status") in TERMINAL]
    assert len(terminal) == 1, terminal
    assert messages[-1] == terminal[0], "terminal event must be last"


def test_sse_for_unknown_run_is_a_contract_error_not_a_stream(client) -> None:
    response = client.get(
        "/v1/runs/0193a7c0-dead-7000-8000-00000000dead/events",
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_no_waiting_input_status_is_ever_emitted(client, finished_run) -> None:
    created, run = finished_run
    assert run["status"] != "WAITING_INPUT"
    with client.stream("GET", created["events_url"]) as response:
        body = b"".join(response.iter_bytes())
    assert b"WAITING_INPUT" not in body
    assert b"tool.call" not in body and b"interrupt" not in body
