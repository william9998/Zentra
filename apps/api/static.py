"""Console 靜態檔與頁面路由（ADR-0006）。

`api` 同時是 Console 的 web server —— 沒有 console 容器、沒有 BFF、沒有 CORS。
瀏覽器拿到 HTML 後用同源 fetch 打 `/v1`，走的就是外部系統那份 API
（Console 是 `specs/api-contract.md` 的第一個真實消費者）。

三條紀律：
1. 全部 `include_in_schema=False` —— OpenAPI 是 Agent 的對外契約，UI 路由不准污染它。
2. **沒有 catch-all**：只有明確的頁面路由，其餘 404 就該是 404（前端也沒有路由器，
   網址由這裡給出，頁面自己從 `location.pathname` 讀 id —— ui-contract §6.3）。
3. 靜態檔一律走 `StaticFiles`，不自己拼路徑 —— path traversal 交給它處理。

ZEN-006A 只交付 runs / run 兩個畫面；ui-contract 表頭的另外三條（agents、agent、
version-new）在 ZEN-006 才會出現。
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from apps.api.config import get_settings
from apps.api.errors import not_found

router = APIRouter(include_in_schema=False)

# **每次都回來問一聲**（有 ETag，沒變就是 304，幾乎不花什麼）。
#
# 沒有這個標頭，瀏覽器會自己「啟發式」決定快取多久 —— 而 ES module 用 `import`
# 拉進來的檔案特別容易被留著。零建置（ADR-0006）沒有檔名 hash 可以打破快取，
# 結果就是新版 runs.js 配上舊版 api.js：新欄位出現了，資料卻是 `—`。
CACHE_CONTROL = "no-cache"


class _OptionalStaticFiles(StaticFiles):
    """目錄可以還不存在的 StaticFiles。

    `apps/console/static/` 由 @frontend-dev 擁有；ZEN-006A 兩邊平行開工，api 啟動時
    它可能還不在。StaticFiles 預設會在**第一個請求**時 `os.stat(directory)`，不存在就
    `RuntimeError` → 500（`check_dir=False` 只關掉建構時那一次檢查，關不掉這一次）。
    這裡把該檢查停掉，目錄不在時就讓它自然 404。

    **路徑解析與 traversal 防護完全沿用 StaticFiles，我們沒有碰。**
    """

    async def check_config(self) -> None:
        return None

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = CACHE_CONTROL
        return response


async def _page(filename: str) -> FileResponse:
    """回 apps/console 下的一個 HTML 檔。

    `apps/console/` 由 @frontend-dev 擁有，可能還沒建立（ZEN-006A 兩邊平行開工）。
    檔案不在就回 404，不要在 import 期或請求期把 app 炸成 500。
    """
    path: Path = get_settings().console_dir / filename
    if not await asyncio.to_thread(path.is_file):
        raise not_found("console page")
    return FileResponse(path, headers={"Cache-Control": CACHE_CONTROL})


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/console/agents", status_code=307)


@router.get("/console/agents")
async def console_agents() -> FileResponse:
    return await _page("agents.html")


# The literal suffix must precede the agent detail route.
@router.get("/console/agents/{agent_id}/versions/new")
async def console_version_new(agent_id: str) -> FileResponse:
    return await _page("version-new.html")


@router.get("/console/agents/{agent_id}")
async def console_agent(agent_id: str) -> FileResponse:
    return await _page("agent.html")


@router.get("/console/mcp")
async def console_mcp() -> FileResponse:
    return await _page("mcp.html")


# 先註冊字面路徑，再註冊帶參數的 —— 讓 /console/runs 不會被 /console/runs/{run_id} 吃掉。
@router.get("/console/runs")
async def console_runs() -> FileResponse:
    return await _page("runs.html")


@router.get("/console/runs/{run_id}")
async def console_run(run_id: str) -> FileResponse:
    # run_id 在這裡**不解析也不驗證**：它只是讓路由匹配得到，
    # 頁面自己從 location.pathname 讀，再去打 /v1/runs/{run_id}。
    return await _page("run.html")


def mount_console(app: FastAPI) -> None:
    """掛上 Console 的靜態檔與頁面路由。main.py 只呼叫這一個函式。"""
    # check_dir=False：apps/console/static 還不存在時 app 仍要能啟動（見 _OptionalStaticFiles）。
    # 不在這裡判斷目錄存不存在再決定要不要掛 —— 那會讓「前端剛建好檔案」必須重啟 api。
    app.mount(
        "/console/static",
        _OptionalStaticFiles(directory=get_settings().console_dir / "static", check_dir=False),
        name="console-static",
    )
    app.include_router(router)
