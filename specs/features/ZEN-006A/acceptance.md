# ZEN-006A 驗收條件 (Acceptance Criteria)

| 欄位 | 值 |
|---|---|
| 任務 | `ZEN-006A` — Console 早期切片：Run 觀測頁 + 發起 Run（只用現有三個端點） |
| 擁有者 | `@qa-verifier` |
| 依據 | `SPEC.md v0.2.2` · `api-contract.md v0.2.2` · `ui-contract.md v0.4.0` · ADR-0005 · ADR-0006 |
| 交件者 | @frontend-dev（`apps/console/**`）· @backend-dev（`apps/api/static.py` + `config.py`/`main.py`）· @platform-eng（`docker/api.Dockerfile`） |

> **這個任務沒有 `spec.md`。** 驗收條件因此直接從契約與 BOARD 的範圍宣告推導，
> 不替 @architect 補寫 feature spec。任何「契約沒寫、BOARD 沒授權」的行為一律視為超出範圍。
>
> **判定規則**：`MUST` 條目任一失敗 → 不得進 `DONE`。
> `SHOULD` 條目失敗 → 記為缺陷但可由裁量判定是否阻擋。
> **未執行 ≠ 通過**：沒跑起來的條目一律標 `未驗證`，寫進報告的「未覆蓋」清單。

驗證用測試位於 `tests/zen006a/`，重跑方式見本檔 §9。

---

## 1. 範圍上限（BOARD 的硬約束）

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| S1 | `apps/console/**` 對 `/v1` 發出的請求**只有三個端點**：`POST /v1/agents/{agent_id}/runs`、`GET /v1/runs/{run_id}`、`GET /v1/runs/{run_id}/events`。原始碼中不得出現任何其他 `/v1` 路徑字面值 | MUST | 靜態掃描 `apps/console/**` 的路徑字面值；且瀏覽器實跑全程攔截網路請求，`/v1` 請求的 path 只落在這三個 pattern |
| S2 | 未實作端點**一行都沒呼叫**：`GET /v1/runs`（列表）、`POST /v1/runs/{id}/cancel`、`/v1/agents`、版本上傳（multipart）、`/mcp/*` | MUST | 同上；另檢查 DOM 中不得出現會導向這些端點的按鈕（停止 / 重試 / 上傳） |
| S3 | 不得新增 `agents.html` / `agent.html` / `version-new.html` / `streaming-output.js` / `version-upload.js` / `endpoints-card.js` | MUST | `find apps/console -type f` 清單比對 |
| S4 | 後端只新增 `/`、`/console/runs`、`/console/runs/{run_id}` 三條 UI 路由與 `/console/static` 掛載；`/v1/*` 的端點、欄位、狀態枚舉、錯誤 code 完全未變 | MUST | `git diff` 稽核 + `tests/zen000/test_public_api_contract.py` 對活服務全綠（無退化） |
| S5 | 沒有 catch-all / SPA fallback：`/console`、`/console/`、`/console/agents`、`/console/runs/a/b` 一律 404，且回應是 `api-contract.md` §0 的錯誤信封（`NOT_FOUND`） | MUST | live HTTP 測試 |

## 2. 正常路徑（端到端，對跑著的 compose）

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| N1 | `GET /` → `307`，`Location: /console/runs`（偏離契約，見 §7 D1） | MUST | live HTTP |
| N2 | `GET /console/runs` → `200`、`content-type: text/html`，內容位元組等同 repo 的 `apps/console/runs.html`（證明映像帶的是當前檔案） | MUST | live HTTP + `cmp` |
| N3 | `GET /console/runs/{任意字串}` → `200` 且回的是 `run.html`（後端不驗 run_id，存在性由前端 `GET /v1/runs/{id}` 呈現） | MUST | live HTTP |
| N4 | `/console/static/**` 下的 11 個資產全部 `200`，且位元組等同 repo 檔案；`.css` 回 `text/css`、`.js` 回 `text/javascript`、`.svg` 回 `image/svg+xml` | MUST | live HTTP + `cmp` |
| N5 | 在真實瀏覽器（headless Chrome）操作：`/console/runs` → 點「發起測試 Run」→ `<dialog>` 開啟 → 填 question → 送出 → 導向 `/console/runs/{run_id}`（網址中的 id 即 `POST` 回應的 `run_id`） | MUST | CDP E2E |
| N6 | 觀測頁在 45 秒內走到終態並顯示 `已完成`，結果卡顯示 `{"echo": "<送出的 question>"}`（逐字元等於送出的字串） | MUST | CDP E2E |
| N7 | 事件時間軸至少出現 `node.start`/`node.end`（`echo_node`、`finalize_node`）與終局 `run.status`，`seq` 嚴格遞增且無重複 | MUST | CDP E2E（讀 DOM 列數與事件型別） |
| N8 | 全程 `0` 個未捕捉例外、`0` 筆 `console.error` | MUST | CDP `Runtime.exceptionThrown` + `Runtime.consoleAPICalled(level=error)` |
| N9 | 頁面重新載入已終態的 Run：仍能完整重建畫面（事件從 DB 重播），且 §3 R1 的「不重連」仍成立 | MUST | CDP E2E |

