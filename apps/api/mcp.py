"""MCP 門面（P3）：`/mcp/agents/{agent_id}`，Streamable HTTP。

**與 REST 同源。** `tools/list` 用 `services.agents.mcp_tools()` —— 和 REST 驗證 `input`
用的是同一份 `version.entrypoints`，不是另外生成一套 schema。`tools/call` 走同一個
`services.runs.create_run` 與同一條 Celery 派工，只有 `trigger` 固定成 `MCP`（spec §7）。
契約：`api-contract.md` §3。

> **P1 提醒**：這裡沒有任何載入或執行使用者程式碼的路徑。MCP 只是第二個門面，
> 真正的執行仍然只發生在沙盒裡。

## 為什麼每個請求建一個 FastMCP app

FastMCP 的 Streamable HTTP app 需要自己的 lifespan 來起 session manager
（沒跑 lifespan 就會 `StreamableHTTPSessionManager task group was not initialized`）。
tool 清單又是**每個 Agent 版本不同**的，沒辦法用一個共用的 server。

選擇是：每個請求建一個 app、在它自己的 lifespan 裡跑完就收。代價是每次請求多一次
物件建構；換到的是**沒有跨請求共用的可變狀態**，不必處理版本發布後的快取失效，
也不會有兩個請求共用同一個 session manager 的競態。單租戶 MVP 這個取捨划算；
要改成快取請連同「版本發布後如何失效」一起設計，不要只是加個 dict。
"""

import asyncio
import json
from typing import Any

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, ToolResult

from apps.api.db import SessionLocal
from apps.api.enums import ErrorCode, RunStatus, RunTrigger
from apps.api.errors import ZentraError
from apps.api.schemas import CreateRunRequest
from apps.api.services import agent_api_keys
from apps.api.services import agents as agents_service
from apps.api.services.runs import create_run
from apps.api.tasks import dispatch_run

#: 契約 §3：同步等待上限。與 `POST /v1/agents/{id}/runs:invoke` 同值，兩個門面一致。
INVOKE_TIMEOUT_SECONDS = 60

#: `tools/call` 的保留參數：帶 `true` 就不等終態，直接回 run_id（契約 §3）。
#: 底線開頭是為了不跟使用者 entrypoint 的欄位撞名。
ASYNC_FLAG = "_async"


class _EntrypointTool(Tool):
    """一個 entrypoint = 一個 MCP tool。

    `parameters` 直接是 entrypoint 的 `input_schema`，**不重新生成**——
    P3 要求 MCP 與 REST 的 input schema 同源，這裡就是那個「同源」的落點。
    """

    agent_id: str
    entrypoint_name: str
    #: 哪一把下游 key 觸發的（ZEN-015）。沒帶 key 就是 None，與 REST 同語意。
    api_key_id: Any = None

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        args = dict(arguments)
        wants_async = bool(args.pop(ASYNC_FLAG, False))

        async with SessionLocal() as session:
            # create_run 會驗 input、解析已發布版本、寫入 Run 並 commit（與 REST 同一條路）
            run, version = await create_run(
                session,
                self.agent_id,
                CreateRunRequest(entrypoint=self.entrypoint_name, input=args),
                trigger=RunTrigger.MCP,
                api_key_id=self.api_key_id,
            )
            run_id = str(run.id)

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
                await session.refresh(run)
                if RunStatus(run.status).is_terminal:
                    return _terminal_result(run, run_id)
                # 與 routers/runs.py 的 invoke 同一條規則：sleep 期間不占連線。
                await session.commit()
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


def _build_server(agent_id: str, version: Any, api_key_id: Any = None) -> FastMCP:
    """把該版本的 entrypoints 轉成 MCP tools。"""
    server = FastMCP(name=f"zentra-agent-{agent_id}")
    for tool in agents_service.mcp_tools(version):
        server.add_tool(_EntrypointTool(
            name=tool["name"],
            description=tool["description"],
            parameters=tool["inputSchema"],
            output_schema=tool["outputSchema"],
            agent_id=agent_id,
            entrypoint_name=tool["name"],
            api_key_id=api_key_id,
        ))
    return server


