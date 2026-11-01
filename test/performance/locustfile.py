"""Zentra 效能測試（Locust）。

三種使用者，依權重混合（1:3:3，壓力集中在真的會跑 Agent 的兩種）：

| 使用者 | 打什麼 | 量的是什麼 |
|---|---|---|
| `ApiReadUser` | `/healthz`、`GET /v1/agents`、`GET /v1/runs` | app 本身 + Postgres 的讀取，不碰容器 |
| `InvokeUser`  | `POST /v1/agents/{id}/runs:invoke` | 完整一次 Run：派工 → 常駐容器 → 回結果 |
| `McpUser`     | `/mcp` 的 `tools/list`、`tools/call` | MCP 門面，`tools/call` 同樣走完整 Run |

預設打測試 Agent `probe-agent`（`make fixture` 註冊），它走的是和使用者 Agent 一樣的常駐容器路線。
每筆 Run 預設模擬等待 1 秒（`ZENTRA_WORK_MS`），代表「等 LLM 回應」這種真實 Agent 的主要耗時。

只想壓某一種：在指令最後加類別名稱，例如 `locust InvokeUser`。

設定全部走環境變數（見 README.md）。沒給 `ZENTRA_MCP_KEY` 時，測試開始會自動發一把
只勾選目標 Agent 的 MCP key，結束時撤銷。
"""

import json
import logging
import os

import requests
from locust import FastHttpUser, between, events, task

logger = logging.getLogger(__name__)

#: 測試 Agent（examples/probe-agent）。不打 LLM，用 work_ms 模擬等待外部服務。
FIXTURE_NAME = "probe-agent"

#: 沒指定就在 test_start 依 `ZENTRA_AGENT_NAME` 找出 id。
AGENT_ID = os.environ.get("ZENTRA_AGENT_ID", "")
AGENT_NAME = os.environ.get("ZENTRA_AGENT_NAME", FIXTURE_NAME)
ENTRYPOINT = os.environ.get("ZENTRA_ENTRYPOINT", "default")
WORK_MS = int(os.environ.get("ZENTRA_WORK_MS", "1000"))
#: 換成別的 Agent 時，用 ZENTRA_INPUT 給符合它 input_schema 的輸入（ZENTRA_WORK_MS 就不適用）。
RUN_INPUT = json.loads(os.environ.get(
    "ZENTRA_INPUT", json.dumps({"question": "locust", "work_ms": WORK_MS})))

#: MCP tool 名稱 `{agent.name}__{entrypoint}`（specs/mcp.md §Tool 是怎麼生出來的）。
#: 在 test_start 依 Agent 名稱補上。
MCP_TOOL = os.environ.get("ZENTRA_MCP_TOOL", "")

_mcp_key = os.environ.get("ZENTRA_MCP_KEY", "")
_auto_key_id = ""

TERMINAL_OK = "SUCCEEDED"


@events.test_start.add_listener
def _prepare(environment, **_):
    """測試開始前：找出目標 Agent、推出 tool 名稱，必要時發一把 MCP key。"""
    global AGENT_ID, MCP_TOOL, _mcp_key, _auto_key_id
    host = environment.host.rstrip("/")

    if not AGENT_ID:
        AGENT_ID = _find_agent(host, AGENT_NAME)

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


def _find_agent(host: str, name: str) -> str:
    """沒有依名稱查的端點，只能翻頁找（每頁上限 100）。"""
    params: dict = {"limit": 100}
    while True:
        resp = requests.get(f"{host}/v1/agents", params=params, timeout=10)
        resp.raise_for_status()
        page = resp.json()
        for item in page["items"]:
            if item["name"] == name:
                return item["agent_id"]
        if not page.get("next_cursor"):
            hint = "（先跑 make fixture）" if name == FIXTURE_NAME else ""
            raise RuntimeError(f"agent {name!r} not found{hint}")
        params["cursor"] = page["next_cursor"]


@events.test_stop.add_listener
def _cleanup(environment, **_):
    """撤銷自動發的 MCP key。手動給的 key 不動。"""
    if _auto_key_id:
        host = environment.host.rstrip("/")
        requests.delete(f"{host}/v1/mcp-keys/{_auto_key_id}", timeout=10)
        logger.info("revoked auto-created MCP key")


class ApiReadUser(FastHttpUser):
    """只讀 REST，不建 Run。"""

    weight = 1
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

    weight = 3
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

    weight = 3
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
            # 解析失敗要記成失敗。讓例外往外丟的話，Locust 會把這筆請求整個略過不計，
            # 結果表上既沒有這一列，也沒有錯誤 —— 實際踩過。
            try:
                message = _parse_mcp(resp.content)
            except ValueError as exc:
                resp.failure(f"unparseable MCP response ({exc}): {resp.text[:200]}")
                return None
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


def _parse_mcp(body: bytes) -> dict | None:
    """Streamable HTTP 可能回純 JSON，也可能回 SSE（`data: {...}`），兩種都收。

    **自己用 UTF-8 解碼，不要用 `resp.text`。** SSE 回應的 content-type 沒帶 charset，
    HTTP 函式庫會當成 latin-1 解，中文變亂碼；其中有些亂碼字元（如 U+0085）會被
    `splitlines()` 當成換行，把 JSON 從中間切斷。只依 SSE 規定的 `\\n` 切行。
    """
    text = body.decode("utf-8").strip()
    if text.startswith("{"):
        return json.loads(text)
    for line in text.split("\n"):
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    return None