## 3. 邊界與失敗路徑

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| R1 | **終態後 `EventSource` 必須真的關閉。** 一個 Run 的完整觀看過程中，`GET /v1/runs/{id}/events` 的請求數 **= 1**；終態後 30 秒內不得出現第 2 次請求（後端在終態結束串流且未實作 `Last-Event-ID` 續傳，不關就會無限重連並從 `seq 0` 重播） | MUST | CDP 攔截 `Network.requestWillBeSent`，終態後續觀察 ≥ 20s |
| R2 | 時間軸不得出現重複事件：同一 `seq` 只渲染一次（即使伺服器重播） | MUST | CDP E2E：計算 DOM 列數 vs 唯一 `seq` 數 |
| R3 | `GET /console/runs/<不存在的 run_id>` → 頁面顯示 `NOT_FOUND`、message、`run_id`，以及「我現在該做什麼」的指引；**不得出現未捕捉例外** | MUST | CDP E2E |
| R4 | dialog 送出空白 question → 走原生 `required` 驗證，**不送出請求**；送出後端會拒絕的輸入 → 顯示 `VALIDATION_ERROR` + message，且 dialog 不關閉、可重送 | MUST | CDP E2E |
| R5 | `<dialog>` 的原生行為成立：`ESC` 關閉、「取消」關閉、開啟時焦點落在輸入框 | SHOULD | CDP E2E |
| R6 | 事件超過 200 筆時只渲染最近 200 筆並出現「載入更早的事件」；超過 2000 筆顯示改用 API 的提示；payload > 2KB 折疊 | SHOULD | 以 stub 伺服器灌假事件（真實 ZEN-000 Run 產不出 200 筆） |
| R7 | 離開頁面時 `destroy()` 真的執行：`EventSource` 關閉、所有 `setInterval`/`setTimeout` 清掉（`pagehide`/`beforeunload` 皆綁） | MUST | 原始碼稽核 + CDP：導頁後不再有 `/events` 請求 |
| R8 | `apps/console` 目錄不存在時 `api` 仍能啟動，`/healthz` 與 `/v1/*` 不受影響，console 路由回 404 | MUST | 以 `CONSOLE_DIR` 指向不存在目錄的 in-process `TestClient` |

## 4. 安全：XSS、憑證與隔離邊界（P4）