def _bearer(scope: dict) -> str:
    """從 ASGI scope 取出 Bearer token。

    這裡沒有 FastAPI 的依賴注入可用（MCP 是掛上去的原生 ASGI app），
    所以自己解 header。規則與 `deps.optional_agent_api_key` 必須一致，
    否則同一把 key 在 REST 能用、在 MCP 不能用。
    """
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token if scheme.lower() == "bearer" and token else ""
    return ""


class _McpDispatch:
    """掛在 `/mcp/agents`，把下一段路徑當成 agent_id。

    Starlette 的 Mount 路徑不支援參數，所以自己解析剩餘路徑，
    不要為了這件事去寫一個前端路由器式的 catch-all。
    """

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await _plain(send, 404, {"error": {
                "code": str(ErrorCode.NOT_FOUND), "message": "not found",
                "run_id": None, "retryable": False, "details": {},
            }})

        # Starlette 版本之間語意不同：新版 scope["path"] 保留完整路徑、root_path 是掛載
        # 前綴；舊版則是 path 已經被扣掉。只有在 path 真的以 root_path 開頭時才扣，
        # 兩種語意就都成立。
        root = scope.get("root_path", "")
        full = scope["path"]
        relative = full[len(root):] if root and full.startswith(root) else full

        agent_id, _, rest = relative.lstrip("/").partition("/")
        if not agent_id:
            return await _plain(send, 404, {"error": {
                "code": str(ErrorCode.NOT_FOUND), "message": "agent id is required",
                "run_id": None, "retryable": False, "details": {},
            }})

        try:
            async with SessionLocal() as session:
                agent = await agents_service.load_agent(session, agent_id)
                version = await agents_service.resolve_published_version(session, agent.id, None)
                # 認證與 REST 同一套規則：有帶就必須有效，沒帶預設放行（ZEN-015）。
                key_id = await _resolve_key(session, agent, _bearer(scope))
                server = _build_server(str(agent.id), version, key_id)
        except ZentraError as exc:
            return await _plain(send, exc.http_status, exc.body())

        app = server.http_app(path="/", stateless_http=True)
        # 內層 app 掛在 "/"，所以把 agent_id 併進 root_path，剩餘路徑才會是 "/"。
        inner_root = root + "/" + agent_id
        inner = dict(scope)
        inner["root_path"] = inner_root
        inner["path"] = inner_root + "/" + rest
        inner["raw_path"] = inner["path"].encode()

        # 這個 app 的 lifespan 必須跑起來，session manager 才會初始化
        async with app.router.lifespan_context(app):
            await app(inner, receive, send)


async def _resolve_key(session: Any, agent: Any, presented: str) -> Any:
    """把 Bearer token 換成 `api_key_id`。與 REST 的判斷一字不差。"""
    from apps.api.config import get_settings

    if not presented:
        if get_settings().require_agent_api_key:
            raise ZentraError(
                ErrorCode.UNAUTHENTICATED,
                "this platform requires an agent API key: send Authorization: Bearer <key>",
                401,
            )
        return None
    row = await agent_api_keys.resolve(session, presented)
    if row is None:
        raise ZentraError(
            ErrorCode.UNAUTHENTICATED, "the agent API key is invalid or revoked", 401)
    if row.agent_id != agent.id:
        raise ZentraError(
            ErrorCode.PERMISSION_DENIED, "this API key belongs to a different agent", 403)
    await agent_api_keys.touch(session, row)
    return row.id


async def _plain(send: Any, status: int, body: dict) -> None:
    """用平台統一的錯誤格式回，不要讓 MCP 端點自創一套（契約 §0）。"""
    payload = json.dumps(body, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": payload})


def mount_mcp(app: FastAPI) -> None:
    """掛上 MCP 門面。main.py 只呼叫這一個函式。

    `/mcp/*` 是 MCP 協定端點，不是 REST，**不進公開 OpenAPI**
    （OpenAPI 描述的是 Agent 的 REST 門面）。
    """
    app.mount("/mcp/agents", _McpDispatch())
