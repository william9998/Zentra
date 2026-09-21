# 交接單 Handoff

**任務**：`ZEN-006A` Console 靜態檔與頁面路由（api 同時當 Console 的 web server）
**從**：@backend-dev　**給**：@orchestrator（下一棒實質是 @qa-verifier）
**日期**：2026-09-21
**Spec 版本**：`v0.2.2`（`api-contract.md v0.2.2` §0、`ui-contract.md v0.4.0` 表頭 + §6.1）

## 1. 我做了什麼
在 `apps/api` 新增 `static.py`，掛上 Console 的靜態檔與兩條頁面路由（`/console/runs`、
`/console/runs/{run_id}`）與 `/` 的 307 轉址，全部 `include_in_schema=False`。
`/v1/*` 的端點、欄位、狀態枚舉**一個字都沒動**（`routers/`、`schemas.py`、`enums.py`、
`services/`、migrations 全未觸碰）。`apps/console/` 不存在時 app 仍能正常啟動，
`/healthz` 與 `/v1/*` 不受影響，那幾條 console 路由回 404。
過程中發現 Starlette 的 `StaticFiles(check_dir=False)` **擋不住第一個請求時的目錄檢查**
（會 `RuntimeError` → 500），已處理，見 §6。

## 2. 產出的檔案
| 檔案 | 說明 |
|---|---|
| `apps/api/static.py` | **新增**。`mount_console(app)`：掛 `/console/static` 的 `StaticFiles` + 三條 UI 路由（`/`、`/console/runs`、`/console/runs/{run_id}`），全部 `include_in_schema=False`；另含 `_OptionalStaticFiles`（目錄可缺席）。 |
| `apps/api/config.py` | **修改**。新增 `console_dir: Path`，預設由 `Path(__file__).resolve().parent.parent / "console"` 推導 → 本機任何 cwd 都是 `<repo>/apps/console`，容器內是 `/srv/apps/console`（`docker/api.Dockerfile` 的 `WORKDIR /srv`）。沿用既有 settings 風格，可用環境變數 `CONSOLE_DIR` 覆寫。 |
| `apps/api/main.py` | **修改**。多兩行：`from apps.api.static import mount_console` 與 `mount_console(app)`（在既有 `include_router` 之後）。其餘不動。 |

沒有新增/修改：資料表、migration、Celery task、佇列、`/v1` 端點、`specs/**`、
`apps/console/**`、`docker/**`、`compose.yaml`、`tests/**`。

## 3. 契約影響
- [x] 有影響 → **`/` 的轉址目標偏離 `ui-contract.md` 表頭**，其餘無影響。

| 項目 | 契約寫的 | 這次實作 | 理由 |
|---|---|---|---|
| `GET /` | 307 → `/console/agents` | 307 → **`/console/runs`** | `agents.html` 在 ZEN-006A 不存在（ZEN-006 才交付）。轉到不存在的頁面等於首頁必 404。 |

- 這一點**已經記在 `coordination/BOARD.md` ZEN-006A 段落的「已知的契約偏離」第 1 點**，
  待 @ui-designer 與 @architect 追認；我沒有重複開 `SPEC-QUESTION`，也沒有改 `specs/**`。
- 程式碼裡留了痕跡：`apps/api/static.py` 的 `root()` docstring 明寫「契約目標是 `/console/agents`，
  ZEN-006 交付 `agents.html` 時要改回去」。
- `/v1` 的回應格式、欄位、錯誤 code **完全沒有為了前端方便而動過**。
  頁面 404 用的是既有的 `errors.not_found()` → `api-contract.md` §0 的標準錯誤信封（`NOT_FOUND`）。

## 4. 下一個人要做什麼
1. **@qa-verifier**：把「`/openapi.json` 不含任何 `/console` 路徑、也不含 `/`」加進契約測試
   （現有 `tests/zen000/test_public_api_contract.py::test_unknown_agent_and_internal_openapi_exclusion`
   已在掃 `/internal`，同一風格加一條即可）。我沒有寫測試 —— `tests/**` 是 QA 的區域。
