"""ZEN-006A: the api must keep working when apps/console is absent.

Runs in a subprocess with CONSOLE_DIR pointing at a non-existent directory, so the
lru_cached Settings of the other tests are not disturbed. This covers the risk
@backend-dev flagged: StaticFiles stats its directory on the *first request*, and a
missing directory used to surface as a 500 rather than a 404.

    PYTHONPATH=. pytest tests/zen006a/test_api_without_console.py -v
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]

PROBE = r"""
import json, os, sys, tempfile
os.environ["CONSOLE_DIR"] = os.path.join(tempfile.mkdtemp(), "console-does-not-exist")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/15")
from fastapi.testclient import TestClient
from apps.api.config import get_settings
get_settings.cache_clear()
from apps.api.main import app

out = {}
with TestClient(app) as client:
    health = client.get("/healthz")
    out["healthz"] = [health.status_code, health.json()]
    schema = client.get("/openapi.json")
    out["openapi_status"] = schema.status_code
    out["openapi_paths"] = sorted(schema.json()["paths"])
    root = client.get("/", follow_redirects=False)
    out["root"] = [root.status_code, root.headers.get("location")]
    for path in ["/console/runs", "/console/runs/x", "/console/static/css/tokens.css",
                 "/console/agents"]:
        response = client.get(path)
        try:
            body = response.json()
        except Exception:
            body = None
        out[path] = [response.status_code, body]
print("PROBE_JSON " + json.dumps(out))
"""


@pytest.fixture(scope="module")
def probe():
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=REPO, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO), "HOME": str(Path.home())},
        timeout=120,
    )
    if result.returncode != 0:
        pytest.fail(f"probe failed rc={result.returncode}\nSTDOUT:\n{result.stdout}\n"
                    f"STDERR:\n{result.stderr}")
    line = [ln for ln in result.stdout.splitlines() if ln.startswith("PROBE_JSON ")]
    assert line, result.stdout
    return json.loads(line[0][len("PROBE_JSON "):])


def test_app_starts_and_healthz_works_without_console(probe) -> None:
    assert probe["healthz"] == [200, {"status": "ok"}]


def test_openapi_still_clean_without_console(probe) -> None:
    assert probe["openapi_status"] == 200
    # ZEN-008 更新：Console 從兩頁擴成五頁，公開 API 也從三條擴成 v0.3.1 的十條。
    # 這裡仍然是**精確集合**，不是「至少包含」—— 多一條 UI 路由或 /internal 就該紅。
    assert set(probe["openapi_paths"]) == {
        "/v1/agents",
        "/v1/agents/{agent_id}",
        "/v1/agents/{agent_id}/openapi.json",
        "/v1/agents/{agent_id}/llm-credential",
        "/v1/agents/{agent_id}/api-keys",
        "/v1/agents/{agent_id}/api-keys/{key_id}",
        "/v1/agents/{agent_id}/containers",
        "/v1/agents/{agent_id}/containers/{container_uuid}",
        "/v1/agents/{agent_id}/containers/{container_uuid}/restart",
        "/v1/agents/{agent_id}/runs",
        "/v1/agents/{agent_id}/runs:invoke",
        "/v1/agents/{agent_id}/versions",
        "/v1/agents/{agent_id}/versions/{semver}",
        "/v1/runs",
        "/v1/runs/{run_id}",
        "/v1/runs/{run_id}/events",
    }


def test_root_still_redirects_without_console(probe) -> None:
    # ZEN-008：agents.html 已交付，`/` 依 ui-contract 轉回 /console/agents
    assert probe["root"] == [307, "/console/agents"]


@pytest.mark.parametrize("path", [
    "/console/runs", "/console/runs/x", "/console/static/css/tokens.css", "/console/agents",
])
def test_missing_console_is_404_never_500(probe, path: str) -> None:
    status, _ = probe[path]
    assert status == 404, f"{path} -> {status}"


@pytest.mark.parametrize("path", ["/console/runs", "/console/runs/x"])
def test_missing_page_uses_the_contract_error_envelope(probe, path: str) -> None:
    """api-contract §0: the routes this slice added must answer with the standard envelope."""
    status, body = probe[path]
    assert status == 404
    assert body is not None and "error" in body, body
    assert body["error"]["code"] == "NOT_FOUND"
    assert set(body["error"]) == {"code", "message", "run_id", "retryable", "details"}
