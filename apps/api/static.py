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

from apps.api.config import get_settings
from apps.api.errors import not_found

router = APIRouter(include_in_schema=False)


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


async def _page(filename: str) -> FileResponse:
    """回 apps/console 下的一個 HTML 檔。

    `apps/console/` 由 @frontend-dev 擁有，可能還沒建立（ZEN-006A 兩邊平行開工）。
    檔案不在就回 404，不要在 import 期或請求期把 app 炸成 500。
    """
    path: Path = get_settings().console_dir / filename
    if not await asyncio.to_thread(path.is_file):
        raise not_found("console page")
    return FileResponse(path)


@router.get("/")
async def root() -> RedirectResponse:
    """契約目標是 `/console/agents`（ui-contract.md 表頭）。

    ZEN-006A 這個切片還沒有 agents.html，先轉到 `/console/runs`；
    **ZEN-006 交付 agents.html 時要改回 `/console/agents`。**
    """
    return RedirectResponse("/console/runs", status_code=307)


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
