"""Zentra 效能測試（Locust）。

三種使用者，依權重混合（1:3:3，壓力集中在真的會跑 Agent 的兩種）：

| 使用者 | 打什麼 | 量的是什麼 |
|---|---|---|
| `ApiReadUser` | `/healthz`、`GET /v1/agents`、`GET /v1/runs` | app 本身 + Postgres 的讀取，不碰容器 |
| `InvokeUser`  | `POST /v1/agents/{id}/runs:invoke` | 完整一次 Run：派工 → 常駐容器 → 回結果 |
| `McpUser`     | `/mcp` 的 `tools/list`、`tools/call` | MCP 門面，`tools/call` 同樣走完整 Run |

預設打測試 Agent `probe-agent`（`make fixture` 註冊），它走的是和使用者 Agent 一樣的常駐容器路線。
每筆 Run 預設模擬等待 1 秒（`ZENTRA_WORK_MS`），代表「等 LLM 回應」這種真實 Agent 的主要耗時。

要量記憶體，就讓每筆 Run 帶得像真的一樣重：`ZENTRA_QUESTION_CHARS`（輸入多長）、
`ZENTRA_CONTEXT_KB`（等待期間 state 裡留多少資料）、`ZENTRA_REPLY_KB`（回答多長）。
`ZENTRA_MODE=llm` + `ZENTRA_MOCK_LLM_URL` 則改走真的 LLM 連線，對端是 `mock_llm.py`。

只想壓某一種：在指令最後加類別名稱，例如 `locust InvokeUser`。

設定全部走環境變數（見 README.md）。沒給 `ZENTRA_MCP_KEY` 時，測試開始會自動發一把
只勾選目標 Agent 的 MCP key，結束時撤銷。
"""

import json
import logging
import os
import time

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
MODE = os.environ.get("ZENTRA_MODE", "simulate")
QUESTION_CHARS = int(os.environ.get("ZENTRA_QUESTION_CHARS", "0"))
CONTEXT_KB = int(os.environ.get("ZENTRA_CONTEXT_KB", "0"))
REPLY_KB = int(os.environ.get("ZENTRA_REPLY_KB", "0"))
#: 假 LLM 的位址，**從 app 容器看出去**的（所以是 host.docker.internal，不是 localhost）。
MOCK_LLM_URL = os.environ.get("ZENTRA_MOCK_LLM_URL", "")


def _probe_input() -> dict:
    """probe-agent 的輸入。旋鈕是 0 就不帶，讓預設的輕量壓測和以前送的一模一樣。"""
    question = "locust"
    if QUESTION_CHARS > len(question):
        question = ("使用者貼進來的長文字。" * (QUESTION_CHARS // 10 + 1))[:QUESTION_CHARS]
    run_input = {"question": question, "work_ms": WORK_MS}
    if MODE != "simulate":
        run_input["mode"] = MODE
    if CONTEXT_KB:
        run_input["context_kb"] = CONTEXT_KB
    if REPLY_KB:
        run_input["reply_kb"] = REPLY_KB
    return run_input


#: 換成別的 Agent 時，用 ZENTRA_INPUT 給符合它 input_schema 的輸入（上面那些旋鈕就不適用）。
RUN_INPUT = json.loads(os.environ["ZENTRA_INPUT"]) if "ZENTRA_INPUT" in os.environ else _probe_input()

#: MCP tool 名稱 `{agent.name}__{entrypoint}`（specs/mcp.md §Tool 是怎麼生出來的）。
#: 在 test_start 依 Agent 名稱補上。
MCP_TOOL = os.environ.get("ZENTRA_MCP_TOOL", "")

_mcp_key = os.environ.get("ZENTRA_MCP_KEY", "")
_auto_key_id = ""
_mock_credential_set = False

TERMINAL_OK = "SUCCEEDED"


@events.test_start.add_listener
def _prepare(environment, **_):
    """測試開始前：找出目標 Agent、推出 tool 名稱，必要時發一把 MCP key。"""
    global AGENT_ID, MCP_TOOL, _mcp_key, _auto_key_id, _mock_credential_set
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

    if MOCK_LLM_URL:
        _point_llm_at_mock(host)
        _mock_credential_set = True


def _point_llm_at_mock(host: str) -> None:
    """把目標 Agent 的 LLM 憑證指向假 LLM。結束時刪掉，Agent 退回平台預設金鑰。

    已經有憑證就不動：API 讀不回原本的金鑰，蓋掉就還原不了。
    """
    url = f"{host}/v1/agents/{AGENT_ID}/llm-credential"
    current = requests.get(url, timeout=10)
    current.raise_for_status()
    if current.json().get("configured"):
        raise RuntimeError("目標 Agent 已經設了 LLM 憑證，蓋掉就還原不了；"
                           "要用假 LLM 請先在 Console 刪掉它，或改用別的 Agent")
    resp = requests.put(url, timeout=10, json={
        "api_key": "mock-llm-not-a-real-key", "base_url": MOCK_LLM_URL, "default_model": "mock"})
    resp.raise_for_status()
    logger.info("LLM of %s now points at %s", AGENT_ID, MOCK_LLM_URL)


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
    """撤銷自動發的 MCP key、拿掉指向假 LLM 的憑證。手動給的 key 不動。"""
    host = environment.host.rstrip("/")
    if _auto_key_id:
        requests.delete(f"{host}/v1/mcp-keys/{_auto_key_id}", timeout=10)
        logger.info("revoked auto-created MCP key")
    if _mock_credential_set:
        _remove_mock_credential(host)


#: 停止後最多等多久讓剩下的 Run 跑完，才拿掉指向假 LLM 的憑證。
DRAIN_SECONDS = 180


def _remove_mock_credential(host: str) -> None:
    """**等這個 Agent 沒有未完成的 Run 才拿掉憑證。**

    憑證一拿掉，Agent 就退回平台預設的**真金鑰**。Locust 停了，排隊中的 Run 還在，
    這時拿掉，它們會帶著大 prompt 打到真的 LLM 供應商 —— 實際踩過。
    等不完就不拿：留著的憑證指向已經關掉的假 LLM，剩下的 Run 只會失敗，不會花錢。
    """
    deadline = time.monotonic() + DRAIN_SECONDS
    while time.monotonic() < deadline:
        pending = [
            requests.get(f"{host}/v1/runs", timeout=10, params={
                "agent_id": AGENT_ID, "status": status, "limit": 1}).json()["items"]
            for status in ("QUEUED", "SCHEDULING", "RUNNING")
        ]
        if not any(pending):
            requests.delete(f"{host}/v1/agents/{AGENT_ID}/llm-credential", timeout=10)
            logger.info("removed mock LLM credential")
            return
        time.sleep(3)
    logger.warning("還有 Run 沒跑完，假 LLM 的憑證先留著（避免它們打到真的供應商）。"
                   "跑完後手動刪：curl -X DELETE %s/v1/agents/%s/llm-credential", host, AGENT_ID)


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