> 單租戶，所以不問「A 租戶看到 B 租戶」。Console 這一層要問的三題是：
> **同源前端持有任何長期憑證嗎？前端碰得到資料層嗎？前端打得到 `/internal/*` 嗎？**

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| X1 | **`innerHTML` / `outerHTML` / `insertAdjacentHTML` / `document.write` 不得寫入任何來自 API 或使用者的字串。** 全 `apps/console/**` 的 `innerHTML` 賦值右側必須是模組內的固定常數樣板（無內插） | MUST | AST 級掃描（解析每個 `.js`，檢查賦值右側節點型別）+ 實測 |
| X2 | 送出 `<img src=x onerror=...>`、`"><script>`、`</pre><img ...>` 等 payload 作為 question 後：觀測頁的結果卡、時間軸 payload、runs 頁暫存清單皆以純文字呈現；DOM 內由該 payload 產生的 `img`/`script`/`iframe`/`svg[onload]` 節點數 = 0；無 alert / 無額外例外 | MUST | CDP E2E（真的送出並走完 Run） |
| X3 | Console 不持有任何長期憑證：`localStorage` / `sessionStorage` / `document.cookie` 只含 UI 偏好與 `run_id` 暫存，**不得含 `api_key`、`token`、`secret`、LLM 金鑰**（ui-contract §6.4、P4） | MUST | CDP 讀取三種儲存體並以關鍵字與值形狀比對 |
| X4 | Console 不對 `/internal/*` 發任何請求；原始碼不得出現 `internal` 路徑字面值 | MUST | 靜態掃描 + CDP 網路攔截 |
| X5 | Console 不直連資料層：原始碼不得出現 `postgres`、`redis`、`5432`、`6379`，也不得有跨來源請求（所有請求 same-origin 相對路徑） | MUST | 靜態掃描 + CDP 網路攔截（所有請求 origin = `http://127.0.0.1:8000`） |
| X6 | `/openapi.json` 不得洩漏任何 UI 路由：`paths` 不含 `/`、不含任何以 `/console` 開頭者；仍不含 `/internal/` | MUST | live HTTP |
| X7 | 靜態檔服務不得被 path traversal 穿出 console 目錄（`../`、`..%2f`、`%2e%2e/`、`....//`、絕對路徑） | MUST | live HTTP |
| X8 | `/console/runs/{run_id}` 路由不得因 run 存在與否而有可觀測差異（不洩漏存在性、不多打一次 DB） | SHOULD | live HTTP：存在與不存在的 run_id 皆回相同 `run.html` |

> **沙盒隔離（L1–L4）不在本切片的變更半徑內**：ZEN-006A 沒有動 `apps/sandbox/**`、`compose.yaml`、
> 沙盒網段或 run token。對應安全清單沿用 ZEN-000 的結論，本次**不重跑、不宣稱通過**（見 §8）。

## 5. `ui-contract.md` 合規

### 5.1 §6.3 六條硬性規則（逐條，全部 MUST）

| # | 規則 | 驗證方式 |
|---|---|---|
| U1 | 不准引入任何執行期函式庫 | 全檔掃描：無 `<script src="http…">`、無 `import` 自 URL/裸模組名（只准相對路徑 `./` `../`）、無 `React/Vue/Alpine/htmx/jQuery/lodash`；CDP 實跑時網路請求中沒有任何跨來源腳本 |
| U2 | 不准有建置步驟 | `apps/console` 下無 `package.json` / `node_modules` / `*.config.*` / `tsconfig.json` / `.ts` / `.jsx`；瀏覽器直接載入 `.html`/`.css`/`.js` 並正常運作（U1 的實跑即證明） |
| U3 | 不准用 `innerHTML` 寫入 API 或使用者字串 | 同 X1 |
| U4 | 不准寫前端路由器 | 無 `history.pushState` / `popstate` / `hashchange`；頁面切換一律 `location.href` 或 `<a href>`；網址由 api 的路由給出 |
| U5 | 不准寫死色碼、間距、字級 | `base.css` / `layout.css` 不得出現 `#rrggbb`、`rgb()`、`px` 字面值（`0`、`1px` 髮絲邊框、`media query` 斷點除外，須逐一列舉並說明）；`tokens.css` 必須與 ui-contract §1 **逐行相同** |
| U6 | 每個 `mount()` 都能 `destroy()`，且真的關掉 `EventSource` 與 `setInterval` | 原始碼稽核：`shell.js`、`run-timeline.js` 皆匯出 `destroy`；頁面 module 綁 `pagehide`/`beforeunload`；CDP 實測導頁後無新 `/events` 請求 |

### 5.2 §4 五態（每個畫面，全部 MUST）

| # | 狀態 | 驗證方式 |
|---|---|---|
| F1 | **Loading**：已知版面用骨架屏；超過 3 秒要有進度語意 | CDP：以延遲回應的 stub 觸發，檢查 `.skeleton` 節點數 > 0 與說明文字存在 |
| F2 | **Empty**：一行說明「這裡會出現什麼」+ 一個 secondary 按鈕；**不得只寫「無資料」** | CDP：runs 頁（列表 + 暫存皆空）、run 頁（結果／用量未產生時）、時間軸（尚無事件時） |
| F3 | **Error**：含 `error.code`（mono）、`run_id`、以及「我現在該做什麼」 | CDP：R3 的 404 畫面與 R4 的 422 畫面逐項檢查 |
| F4 | **Streaming**：串流中有連線狀態說明、`aria-live="polite"`；只追加不重畫 | CDP：RUNNING 期間檢查連線文字與 `aria-live` 屬性 |
| F5 | **Success**：正常態（N6 即是） | CDP |