2. **主 session**：三方交件後跑一次 `docker compose build api && docker compose up -d api`，
   然後 `curl -i http://localhost:8000/` 應得 `307 Location: /console/runs`，
   `curl -i http://localhost:8000/console/runs` 應得 `200 text/html`。
   `docker/api.Dockerfile` 的 `COPY apps/console ./apps/console`（@platform-eng 已加）**是這件事的前提**。
3. **@frontend-dev**：`runs.html`、`run.html` 必須直接放在 `apps/console/` 根層（不是 `static/` 底下），
   靜態資產放 `apps/console/static/**`，頁面裡引用 `/console/static/...` 絕對路徑。
   目前 `apps/console/static/**` 已存在且我實測可服務（見 §7），但 `runs.html` / `run.html` 交件時我這邊還沒有。
4. **ZEN-006 開工時**：把 `root()` 改回 `/console/agents`，並補上另外三條頁面路由
   （`/console/agents`、`/console/agents/{agent_id}`、`/console/agents/{agent_id}/versions/new`）。

## 5. 我沒做的事（誠實列出）
- **沒有寫任何自動化測試**（`tests/**` 不在我的可寫範圍）。我的驗證是 scratchpad 裡的一次性腳本，
  不會進 repo，**所以 QA 必須自己補測試，不能引用我的腳本當證據**。
- **沒有跑 `docker compose build` / `up`**，也就沒有在真容器裡驗證過 `/srv/apps/console` 路徑。
  我只驗證了 `console_dir` 的推導規則與 cwd 無關（從 `/` 執行也解析正確）。
- **`tests/zen000/` 的 live 契約測試沒有真的跑起來**：它需要 `ZENTRA_TEST_BASE_URL` 指向活著的
  四服務部署，我沒起 compose，結果是 **10 skipped**（見 §7）。**skipped 不是 passed。**
- 沒有實作 `GET /v1/runs`、`POST /v1/runs/{id}/cancel`、`/v1/agents/*` 的 GET、版本上傳、MCP（ZEN-004 / ZEN-001 / ZEN-005）。
- 沒有做另外三個畫面的路由（`agents` / `agent` / `version-new`）。
- 沒有做 catch-all、沒有做 SPA fallback：`/console`、`/console/`、`/console/agents`、
  `/console/runs/a/b` 一律 404。這是刻意的。
- 沒有加任何快取標頭調校（`FileResponse` / `StaticFiles` 的預設 ETag + Last-Modified 就這樣）。

## 6. 已知限制與風險
| 項目 | 影響 | 建議 |
|---|---|---|
| `_OptionalStaticFiles.check_config()` 覆寫 | Starlette 的 `check_dir=False` 只關掉**建構時**檢查；第一個請求還會 `os.stat(directory)`，目錄不在就 `RuntimeError` → 500。我覆寫 `check_config()` 讓它不檢查，目錄不在時自然 404。**路徑解析與 traversal 防護完全沿用 `StaticFiles`，沒有動。** | 這依賴 Starlette 的 `check_config` 方法名。若日後升級 Starlette 改名，覆寫會靜默失效 → 只有在「`apps/console/static` 不存在」這個過渡情境會復發 500。`apps/console` 進 repo 後風險趨近於零。 |
| `console_dir` 用 `__file__` 推導 | 若有人把 `apps/api` 與 `apps/console` 拆到不同目錄層級，預設值會失準 | 已支援 `CONSOLE_DIR` 環境變數覆寫，`compose.yaml` 目前不需要設 |
| `/console/runs/{run_id}` 不驗證 `run_id` | 任何字串（含不存在的 run）都回 200 + `run.html` | 這是刻意的：頁面自己 `fetch /v1/runs/{id}`，404 由前端呈現。**後端不在 UI 路由做 run 存在性查詢**（否則每次開頁面多一次 DB query，且會洩漏存在性差異） |
| 驗證環境與容器未必同版 | 機器上沒有 `uv`、repo 沒有 `.venv`。我在兩個環境各跑一次：scratchpad 臨時 venv，以及先前 backend session 留下的 `/private/tmp/zentra-backend-venv` —— **兩者都是 fastapi 0.141.1 / starlette 1.6.0，結果逐條相同**。所以「與其他後端工作所用的環境不一致」這個風險比我原先寫的小；真正沒覆蓋到的只有「容器 build 時 `fastapi>=0.115` 解析出的版本」 | 主 session 用 compose 實跑一次即可覆蓋。另注意：**我只驗證過 starlette 1.6.0 一個版本**，`check_config` 覆寫在更舊版本上沒有實測 |
| `/console/runs/` （尾斜線） | FastAPI 的 `redirect_slashes` 會 307 → `/console/runs` | 行為正常，記錄備查 |

