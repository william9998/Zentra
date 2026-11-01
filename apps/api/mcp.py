"""MCP 門面（P3）：平台唯一的 `/mcp`，Streamable HTTP。

**一個接口，Agent 是 tools。** 呼叫端帶 MCP key（`mcp_keys`），`tools/list` 列出這把 key
勾選的每個 Agent、其最新 PUBLISHED 版本的每個 entrypoint；對方的 LLM 看 tool 清單決定呼叫哪個。

**與 REST 同源。** tool 的 `inputSchema` 用 `services.agents.mcp_tools()` —— 和 REST 驗證 `input`
用的是同一份 `version.entrypoints`，不是另外生成一套 schema。`tools/call` 走同一個
`services.runs.create_run` 與同一條 Celery 派工，只有 `trigger` 固定成 `MCP`，並記下 `mcp_key_id`。
規格：`specs/mcp.md`。

> **P1 提醒**：這裡沒有任何載入或執行使用者程式碼的路徑。MCP 只是另一個門面，
> 真正的執行仍然只發生在沙盒裡。

## 為什麼每個請求建一個 FastMCP app

FastMCP 的 Streamable HTTP app 需要自己的 lifespan 來起 session manager
（沒跑 lifespan 就會 `StreamableHTTPSessionManager task group was not initialized`）。
tool 清單又是**每把 key、每個版本不同**的，沒辦法用一個共用的 server。

選擇是：每個請求建一個 app、在它自己的 lifespan 裡跑完就收。代價是每次請求多一次
物件建構與幾次查詢；換到的是**沒有跨請求共用的可變狀態**，不必處理勾選變更或版本發布後的
快取失效，也不會有兩個請求共用同一個 session manager 的競態。單租戶內部工具這個取捨划算；
要改成快取請連同「勾選變更、版本發布後如何失效」一起設計，不要只是加個 dict。
"""

import asyncio
import json
import logging
import re
from typing import Any

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, ToolResult

from apps.api import db
from apps.api.deps import load_run
from apps.api.enums import ErrorCode, RunStatus, RunTrigger
from apps.api.errors import ZentraError
from apps.api.rows import Agent, McpKey
from apps.api.schemas import CreateRunRequest
from apps.api.services import agents as agents_service
from apps.api.services import mcp_keys
from apps.api.services.runs import create_run
from apps.api.tasks import dispatch_run

logger = logging.getLogger(__name__)

#: 同步等待上限。與 `POST /v1/agents/{id}/runs:invoke` 同值，兩個門面一致。
INVOKE_TIMEOUT_SECONDS = 60

#: `tools/call` 的保留參數：帶 `true` 就不等終態，直接回 run_id。
#: 底線開頭是為了不跟使用者 entrypoint 的欄位撞名。
ASYNC_FLAG = "_async"

#: tool 名稱的分隔：`{agent}__{entrypoint}`。
TOOL_SEPARATOR = "__"

_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def tool_name(agent_name: str, entrypoint_name: str) -> str:
    """`{agent}__{entrypoint}`。Agent 名稱允許 `.`，部分 MCP 客戶端不收，所以換成 `_`。"""
    return _UNSAFE_NAME_CHARS.sub("_", agent_name) + TOOL_SEPARATOR \
        + _UNSAFE_NAME_CHARS.sub("_", entrypoint_name)


class _EntrypointTool(Tool):
    """一個（Agent, entrypoint）= 一個 MCP tool。

    `parameters` 直接是 entrypoint 的 `input_schema`，**不重新生成**——
    P3 要求 MCP 與 REST 的 input schema 同源，這裡就是那個「同源」的落點。
    """

    agent_id: str
    entrypoint_name: str
    #: 哪一把 MCP key 觸發的。每個 `/mcp` 請求都有 key，所以一定有值。
    mcp_key_id: Any = None

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        args = dict(arguments)
        wants_async = bool(args.pop(ASYNC_FLAG, False))

        async with db.transaction() as conn:
            # create_run 會驗 input、解析已發布版本、寫入 Run（與 REST 同一條路）
            run, version = await create_run(
                conn,
                self.agent_id,
                CreateRunRequest(entrypoint=self.entrypoint_name, input=args),
                trigger=RunTrigger.MCP,
                mcp_key_id=self.mcp_key_id,
            )
            run_id = str(run.id)

        # 版本被存活檢查放棄：Run 建出來就是 FAILED（AGENT_NOT_RUNNING），不派工，
        # 直接走終態那條路回 isError —— 跟 REST 的 409 是同一件事。
        if RunStatus(run.status).is_terminal:
            return _terminal_result(run, run_id)

        # Broker I/O 是同步的，不要跑在事件迴圈上（與 routers/runs.py 同樣的理由）
        await asyncio.to_thread(dispatch_run, run_id)

        if wants_async:
            return ToolResult(structured_content={
                "run_id": run_id,
                "status": str(RunStatus.QUEUED),
                "agent_version": version.semver,
            })

        loop = asyncio.get_running_loop()
        deadline = loop.time() + INVOKE_TIMEOUT_SECONDS
        while loop.time() < deadline:
            async with db.connection() as conn:
                current = await load_run(run_id, conn)
            if RunStatus(current.status).is_terminal:
                return _terminal_result(current, run_id)
            await asyncio.sleep(0.1)

        raise ToolError(
            f"{ErrorCode.SANDBOX_TIMEOUT}: synchronous invocation exceeded "
            f"{INVOKE_TIMEOUT_SECONDS} seconds (run_id={run_id})"
        )