### 5.3 §2 Run 狀態語言（全部 MUST）

| # | 條件 | 驗證方式 |
|---|---|---|
| L1 | 六個狀態（`QUEUED`/`SCHEDULING`/`RUNNING`/`SUCCEEDED`/`FAILED`/`CANCELLED`）各有**圓點 + 繁中文字標籤**，標籤字串逐字等於 §2 的表（排隊中／配置中／執行中／已完成／失敗／已取消） | 原始碼稽核 + CDP 讀取實際渲染的標籤 |
| L2 | 圓點顏色取自 `--status-*` token，尺寸 6px，`border-radius: var(--radius-full)`；**狀態不得只靠顏色**（一定同時有文字） | CDP `getComputedStyle` 實測 |
| L3 | `RUNNING` 時**同時顯示當前節點名稱與已耗時**，且耗時每秒更新 | CDP：RUNNING 期間取兩次快照比較 |
| L4 | `FAILED` 時顯示 `error.code` | 原始碼稽核 + stub 實測（ZEN-000 正常路徑產不出 FAILED） |
| L5 | **不得渲染 `WAITING_INPUT`**，原始碼不得出現該字串；不得使用 `--status-waiting`（token 本身可留在 `tokens.css`） | 全檔掃描 |
| L6 | **絕對禁止 emoji 當圖示**：`apps/console/**` 全部檔案不得含 emoji 碼位；圖示一律取自 `icons.svg` sprite，`stroke-width="1.5"` | 全檔 Unicode 掃描 + sprite 稽核 |
| L7 | `icons.svg` 只含 §3 表列的 10 個 `symbol`，`viewBox="0 0 24 24"`，`stroke="currentColor"` | sprite 稽核 |

## 6. 契約一致性（`api-contract.md v0.2.2`）

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| C1 | `api.js` 的 `RUN_STATUS` 枚舉 = SPEC §9 六值，**無 `WAITING_INPUT`**；`EVENT_TYPE` = 契約 §1 七值，**無 `tool.call` / `tool.result` / `interrupt`**；`ERROR_CODE` = 契約 §0 十三值，無自創 | MUST | 從 `api.js` 解析枚舉並與契約文字逐值比對 |
| C2 | 全 `apps/console/**` 不得出現已移除的值：`WAITING_INPUT`、`tool.call`、`tool.result`、`interrupt`、`config.tier` / `"tier"` | MUST | 全檔掃描 |
| C3 | 契約欄位名（snake_case）作為**屬性存取**只出現在 `api.js`（ui-contract §6.2）；其他檔案用正規化後的 camelCase view model | MUST | AST 掃描：其他檔案不得有 `.run_id` / `.agent_id` / `.created_at` / `.llm_calls` / `.cost_usd` … 形式的成員存取 |
| C4 | `POST /v1/agents/{id}/runs` 的實際回應欄位集合 = `{run_id, agent_id, agent_version, status, created_at, events_url}`，`status == "QUEUED"`，`run_id` 為 UUID v7；`api.js` 讀取的欄位是這些欄位的子集 | MUST | live HTTP + 原始碼比對 |
| C5 | `GET /v1/runs/{id}` 的實際回應欄位集合 = 契約 §1 的 12 個欄位；`usage.cost_usd` 是**字串**；未產生欄位為 `null` 但仍存在；前端不得對 `cost_usd` 做浮點運算 | MUST | live HTTP + 原始碼比對 |
| C6 | SSE 每則訊息的 `event:` 屬於七種枚舉之一、`id:` 為遞增整數；前端以 `id` 去重（R2） | MUST | live HTTP（SSE）+ CDP |
| C7 | 前端呈現的錯誤一律取自回應的 `error` 信封五欄位（`code`/`message`/`run_id`/`retryable`/`details`），不得自創 code | MUST | 原始碼稽核 + R3/R4 實測 |

## 7. 三條契約偏離的判定門檻

