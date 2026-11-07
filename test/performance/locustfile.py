"""Zentra 效能測試（Locust）。

三種使用者，依權重混合（1:3:3，壓力集中在真的會跑 Agent 的兩種）：

| 使用者 | 打什麼 | 量的是什麼 |
|---|---|---|
| `ApiReadUser` | `/healthz`、`GET /v1/agents`、`GET /v1/runs` | app 本身 + Postgres 的讀取，不碰容器 |
| `InvokeUser`  | `POST /v1/agents/{id}/runs:invoke` | 完整一次 Run：派工 → 常駐容器 → 回結果 |
| `McpUser`     | `/mcp` 的 `tools/list`、`tools/call` | MCP 門面，`tools/call` 同樣走完整 Run |

**要打哪些 Agent 寫在 `targets.toml`**：可以列很多個，各自設比重、入口、輸入。
每次呼叫前依比重隨機挑一個，結果表按 Agent 分開列。預設只有測試 Agent `probe-agent`
（`make fixture` 註冊），它走的是和使用者 Agent 一樣的常駐容器路線。
probe-agent 每筆 Run 預設模擬等待 1 秒（`ZENTRA_WORK_MS`），代表「等 LLM 回應」這種真實 Agent 的主要耗時。

要量記憶體，就讓每筆 Run 帶得像真的一樣重：`ZENTRA_QUESTION_CHARS`（輸入多長）、
`ZENTRA_CONTEXT_KB`（等待期間 state 裡留多少資料）、`ZENTRA_REPLY_KB`（回答多長）。
`ZENTRA_MODE=llm` + `ZENTRA_MOCK_LLM_URL` 則改走真的 LLM 連線，對端是 `mock_llm.py`。
（這幾個旋鈕只作用在 targets.toml 裡沒寫 `input` 的 probe-agent。）

只想壓某一種：在指令最後加類別名稱，例如 `locust InvokeUser`。

其餘設定走環境變數（見 README.md）。沒給 `ZENTRA_MCP_KEY` 時，測試開始會自動發一把
勾選清單裡所有 Agent 的 MCP key，結束時撤銷。
"""

import json
import logging
import os
import random
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import requests
from locust import FastHttpUser, between, events, task

logger = logging.getLogger(__name__)

#: 測試 Agent（examples/probe-agent）。不打 LLM，用 work_ms 模擬等待外部服務。
FIXTURE_NAME = "probe-agent"

#: 要打哪些 Agent。相對路徑以這支檔案的目錄為準。
TARGETS_FILE = Path(__file__).parent / os.environ.get("ZENTRA_TARGETS", "targets.toml")

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


@dataclass
class Target:
    """targets.toml 裡的一個 Agent。`agent_id` 在 test_start 依名稱查出來。"""
    name: str
    weight: int
    entrypoint: str
    input: dict
    agent_id: str = ""

    @property
    def mcp_tool(self) -> str:
        # `{agent.name}__{entrypoint}`（specs/mcp.md §Tool 是怎麼生出來的）
        return f"{self.name}__{self.entrypoint}"


def _load_targets(path: Path) -> list[Target]:
    """讀清單、檢查寫法。寫錯就在開始前停下來，不要壓到一半才發現。"""
    with path.open("rb") as f:
        entries = tomllib.load(f).get("agent", [])
    if not entries:
        raise RuntimeError(f"{path.name} 裡沒有任何 [[agent]]")
    targets = []
    for entry in entries:
        name = entry.get("name")
        if not name:
            raise RuntimeError(f"{path.name}：每個 [[agent]] 都要有 name")
        if "input" in entry:
            run_input = entry["input"]
        elif name == FIXTURE_NAME:
            run_input = _probe_input()
        else:
            raise RuntimeError(f"{path.name}：{name} 沒有寫 input（只有 {FIXTURE_NAME} 可以省略）")
        targets.append(Target(name=name, weight=int(entry.get("weight", 1)),
                              entrypoint=entry.get("entrypoint", "default"), input=run_input))
    return targets


TARGETS = _load_targets(TARGETS_FILE)


def _pick() -> Target:
    return random.choices(TARGETS, weights=[t.weight for t in TARGETS])[0]


_mcp_key = os.environ.get("ZENTRA_MCP_KEY", "")
_auto_key_id = ""
#: 這次壓測把 LLM 換成假 LLM 的 Agent，結束時要換回來
_mocked: list[Target] = []

TERMINAL_OK = "SUCCEEDED"


@events.test_start.add_listener
def _prepare(environment, **_):
    """測試開始前：依名稱找出每個 Agent，必要時發一把 MCP key、換上假 LLM。

    **準備失敗就整個停掉。** Locust 預設只印出例外、照樣開始壓：每一筆都打到不存在的
    Agent、全部失敗，最後還以 exit code 0 結束 —— 看起來像跑完了一次壓測。
    """
    try:
        _prepare_targets(environment.host.rstrip("/"))
    except Exception as exc:                       # noqa: BLE001
        logger.error("壓測準備失敗，不開始：%s", exc)
        environment.process_exit_code = 1
        environment.runner.quit()