def _terminal_result(run: Any, run_id: str) -> ToolResult:
    """終態 → MCP 結果。失敗一律走 ToolError，讓客戶端看得到 isError。"""
    if run.status == RunStatus.SUCCEEDED:
        return ToolResult(structured_content=run.output or {})

    error = run.error or {}
    # error.code 只會是 api-contract §0 的枚舉值，不要在這裡自創字串
    code = error.get("code") or ErrorCode.INTERNAL_ERROR
    message = error.get("message") or f"run finished as {run.status}"
    raise ToolError(f"{code}: {message} (run_id={run_id})")


async def _collect_tools(conn: Any, key: McpKey) -> list[_EntrypointTool]:
    """這把 key 勾選的 Agent → tools。沒有 PUBLISHED 版本或已刪除的 Agent 直接略過。"""
    selected = (await mcp_keys.agent_ids_by_key(conn, [key.id]))[key.id]
    tools: list[_EntrypointTool] = []
    seen: set[str] = set()
    for agent_id in selected:
        try:
            agent: Agent = await agents_service.load_agent(conn, agent_id)
            version = await agents_service.resolve_published_version(conn, agent.id, None)
        except ZentraError:
            continue
        for item in agents_service.mcp_tools(version):
            name = tool_name(agent.name, item["name"])
            if name in seen:
                logger.warning("mcp tool name collision, skipped: %s (agent=%s)", name, agent.id)
                continue
            seen.add(name)
            tools.append(_EntrypointTool(
                name=name,
                description=f"[{agent.name}] {item['description']}".rstrip(),
                parameters=item["inputSchema"],
                output_schema=item["outputSchema"],
                agent_id=str(agent.id),
                entrypoint_name=item["name"],
                mcp_key_id=key.id,
            ))
    return tools


def _bearer(scope: dict) -> str:
    """從 ASGI scope 取出 Bearer token。

    這裡沒有 FastAPI 的依賴注入可用（MCP 是掛上去的原生 ASGI app），
    所以自己解 header。只認 `Authorization: Bearer`。
    """
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token if scheme.lower() == "bearer" and token else ""
    return ""


class _McpEndpoint:
    """掛在 `/mcp`（與 `/mcp/`）的 ASGI endpoint。

    不用 Starlette 的 Mount：Mount 會把 `/mcp` 以 307 轉到 `/mcp/`，
    而不少 MCP 客戶端不會在轉址時重送 POST body。
    """

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await _plain(send, 404, _error_body(ErrorCode.NOT_FOUND, "not found"))

        try:
            key = await _authenticate(_bearer(scope))
            async with db.connection() as conn:
                tools = await _collect_tools(conn, key)
        except ZentraError as exc:
            return await _plain(send, exc.http_status, exc.body())

        server = FastMCP(name="zentra")
        for tool in tools:
            server.add_tool(tool)
        app = server.http_app(path="/", stateless_http=True)

        # 內層 app 掛在 "/"，所以把 `/mcp` 併進 root_path，剩餘路徑才會是 "/"。
        inner_root = scope.get("root_path", "") + "/mcp"
        inner = dict(scope)
        inner["root_path"] = inner_root
        inner["path"] = inner_root + "/"
        inner["raw_path"] = inner["path"].encode()

        # 這個 app 的 lifespan 必須跑起來，session manager 才會初始化
        async with app.router.lifespan_context(app):
            await app(inner, receive, send)


async def _authenticate(presented: str) -> McpKey:
    """Bearer token → MCP key。沒帶、無效、已撤銷一律 401。"""
    if not presented:
        raise ZentraError(
            ErrorCode.UNAUTHENTICATED,
            "send the MCP key: Authorization: Bearer <zmcp_live_…>", 401,
        )
    async with db.transaction() as conn:
        key = await mcp_keys.resolve(conn, presented)
        if key is None:
            raise ZentraError(ErrorCode.UNAUTHENTICATED, "the MCP key is invalid or revoked", 401)
        await mcp_keys.touch(conn, key)
        return key


def _error_body(code: ErrorCode, message: str) -> dict:
    return {"error": {
        "code": str(code), "message": message,
        "run_id": None, "retryable": False, "details": {},
    }}


async def _plain(send: Any, status: int, body: dict) -> None:
    """用平台統一的錯誤格式回，不要讓 MCP 端點自創一套（契約 §0）。"""
    payload = json.dumps(body, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": payload})


def mount_mcp(app: FastAPI) -> None:
    """掛上 MCP 接口。main.py 只呼叫這一個函式。

    `/mcp` 是 MCP 協定端點，不是 REST，**不進公開 OpenAPI**
    （OpenAPI 描述的是 Agent 的 REST 門面）。
    """
    endpoint = _McpEndpoint()
    app.router.add_route("/mcp", endpoint, include_in_schema=False)
    app.router.add_route("/mcp/", endpoint, include_in_schema=False)