## 7. 驗證狀態
環境：無 `uv`、repo 無 `.venv`。下表每一條都在**兩個**直譯器各跑過一次、結果逐條相同：
(a) scratchpad 臨時 venv，(b) 先前 backend session 留在機器上的 `/private/tmp/zentra-backend-venv`。
兩者皆為 `python3.13` + **fastapi 0.141.1 / starlette 1.6.0** + sqlalchemy / asyncpg / redis / celery / pytest 9.1.1，
`PYTHONPATH=<repo>` 從 repo 根目錄執行。**所有驗證都是 in-process `TestClient`，沒有起 compose。**

| 檢查 | 結果 | 證據 |
|---|---|---|
| `apps.api.main` 能 import | ✅ | `from apps.api.main import app` 成功，路由表含 `/healthz` 與兩個 included router |
| **`apps/console` 不存在時能啟動**（`CONSOLE_DIR=/nonexistent/...`）| ✅ | `/healthz` → `200 {"status":"ok"}`；`/openapi.json` → 200；`/` → `307 /console/runs`；`/console/runs` → `404 {"error":{"code":"NOT_FOUND",...}}`；`/console/runs/{id}` → 404；`/console/static/css/tokens.css` → 404；`/console/agents` → 404 |
| **`/openapi.json` 不含 `/console`、不含 `/`** | ✅ | 目錄缺席與存在兩種情況下皆為 `['/v1/agents/{agent_id}/runs', '/v1/runs/{run_id}', '/v1/runs/{run_id}/events']` |
| 頁面路由（用臨時目錄放假的 `runs.html` / `run.html`）| ✅ | `/` follow → 200 `runs.html`；`/console/runs` → `200 text/html`；`/console/runs/{uuid|not-a-uuid|0}` → 200 `run.html`；`/console/runs/a/b` → 404（無 catch-all） |
| 靜態檔 + path traversal | ✅ | `/console/static/css/tokens.css` → `200 text/css`；`../runs.html`、`..%2Fruns.html`、`%2e%2e/%2e%2e/SECRET.txt`、`....//SECRET.txt`、`//etc/passwd` 五種嘗試全部 404，且回應內容從未離開 console 目錄 |
| 對 @frontend-dev **實際**的 `apps/console/`（交件當下）| ✅（部分）| `/console/static/css/tokens.css` → `200 text/css`、`/console/static/js/api.js` → `200 text/javascript`；`runs.html` / `run.html` 尚未存在故 `/console/runs` → 404（符合設計） |
| `console_dir` 與 cwd 無關 | ✅ | 從 `/` 執行 → `/Users/william/Documents/project/Zentra/apps/console`；同理容器內 `/srv/apps/api/config.py` → `/srv/apps/console` |
| `tests/zen000/test_public_api_contract.py` | ⚠️ **未實際執行到（10 skipped，0 passed、0 failed）** | 兩個環境各跑一次皆為 `10 skipped in 0.03s`。原因：該檔的 `client` fixture 需要 `ZENTRA_TEST_BASE_URL` 指向活著的部署，未設則整檔 skip。**沒有回歸證據，也沒有退化證據。** |
| `tests/zen000/` 其餘檔案 | 未跑 | 需要 docker / 活的 compose，且不在本次改動半徑內 |
| 建置 / 型別檢查 | 未跑 | 專案沒有 lint / mypy 設定 |

> 白話版：**「apps/console 不存在也能啟動」與「OpenAPI 乾淨」是我實際驗證過的；
> 「跑在真容器裡沒問題」與「ZEN-000 契約測試沒退化」我都沒有證據。**

## 8. 未決的 SPEC-QUESTION
- [x] 無（我沒有新開）。
  `/` → `/console/runs` 的偏離**沿用 BOARD 上 ZEN-006A 既有的「已知的契約偏離」第 1 點**，
  仍待 @ui-designer / @architect 追認 —— 在追認之前，**不得視為契約已改**。