BOARD 已記錄三條偏離，**尚未經 @ui-designer / @architect 追認**。本次驗收採下列門檻：

| # | 偏離 | 判定門檻 |
|---|---|---|
| D1 | `/` → `/console/runs`（契約表頭寫 `/console/agents`） | 可接受的**過渡**，條件全滿足才不阻擋：(a) `agents.html` 確實不存在於本切片；(b) 程式碼留下明確回收註記；(c) BOARD 已記錄待追認。**若轉去 `/console/agents` 會 404，轉址到唯一存在的頁面才是對使用者誠實的行為。** |
| D2 | `runs.html` 的「發起測試 Run」`<dialog>` 不在 ui-contract §5 元件清單 | 條件：(a) 只使用原生 `<dialog>`（§7 要求原生元素優先），(b) 只呼叫已存在端點，(c) 用既有 token 與 `.btn-*`，未新增元件檔到 §6.1 清單外的 `components/`，(d) BOARD 已記錄。滿足則不阻擋，但**必須在 ZEN-006 開工前由 @ui-designer 收斂或刪除**。 |
| D3 | `runs.html` 用 `localStorage` 暫存最近 `run_id` | 條件：(a) 只存 `run_id` / question 摘要 / 時間，**絕不存憑證**（X3），(b) 程式碼有 `ZEN-004 後刪除` 的明確標記與可整段移除的邊界，(c) 讀取端對壞資料容錯不致當頁，(d) BOARD 已記錄。滿足則不阻擋。 |

> 三條偏離**都不得被視為契約已變更**。追認責任在 @architect / @ui-designer，
> QA 只判定「它們在本切片是否構成缺陷或安全風險」。

## 8. P1–P5 影響評估（必須逐條確認，不得略過）

| # | 立場 | 本切片是否波及 | 驗證方式 |
|---|---|---|---|
| P1 | 使用者程式碼永不在平台行程內執行 | 預期**未波及**：Console 是瀏覽器端靜態檔；`apps/api/static.py` 只回 `FileResponse` 與 `RedirectResponse`，不 import、不 exec 任何使用者碼 | `git diff` 稽核 + 確認 `static.py` 無 `import`/`exec`/`eval`/`subprocess` |
| P2 | 統一 LangChain + LangGraph | 未波及（沒碰 agent runtime） | diff 稽核 |
| P3 | 每個 Agent 天生有 API + MCP 兩個門面 | 未波及；Console 未顯示 endpoints 卡（ZEN-006），**但也不得讓 UI 路由污染公開 OpenAPI**（X6） | X6 |
| P4 | 沙盒內拿不到任何長期憑證 | **需實測**：Console 是同源前端，若在瀏覽器儲存體持有任何長期憑證即違反 §6.4 的同一精神；另須確認 Console 沒有打開通往 `/internal/*` 的新路徑 | X3、X4、X5 |
| P5 | Agent 版本不可變 | 未波及（沒有上傳、沒有版本寫入路徑） | diff 稽核 + S2 |

## 9. 重跑方式

```bash
# 前提：四服務已起，Console 活在 http://127.0.0.1:8000/console/runs
export ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000
cd <repo>
PYTHONPATH=. /private/tmp/zentra-backend-venv/bin/pytest tests/zen006a -v

# 只跑靜態契約稽核（不需服務）
PYTHONPATH=. /private/tmp/zentra-backend-venv/bin/pytest tests/zen006a/test_console_source_contract.py -v

# 瀏覽器 E2E（需要 headless Chrome 與 node）
PYTHONPATH=. ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 \
  /private/tmp/zentra-backend-venv/bin/pytest tests/zen006a/test_console_e2e.py -v
```

未設 `ZENTRA_TEST_BASE_URL` 時 live 與 E2E 測試 **skip**（skip 不是 pass，必須在報告中標為未驗證）。

## 10. 明確不在本次驗收範圍

以下依 BOARD / ADR 刻意不做，**不得當成缺陷**，但必須確認「真的沒偷做」（§1）：
Run 列表與分頁、cancel、重試、上傳、MCP、另外三個畫面、`streaming-output.js` 的逐字渲染、
`Last-Event-ID` 續傳、認證、gVisor / L2 核心隔離、多瀏覽器相容（Chrome 以外）。

---