def _prepare_targets(host: str) -> None:
    global _mcp_key, _auto_key_id

    for target in TARGETS:
        target.agent_id = _find_agent(host, target.name)
    logger.info("targets: %s", ", ".join(f"{t.name}×{t.weight}" for t in TARGETS))

    if not _mcp_key:
        resp = requests.post(f"{host}/v1/mcp-keys", timeout=10, json={
            "name": "locust-perf", "agent_ids": sorted({t.agent_id for t in TARGETS})})
        resp.raise_for_status()
        body = resp.json()
        _mcp_key, _auto_key_id = body["key"], body["key_id"]
        logger.info("auto-created MCP key %s", body["key_prefix"])

    if MOCK_LLM_URL:
        _point_llm_at_mock(host)


def _point_llm_at_mock(host: str) -> None:
    """把清單裡**每個** Agent 的 LLM 憑證指向假 LLM。結束時刪掉，Agent 退回平台預設金鑰。

    全部換、不挑：漏掉一個，那個 Agent 壓測期間每一筆都打到真的供應商。
    有任何一個已經有自己的憑證就整個不開始：API 讀不回原本的金鑰，蓋掉就還原不了。
    """
    unique = list({t.agent_id: t for t in TARGETS}.values())
    owned = [t.name for t in unique if requests.get(
        f"{host}/v1/agents/{t.agent_id}/llm-credential", timeout=10).json().get("configured")]
    if owned:
        raise RuntimeError(f"這些 Agent 已經設了自己的 LLM 憑證，蓋掉就還原不了：{', '.join(owned)}。"
                           "要用假 LLM 請先在 Console 刪掉，或從 targets.toml 拿掉它們")
    for target in unique:
        resp = requests.put(f"{host}/v1/agents/{target.agent_id}/llm-credential", timeout=10, json={
            "api_key": "mock-llm-not-a-real-key", "base_url": MOCK_LLM_URL, "default_model": "mock"})
        resp.raise_for_status()
        _mocked.append(target)
        logger.info("LLM of %s now points at %s", target.name, MOCK_LLM_URL)


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
    if _mocked:
        _remove_mock_credentials(host)


#: 停止後最多等多久讓剩下的 Run 跑完，才拿掉指向假 LLM 的憑證。
DRAIN_SECONDS = 180


def _remove_mock_credentials(host: str) -> None:
    """**等這些 Agent 都沒有未完成的 Run 才拿掉憑證。**

    憑證一拿掉，Agent 就退回平台預設的**真金鑰**。Locust 停了，排隊中的 Run 還在，
    這時拿掉，它們會帶著大 prompt 打到真的 LLM 供應商 —— 實際踩過。
    等不完就不拿：留著的憑證指向已經關掉的假 LLM，剩下的 Run 只會失敗，不會花錢。
    """
    deadline = time.monotonic() + DRAIN_SECONDS
    remaining = list(_mocked)
    while remaining and time.monotonic() < deadline:
        for target in list(remaining):
            pending = [
                requests.get(f"{host}/v1/runs", timeout=10, params={
                    "agent_id": target.agent_id, "status": status, "limit": 1}).json()["items"]
                for status in ("QUEUED", "SCHEDULING", "RUNNING")
            ]
            if not any(pending):
                requests.delete(f"{host}/v1/agents/{target.agent_id}/llm-credential", timeout=10)
                logger.info("removed mock LLM credential of %s", target.name)
                remaining.remove(target)
        if remaining:
            time.sleep(3)
    for target in remaining:
        logger.warning("%s 還有 Run 沒跑完，假 LLM 的憑證先留著（避免它們打到真的供應商）。"
                       "跑完後手動刪：curl -X DELETE %s/v1/agents/%s/llm-credential",
                       target.name, host, target.agent_id)


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
        target = _pick()
        with self.client.post(
            f"/v1/agents/{target.agent_id}/runs:invoke",
            json={"entrypoint": target.entrypoint, "input": target.input},
            # 每個 Agent 各一列，才看得出是哪一個慢
            name=f"runs:invoke [{target.name}]",
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
        result = self._rpc("tools/list", {}, "mcp tools/list")
        if result is not None:
            listed = {t["name"] for t in result.get("tools", [])}
            for target in TARGETS:
                if target.mcp_tool not in listed:
                    logger.warning("tool %s not in tools/list", target.mcp_tool)

    @task(2)
    def tools_call(self):
        target = _pick()
        self._rpc("tools/call", {"name": target.mcp_tool, "arguments": target.input},
                  f"mcp tools/call [{target.name}]")


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
