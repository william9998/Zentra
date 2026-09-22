# ZEN-008 前端開發交接給 QA

**任務**：`ZEN-008` OCI image 型 Agent 註冊、呼叫與一次性執行
**從**：@frontend-dev　**給**：@qa-verifier
**日期**：2026-09-22
**Spec 版本**：`v0.3.1`（API v0.3.1、UI v0.5.1）

## 1. 我做了什麼

Console 已從 ZEN-006A 的兩頁鷹架擴成契約指定的五頁。使用者可在 Agents 頁建立 metadata、在
version-new 頁輸入 semver 與 canonical OCI digest 並輪詢 `BUILDING → PUBLISHED / FAILED`，再於 Agent 詳情
選擇已發布版本與 entrypoint、輸入 JSON object 建立 Run。建立成功只導向既有 `/console/runs/{run_id}`，
Run 終態與 SSE 仍由原觀測頁接手。`api.js` 集中正規化 Agent / Version / Run 欄位；Run request 僅建立
`version`、`entrypoint`、`input` 三個 JSON 欄位，沒有 trigger body/header、image ref 或 runtime 控制項。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `apps/console/agents.html`、`agent.html`、`version-new.html` | 新增三個契約頁，與既有 Runs/Run 組成五頁 |
| `apps/console/static/js/api.js` | API v0.3.1 wrappers、錯誤、Agent/Version/Run view-model normalization |
| `apps/console/static/js/components/create-agent-dialog.js` | 原生 dialog metadata 建立流程、欄位與 API error focus |
| `apps/console/static/js/components/agent-list.js` | Loading/Empty/Error/Partial/Success、搜尋與 cursor 載入 |
| `apps/console/static/js/components/agent-version-list.js` | 三種版本狀態、artifact/錯誤展開、BUILDING 2 秒輪詢 |
| `apps/console/static/js/components/version-registration.js` | OCI digest 本地檢查、不可變確認、輪詢、終態摘要與完整 Copy |
| `apps/console/static/js/components/run-composer.js` | PUBLISHED-only 選擇、entrypoint/schema、JSON object 驗證、建立 Run 與導頁 |
| `apps/console/static/js/components/endpoints-card.js` | REST / MCP / OpenAPI 三門面與完整 URL Copy |
| `apps/console/static/js/components/recent-runs.js` | Agent 最近 Runs 的獨立五態與觀測頁連結 |
| `apps/console/static/js/pages/agents.js`、`agent.js`、`version-new.js` | 三頁組裝、資料生命週期與 destroy cleanup |
| `apps/console/static/js/pages/runs.js` | 移除 localStorage 測試鷹架，改接真 Run list/filter/cursor API |
| `apps/console/static/js/shell.js` | 啟用 Agents 導覽 |
| `apps/console/static/js/dom.js` | 共用安全 Error UI、bytes formatting；動態資料仍只走 `textContent` |
| `apps/console/static/css/base.css`、`layout.css`、`tokens.css` | v0.5.1 表單、列表、版本、endpoint、responsive 樣式；未新增 token |

## 3. 契約影響

- [x] 有影響：實作消費已核准的 `api-contract.md v0.3.1` 與 `ui-contract.md v0.5.1`，未變更契約。
- Version registration 為 JSON `semver + image_ref`，沒有 multipart、tar、credential 或 Docker build UI。
- Run composer 只呼叫 async Run endpoint，request 沒有 `trigger` 或任何識別 header；server 應記為 `API`。
- Console 依賴 API 契約既有的 `GET /v1/runs` 精簡列表，以及五條 Console 靜態頁 route。

## 4. 下一個人要做什麼

1. 等 backend 合入三條 Agent `FileResponse` routes、`/` 轉址與 `GET /v1/runs` 後，跑 Z8-28 / Z8-29 真後端 Playwright。
2. 攔截 version registration request，斷言 JSON 只有 semver/image ref；攔截 create Run，斷言 body 恰為 version/entrypoint/input 且沒有 trigger/header discriminator。
3. 對 BUILDING→PUBLISHED、BUILDING→FAILED(PULL/INSPECT/VALIDATE)、VERSION_CONFLICT、schema error 做五態與 focus 驗收。
4. 驗證離開 Agent/version-new 時所有 2 秒 polling 停止；Run 頁離開時既有 EventSource 仍會 close。
5. 用長 canonical image ref 驗證畫面不水平溢位且 Copy 內容未截斷；鍵盤走完 dialog、details、form 與導頁。

## 5. 我沒做的事（誠實列出）

- 沒有修改 `apps/api/**`；目前觀察到 `apps/api/static.py` 尚缺三條 Agent 頁 route、root 仍轉 Runs，已直接通知 backend。
- 目前觀察到後端尚未提供 `GET /v1/runs` list；recent-runs 與 Runs 頁會在該端點上線前顯示契約 Error 態，已通知 backend。
- 沒有新增測試（`tests/**` 是 QA 可寫區）；沒有真 Registry / Docker / Celery / SSE E2E 證據。
- 沒有改寫既有 Run 觀測頁或另開第二套 SSE；ZEN-008 只把 create Run 成功導向既有觀測頁。
- 沒有 image tar upload、Registry credential、Dockerfile editor、command/port/network/volume 控制項或第六頁。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| 後端靜態 route 尚在整合 | 三個 HTML 已存在但乾淨 URL 暫時 404 | backend 補 route 後納入 route/E2E 測試 |
| Run list 後端尚在整合 | Runs 與 recent-runs 暫時只能驗 Error 態 | 依 API v0.3.1 補精簡列表後測 filter/cursor |
| 舊 ZEN-006A source test 鎖定 v0.2.2 兩頁切片 | 新增合法檔案、VERSION_CONFLICT、Agent/Version endpoints 與移除鷹架使 7 項必然失敗 | QA 以 ZEN-008 測試取代那些 scope-ceiling 斷言，不應把舊預期改成產品行為 |
| 尚未做真瀏覽器視覺驗證 | responsive、焦點返回與五態仍需 Playwright/人工確認 | 在整合後開五頁逐態驗證 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| ES module 語法 | ✅ | 15 個相關 `.js` 全部通過 `node --check` |
| 格式檢查 | ✅ | `git diff --check -- apps/console` 無輸出 |
| DOM/網路靜態掃描 | ✅ | 動態內容使用 DOM/textContent；`fetch`/`EventSource` 只在 `api.js`；無 FormData/file input/trigger header/credential UI |
| 舊 Console source contract | ⚠️ 30 passed / 7 failed | `PYTHONPATH=. pytest -q tests/zen006a/test_console_source_contract.py`；7 項皆明確鎖 v0.2.2 / ZEN-006A exact slice 與已移除鷹架 |
| Z8-28 / Z8-29 Playwright | 未跑 | 後端 routes、Run list 與真 Registry/Docker 整合尚未完成 |
| 五頁 Loading / Empty / Error 人工瀏覽 | 未跑 | 同上；不能宣稱視覺驗收通過 |

## 8. 未決的 SPEC-QUESTION

- [x] 無。前端缺口是已定契約的後端整合與 QA 驗收，不需要自創欄位或狀態。
