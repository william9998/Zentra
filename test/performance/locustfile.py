"""Zentra 效能測試（Locust）。

三種使用者，依權重混合：

| 使用者 | 打什麼 | 量的是什麼 |
|---|---|---|
| `ApiReadUser` | `/healthz`、`GET /v1/agents`、`GET /v1/runs` | app 本身 + Postgres 的讀取，不碰沙盒 |
| `InvokeUser`  | `POST /v1/agents/{id}/runs:invoke` | 完整一次 Run：派工 → 沙盒 → 回結果 |
| `McpUser`     | `/mcp` 的 `tools/list`、`tools/call` | MCP 門面，`tools/call` 同樣走完整 Run |

只想壓某一種：在指令最後加類別名稱，例如 `locust ApiReadUser`。

設定全部走環境變數（見 README.md）。沒給 `ZENTRA_MCP_KEY` 時，測試開始會自動發一把
只勾選目標 Agent 的 MCP key，結束時撤銷。
"""

import json
import logging
import os

import requests
from locust import FastHttpUser, between, events, task

logger = logging.getLogger(__name__)

#: 內建 fixture `builtin-echo`。沒有 LLM 呼叫，量到的是平台本身的開銷。
BUILTIN_ECHO = "01930000-0000-7000-8000-000000000001"

AGENT_ID = os.environ.get("ZENTRA_AGENT_ID", BUILTIN_ECHO)
ENTRYPOINT = os.environ.get("ZENTRA_ENTRYPOINT", "default")
RUN_INPUT = json.loads(os.environ.get("ZENTRA_INPUT", '{"question": "locust"}'))

#: MCP tool 名稱 `{agent.name}__{entrypoint}`（specs/mcp.md §Tool 是怎麼生出來的）。
#: 在 test_start 依 Agent 名稱補上。
MCP_TOOL = os.environ.get("ZENTRA_MCP_TOOL", "")

_mcp_key = os.environ.get("ZENTRA_MCP_KEY", "")
_auto_key_id = ""

TERMINAL_OK = "SUCCEEDED"


@events.test_start.add_listener
def _prepare(environment, **_):
    """測試開始前：查 Agent 名稱推出 tool 名稱，必要時發一把 MCP key。"""
    global MCP_TOOL, _mcp_key, _auto_key_id
    host = environment.host.rstrip("/")

    agent = requests.get(f"{host}/v1/agents/{AGENT_ID}", timeout=10)
    agent.raise_for_status()
    if not MCP_TOOL:
        MCP_TOOL = f"{agent.json()['name']}__{ENTRYPOINT}"

    if not _mcp_key:
        resp = requests.post(f"{host}/v1/mcp-keys", timeout=10,
                             json={"name": "locust-perf", "agent_ids": [AGENT_ID]})
        resp.raise_for_status()
        body = resp.json()
        _mcp_key, _auto_key_id = body["key"], body["key_id"]
        logger.info("auto-created MCP key %s", body["key_prefix"])


@events.test_stop.add_listener
def _cleanup(environment, **_):
    """撤銷自動發的 MCP key。手動給的 key 不動。"""
    if _auto_key_id:
        host = environment.host.rstrip("/")
        requests.delete(f"{host}/v1/mcp-keys/{_auto_key_id}", timeout=10)
        logger.info("revoked auto-created MCP key")


class ApiReadUser(FastHttpUser):
    """只讀 REST，不建 Run。"""

    weight = 3
    wait_time = between(0.5, 1.5)

    @task(1)
    def healthz(self):
        self.client.get("/healthz")

    @task(3)
    def list_agents(self):
        self.client.get("/v1/agents")

    @task(3)
    def list_runs(self):
        self.client.get("/v1/runs?limit=20", name="/v1/runs?limit=20")


class InvokeUser(FastHttpUser):
    """同步呼叫 Agent，等到終態才回（平台最多等 60 秒）。"""

    weight = 1
    wait_time = between(1, 3)
    # 比平台的 60 秒上限多一點，讓逾時由平台回 SANDBOX_TIMEOUT，而不是 Locust 先斷線
    network_timeout = 70.0
    connection_timeout = 10.0

    @task
    def invoke(self):
        with self.client.post(
            f"/v1/agents/{AGENT_ID}/runs:invoke",
            json={"entrypoint": ENTRYPOINT, "input": RUN_INPUT},
            name="/v1/agents/[id]/runs:invoke",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
                return
            run = resp.json()
            if run.get("status") != TERMINAL_OK:
                err = run.get("error") or {}
                resp.failure(f"{run.get('status')} {err.get('code')}: {err.get('message')}")


class McpUser(FastHttpUser):
    """外部 AI 平台的典型行為：先 tools/list，再 tools/call。"""

    weight = 1
    wait_time = between(1, 3)
    network_timeout = 70.0
    connection_timeout = 10.0

    def _rpc(self, method: str, params: dict, name: str):
        headers = {
            "Authorization": f"Bearer {_mcp_key}",
            "Accept": "application/json, text/event-stream",
        }
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        with self.client.post("/mcp", json=payload, headers=headers,
                              name=name, catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            message = _parse_mcp(resp.text)
            if message is None:
                resp.failure(f"unparseable MCP response: {resp.text[:200]}")
                return None
            if "error" in message:
                resp.failure(f"JSON-RPC error: {message['error']}")
                return None
            result = message.get("result", {})
            if result.get("isError"):
                text = (result.get("content") or [{}])[0].get("text", "")
                resp.failure(f"isError: {text[:200]}")
                return None
            return result

    @task(1)
    def tools_list(self):
        result = self._rpc("tools/list", {}, "/mcp tools/list")
        if result is not None and not any(t["name"] == MCP_TOOL for t in result.get("tools", [])):
            logger.warning("tool %s not in tools/list", MCP_TOOL)

    @task(2)
    def tools_call(self):
        self._rpc("tools/call", {"name": MCP_TOOL, "arguments": RUN_INPUT}, "/mcp tools/call")


def _parse_mcp(text: str) -> dict | None:
    """Streamable HTTP 可能回純 JSON，也可能回 SSE（`data: {...}`），兩種都收。"""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    return None