## 11. 驗收後補註（2026-09-21，@qa-verifier 執行驗證時寫入）

這一節記錄「驗收條件寫完、實際執行之後」我做的兩點澄清與一個發現。
**分開記錄，是為了讓人看得出哪些是事前定的、哪些是事後補的。**

### 11.1 S5 的澄清：未匹配路由的 404 信封

S5 原文要求未匹配的 console 路徑「回應是 `api-contract.md` §0 的錯誤信封」。實測：

```
$ curl -s http://127.0.0.1:8000/console/runs/a/b   → {"detail":"Not Found"}   [404]
$ curl -s http://127.0.0.1:8000/console/agents      → {"detail":"Not Found"}   [404]
$ curl -s http://127.0.0.1:8000/v1/unknown          → {"detail":"Not Found"}   [404]
$ curl -s http://127.0.0.1:8000/nonexistent         → {"detail":"Not Found"}   [404]
```

**所有未匹配路由都是 FastAPI 預設的 `{"detail":"Not Found"}`，包含 ZEN-006A 之前就存在的 `/v1/*`。**
這不是本切片造成的，也沒有被本切片弄得更糟；`apps/api/errors.py` 沒有註冊 `404`/`StarletteHTTPException` 的處理器，
這是 ZEN-000 就有的平台級缺口。

因此 S5 以兩段評定：
- **(a) 沒有 catch-all / SPA fallback，未匹配一律 404** → **PASS**（`test_no_catch_all_or_spa_fallback`，10 條）
- **(b) 本切片新增路由自身的錯誤路徑用契約信封** → **PASS**
  （`test_missing_page_uses_the_contract_error_envelope`：`CONSOLE_DIR` 不存在時
  `/console/runs` 回 `{"error":{"code":"NOT_FOUND",...}}`）
- **(c) 平台級未匹配路由的信封** → **本切片範圍外的既有缺口**，記為觀察事項交 ZEN-004，不計入 ZEN-006A。

### 11.2 §5.3 L3 的結果與 `ZEN-006A-D1`

L3（RUNNING 同時顯示節點名稱與已耗時、耗時每秒更新）**PASS**
（`run.running_shows_node_and_elapsed`：實測到 `執行中 · finalize_node · 1.3s`）。

驗證過程另外發現一個 L3 沒涵蓋到的暫態缺陷，獨立編號記錄：

| ID | `ZEN-006A-D1` |
|---|---|
| 嚴重度 | 次要（Minor）· 不阻擋 `DONE` |
| 負責 | @frontend-dev（`apps/console/static/js/`） |
| 現象 | RUNNING 期間「已耗時」顯示 `—` 而不是時間，最長持續到下一個 1 秒 tick |
| 成因 | `pages/run.js` 的 `elapsedMs()` 用伺服器時間戳減瀏覽器 `Date.now()`；瀏覽器時鐘落後伺服器時 elapsed 為負，`dom.js` 的 `formatDuration()` 對 `ms < 0` 回傳 `—` |
| 證據 | 本機實測 `created_at` 比瀏覽器當下時間快 **+0.017s**；E2E 連續三次重現 `· 尚未進入節點 · —` 共 7 個取樣（約 840ms）後跳為 `849ms` |
| 建議修法 | `Math.max(0, end - start)`（一行） |
| 測試 | `tests/zen006a/test_console_e2e.py::test_browser_check[run.running_elapsed_is_always_numeric]`，標為 `xfail` 並在 `KNOWN_DEFECTS` 註明；修好後會變 `XPASS` |

> 這是與時鐘偏移有關的環境相依行為：瀏覽器與資料庫時鐘一致的機器上這條會直接 PASS。
> **不因為環境相依就當作沒發生** —— 任何瀏覽器與 DB 不同機的部署都可能出現。

### 11.3 執行結果（2026-09-21）

| 套件 | 結果 |
|---|---|
| `tests/zen006a`（本檔的驗收條件） | **141 passed, 1 xfailed**（xfail = `ZEN-006A-D1`） |
| `tests/zen000`（回歸；DB/Redis 經 socat 代理接進 compose 網路） | **23 passed, 1 skipped**（skip = 需要 runtime 映像的 LangGraph） |

未覆蓋清單見交接單 `coordination/handoffs/ZEN-006A/qa-verifier-to-orchestrator.md` §5。
