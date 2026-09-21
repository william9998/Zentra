# 交接單 Handoff

**任務**：`ZEN-006A` Console 早期切片：Run 觀測頁 + 發起 Run（驗收裁定）
**從**：@qa-verifier　**給**：@orchestrator
**日期**：2026-09-21
**Spec 版本**：`SPEC v0.2.2` / `api-contract v0.2.2` / `ui-contract v0.4.0`

---

## 0. 裁定

> ## 🟡 有條件通過 —— `DONE`，但在 **G5 的 CHANGELOG 補上之後才生效**
>
> 在那之前任務維持 `REVIEW`。這是唯一的阻擋條件，而且不需要動任何程式碼。

| | |
|---|---|
| **阻擋條件（1 條，必須先做）** | `CHANGELOG.md` 的 `## Unreleased` 沒有任何 ZEN-006A 條目 → **G5 未過**。`CHANGELOG.md` 不在我的可寫範圍，請由主 session 或交件者補一行。補完即視為 `DONE`，**不需要我再驗一次** |
| **不阻擋的移交義務（3 條）** | BOARD 的三條契約偏離：判定為**可接受的過渡**，但必須帶進 ZEN-006（見 §3） |
| **不阻擋的開放缺陷（1 個）** | `ZEN-006A-D1`：RUNNING 期間「已耗時」暫態顯示 `—`（見 §4） |

**為什麼不是 `BLOCKED`**：三條偏離逐條符合我在動工前寫下的門檻（`specs/features/ZEN-006A/acceptance.md` §7），
沒有一條構成缺陷、安全風險或契約變更；`D1` 是次秒級的顯示暫態。
**為什麼不是無條件 `DONE`**：G5 是 `AGENTS.md` §6 明列的閘門，「缺一不可」，我不打算為了漂亮而放過一行 CHANGELOG。

**我沒有動 `coordination/BOARD.md`**（依指示，由主 session 串行整合）。

---

## 1. 我做了什麼

1. **先補驗收條件再驗**：`specs/features/ZEN-006A/` 原本不存在。我寫了
   `specs/features/ZEN-006A/acceptance.md`（範圍上限、ui-contract §6.3 六條、§4 五態、§2 狀態語言、
   契約一致性、P1–P5、三條偏離的判定門檻），**沒有替 @architect 補 `spec.md`**。
2. **自己重跑，沒有引用主 session 的 16/16**：新建 `tests/zen006a/`（4 個 pytest 檔 + 1 個零依賴的
   CDP driver），對**跑著的 compose + headless Chrome 153** 實跑。
3. **另外把 ZEN-000 的 10 條 DB 測試真的跑起來**（見 §2.3），所以「沒有回歸」這句話有 DB 層的證據，
   不是只有 10 條公開 API 測試。

## 2. 產出的檔案與執行結果

| 檔案 | 說明 |
|---|---|
| `specs/features/ZEN-006A/acceptance.md` | 驗收條件（§1–§10 事前寫、§11 驗證後補註，刻意分開） |
| `tests/zen006a/test_console_source_contract.py` | 37 條：ui-contract §6.3 六條硬性規則、§2/§3 視覺語言、契約枚舉、範圍上限、P4 靜態面 |
| `tests/zen006a/test_console_routes.py` | 45 條：live HTTP —— 頁面路由、靜態檔位元組比對、path traversal、OpenAPI 乾淨度、三個端點的契約 |
| `tests/zen006a/test_api_without_console.py` | 9 條：`CONSOLE_DIR` 不存在時 api 仍啟動、404 不是 500、新增路由用契約信封 |
| `tests/zen006a/cdp_console_e2e.mjs` | 瀏覽器 E2E driver（49 個具名檢查）。**零 npm 依賴**：node 24 內建 `WebSocket` 直驅 CDP（機器上沒有 Playwright，ADR-0006 也不准往 repo 加 node_modules） |
| `tests/zen006a/test_console_e2e.py` | 把 driver 的每個檢查變成一條具名 pytest；`ok === null`（沒觀察到）一律 `skip`，**絕不當成 pass** |

### 2.1 ZEN-006A 驗收套件

```bash
$ ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 PYTHONPATH=. \
    /private/tmp/zentra-backend-venv/bin/pytest tests/zen006a -q -rxXs
...
XFAIL tests/zen006a/test_console_e2e.py::test_browser_check[run.running_elapsed_is_always_numeric]
141 passed, 1 xfailed in 34.07s
```

唯一的 `xfail` 是 §4 的 `ZEN-006A-D1`，**用 `KNOWN_DEFECTS` 明文登記、不是刪掉也不是 skip**；
修好之後它會變 `XPASS`，不會靜悄悄地消失。

### 2.2 E2E driver 原始計數

`48 ok / 1 failed / 0 not-observed`，`fatal: null`，Chrome `153.0.8010.52`。

### 2.3 ZEN-000 回歸（沒有退化）

環境提示說主機原生 Postgres 佔住 `127.0.0.1:5432`、那 10 條 DB 測試跑不起來。
我沒有照實標「未驗證」了事 —— 開兩個 `alpine/socat` 容器接進 `zentra-net`，把容器的
postgres/redis 橋到 `127.0.0.1:15432 / 16379`，把那 10 條真的跑完，**跑完就把代理刪掉**：

```bash
$ ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 \
  ZENTRA_TEST_DB_URL=postgresql+asyncpg://zentra:zentra@127.0.0.1:15432/zentra \
  ZENTRA_TEST_REDIS_URL=redis://127.0.0.1:16379/0 \
  PYTHONPATH=. /private/tmp/zentra-backend-venv/bin/pytest tests/zen000 -v
...
23 passed, 1 skipped in 2.31s
```

僅存的 1 個 skip 是 `test_runtime_unit.py`（需要 runtime 映像裡的 LangGraph，主機 venv 沒有），
**與 ZEN-006A 無關**。對照：不接代理時是 `14 passed, 10 skipped`。

---

## 3. 三條契約偏離：逐條裁定（不阻擋，但必須帶走）

| # | 偏離 | 裁定 | 理由與證據 |
|---|---|---|---|
| D1 | `/` → `/console/runs`（契約表頭寫 `/console/agents`） | **可接受的過渡，不阻擋** | `GET /console/agents` 實測 `404`（`test_root_redirects_to_the_only_page_that_exists`）—— 照契約轉址會讓首頁必定 404，**轉到唯一存在的頁面才是對使用者誠實**。`apps/api/static.py` 的 `root()` docstring 寫明「ZEN-006 交付 agents.html 時要改回去」，且有測試釘住這段註記（`test_root_redirect_deviation_is_documented_in_code`） |
| D2 | 「發起測試 Run」`<dialog>` 不在 ui-contract §5 元件清單 | **可接受的過渡，不阻擋** | 它是**原生 `<dialog>`**（ui-contract §7「原生元素優先」），不是自製元件：實測 `tagName=DIALOG`、`:modal` 成立、ESC 與「取消」都能關、開啟時焦點落在 `#question`。沒有在 `components/` 新增檔案。只呼叫已存在的 `POST /v1/agents/{id}/runs` |
| D3 | `runs.html` 用 `localStorage` 暫存最近 `run_id` | **可接受的過渡，不阻擋** | 實測瀏覽器儲存體只有 `zentra.console.recentRuns` 一個 key，`sessionStorage` 空、`document.cookie` 空，內容只有 `runId/question/createdAt`，**沒有任何憑證**（P4 / ui-contract §6.4）。程式碼有 `ZEN-004 後刪除` 的區塊標記，測試釘住形狀（`test_localstorage_stash_is_marked_for_removal`） |

**三條都不得被當成契約已變更。** 移交條件（請主 session 寫進 BOARD，我不動 BOARD）：

> **ZEN-006 不得在 @ui-designer 與 @architect 對這三條做出裁決之前進入 `SPEC_READY`。**
> 裁決結果只有兩種：收斂回 `ui-contract.md`（bump 版本），或刪掉。

另外：@frontend-dev 交接單 §8 列了 **6 個 SPEC-QUESTION 候選**（Q1 `--dot-size`、Q2 子區塊 Empty 的按鈕、
Q3 `dom.js` 不在 §6.1 清單、Q4 欄位名當標籤文字、Q5 media query 字面值、Q6 字型 fallback），
**它們從來沒有真的開在 BOARD 上**。形式上 G6 通過（BOARD 的 SPEC-QUESTION 表是空的），
但實質上這 6 題沒人裁。建議主 session 把它們正式開成 `Q-00X` 交 @architect / @ui-designer，
與上面三條一起在 ZEN-006 前收斂。**這不阻擋 ZEN-006A。**

---

## 4. 開放缺陷 `ZEN-006A-D1`（次要，不阻擋）

| | |
|---|---|
| 負責 | @frontend-dev（`apps/console/static/js/`） |
| 現象 | RUNNING 期間狀態列的「已耗時」顯示 `—`，最長持續到下一個 1 秒 tick |
| 實測 | `["· 尚未進入節點 · —" ×7, "· 尚未進入節點 · 849ms" ×4, "· finalize_node · 1.3s"]`（每 120ms 取樣，連續三次重現） |
| 成因 | `pages/run.js` 的 `elapsedMs()` 用**伺服器時間戳**減**瀏覽器 `Date.now()`**；瀏覽器時鐘落後時 elapsed 為負，`dom.js` 的 `formatDuration()` 對 `ms < 0` 回傳 `—` |
| 佐證 | 實測 `created_at` 比收到回應當下的主機時間快 **+0.017s**（`created_at - host_after = +0.017s`）。任何瀏覽器與 DB 不同機的部署都可能出現，不是只有這台機器 |
| 建議修法 | `Math.max(0, end - start)`，一行 |
| 為什麼不阻擋 | 次秒級、會自己修正、不影響資料、不影響安全。ui-contract §2 的 L3 本身**已通過**（實測 `執行中 · finalize_node · 1.3s`），這是 L3 沒涵蓋到的暫態 |

---

## 5. 測了什麼 / 沒測什麼（誠實列出）

### 5.1 通過的重點（附實測值）

| 項目 | 證據 |
|---|---|
| **終態後 `EventSource` 真的關掉**（本切片最大風險） | `{"afterTerminal":1,"afterIdle":1,"watchedMs":20000}` —— 整個 Run 只有 **1 次** `/events` 請求，終態後再盯 20 秒仍是 1 次。重新載入已終態的 Run 也是 `reloadEvents:1` |
| **`innerHTML` 沒碰到任何 API 字串**（vanilla 最容易出的 XSS） | 靜態面：解析每個 `.js`，`innerHTML` 賦值右側必須是模組內**無內插**的常數樣板（`FRAME`/`PAGE`），並對 `<p>${run.output}</p>` 這類反例做過偵測器驗證；動態面：真的送出 `<img src=x onerror=...><script>...` 走完 Run → `{"xssFired":null,"imgCount":0,"iframeCount":0,"inlineScripts":0,"eventHandlerAttrs":0}`，結果卡 `echo` 逐字元等於送出的字串 |
| **P4：同源前端不持有長期憑證** | `localStorage` 只有 `zentra.console.recentRuns`；`sessionStorage` 空；`cookie` 空；原始碼無 `api_key/Bearer/Authorization/secret` |
| **P4：沒有打開通往資料層或 Internal Gateway 的新路徑** | 全程請求 origin 只有 `["http://127.0.0.1:8000"]`；`/v1` 請求只有三個 path；`/internal` 0 次；原始碼無 `postgres/redis/5432/6379/internal` |
| **範圍上限** | 交付檔案集合逐檔比對；`api.js` 的請求路徑正規化後恰為 `{/agents/{}/runs, /runs/{}, /runs/{}/events}`；全專案只有 `api.js` 有 `fetch(`（1 次）與 `new EventSource(`（1 次）；無 `/cancel`、`/versions`、`/mcp/`、`listRuns`、`cancelRun` |
| **OpenAPI 未被 UI 路由污染**（P3） | `paths` 恰為三條 `/v1/*`，無 `/`、`/console*`、`/healthz`、`/internal*` |
| **ui-contract §6.3 六條** | 逐條有測：無執行期函式庫／無建置產物／`innerHTML` 稽核／無前端路由器（無 `pushState`/`popstate`/`hashchange`）／`tokens.css` 與 §1 **逐 token 逐值相同**、其他 CSS 零 hex 零 `rgb()`、px 字面值只有 `1px/2px/-1px/768px`／`mount()` 皆有 `destroy()` 且綁 `pagehide`+`beforeunload` |
| **§4 五態** | Loading 骨架屏、Empty（說明+secondary 按鈕，非「無資料」）、Error（`NOT_FOUND` mono + `run_id` + 下一步）、Streaming（`aria-live=polite` + 連線狀態）、Success 皆實測 |
| **§2 狀態語言** | 圓點實測 `6px/6px/999px`；六個標籤字串與 §2 逐字相同；`FAILED` 顯示 `SANDBOX_TIMEOUT`；`CANCELLED` 圓點色與 `FAILED` 不同；全 console 零 emoji（含 ⏳⚙✋✅❌🔴 的碼位掃描） |
| **已移除的值** | 全檔（去註解後）無 `WAITING_INPUT`／`tool.call`／`tool.result`／`interrupt`／`tier`；SSE 實際位元組也掃過 |
| **path traversal** | 6 種嘗試全部 404，回應從未離開 console 目錄 |
| **映像帶的就是當前檔案** | 13 個檔案逐一位元組比對「容器服務出來的」vs「repo 裡的」，全等 |
| **`apps/console` 缺席時 api 仍能啟動** | `/healthz` 200、OpenAPI 乾淨、`/` 仍 307、console 路由 404 且是契約信封（不是 500） |

### 5.2 ⚠️ 未覆蓋 / 未驗證（不得當成通過）

| 項目 | 狀態 |
|---|---|
| **Safari / Firefox** | **未測**。只有 Chrome 153。外部 SVG sprite `<use href="file.svg#id">` 的跨瀏覽器行為沒有實測 |
| **窄螢幕 / 行動版面** | **未測**。只有 1440×1000。@frontend-dev 已聲明手機版沒有漢堡選單 |
| **螢幕報讀器（VoiceOver）、完整鍵盤巡覽** | **未測**。只驗了 `aria-live`、原生元素與 focus ring 的存在 |
| **`prefers-reduced-motion` 的實際表現** | **未測**（CSS 有寫，沒開系統設定驗） |
| **剪貼簿真的寫入** | **未測**。headless 沒有剪貼簿權限 |
| **文字對比 ≥ 4.5:1（ui-contract §7）** | **未測**。沒有做對比度量測 |
| **`token` / `log` / `custom` 事件的真實渲染** | **未測**。ZEN-000 產不出這四種；我用 CDP `Fetch` 注入合成事件驗過「不會崩、各有摘要」，但**那不是真實資料** |
| **真實的 `FAILED` / `CANCELLED` Run** | **未測**。後端目前沒有 cancel 端點也產不出 FAILED；這兩個畫面是用 CDP 注入**契約形狀的合成回應**渲染出來的（真檔案、假資料），**不等於端到端** |
| **`Last-Event-ID` 續傳與真實斷線重連** | **未測**（ZEN-004 才做）。我驗的是「終態後不重連」這個相反命題 |
| **負載 / 併發**（佇列積壓、`QUOTA_EXCEEDED` 的畫面） | **未跑**。不在本切片範圍 |
| **沙盒 L1–L4 隔離清單** | **本次未重跑**。ZEN-006A 完全沒有動 `apps/sandbox/**`、`compose.yaml`、沙盒網段或 run token（`git show --stat 458d1b7` 佐證），不在變更半徑內。沿用 ZEN-000 的結論，**本裁定不對沙盒隔離做任何宣稱** |
| **L2 核心隔離（gVisor）／容器逃逸** | **依 ADR-0005 本架構不防這一類**。對應測試在 ZEN-000 是 `skipped`，**不是 `passed`**。這是已知取捨不是缺陷；但若有人提出要跑**外部來源**的 Agent，必須先擋下並開 `SPEC-QUESTION` |
| **G1 的 spec-keeper 覆核** | **未做**（不是我的角色）。依 `AGENTS.md` §5 流程，QA 通過後應交 `@spec-keeper`。我只以 `git show --stat 458d1b7` 稽核 diff 半徑：只動 `apps/api/{config,main,static}.py` + `apps/console/**` + `docker/api.Dockerfile`，未碰 routers / schemas / enums / services / models / migrations / worker / sandbox / compose / specs |

### 5.3 觀察事項（本切片範圍外的既有缺口，不計入 ZEN-006A）

**未匹配路由的 404 不是契約信封**：

```
/console/runs/a/b → {"detail":"Not Found"}    /console/agents → {"detail":"Not Found"}
/v1/unknown       → {"detail":"Not Found"}    /nonexistent    → {"detail":"Not Found"}
```

`apps/api/errors.py` 沒有註冊 `404`／`StarletteHTTPException` 的處理器，**ZEN-006A 之前就是這樣**
（`/v1/unknown` 是佐證），本切片沒有讓它變糟，而且本切片**新增路由自身**的錯誤路徑用的是正確信封
（`CONSOLE_DIR` 缺席時 `/console/runs` 回 `{"error":{"code":"NOT_FOUND",...}}`，已測）。
建議記在 BOARD 交 ZEN-004 一併處理。詳見 `acceptance.md` §11.1。

---

## 6. G1–G6 逐條

| 閘門 | 結果 | 說明 |
|---|---|---|
| G1 P1–P5 | ✅（我的部分） | P4 以瀏覽器實測（儲存體／origin／端點白名單）；P1/P2/P5 以 diff 半徑稽核 —— `static.py` 只有 `FileResponse`/`RedirectResponse`，無 `import`/`exec`/`eval`/`subprocess`，沒有任何載入使用者程式碼的路徑。**正式 G1 裁定屬 @spec-keeper，尚未覆核** |
| G2 契約測試 | ✅ | `tests/zen006a` 45 條 live 契約 + 37 條原始碼契約；`tests/zen000` 公開 API 契約 10 條全過 |
| G3 單元／整合 | ✅ | 新增邏輯（路由 + 靜態掛載）有 9 條 in-process 測試；核心路徑有端到端 E2E。前端**依 ADR-0006 無單元測試層**，這是規格如此，不是漏做 |
| G4 沙盒／憑證／出口 | ⚪ 不適用（憑證面 ✅） | 未動沙盒，未重跑 L1–L4；P4 的瀏覽器側已驗。見 §5.2 |
| G5 交接單／CHANGELOG／SPEC bump | ❌ **未過** | 交接單 ✅（三份 + 本份）；SPEC 未變故不需 bump ✅；**CHANGELOG 無 ZEN-006A 條目** ← 唯一阻擋條件 |
| G6 無未解 SPEC-QUESTION | ✅（形式） | BOARD 的表是空的。但 @frontend-dev 的 6 個候選從未開立，建議補開（§3），不阻擋 |

---

## 7. 下一個人要做什麼

1. **@orchestrator（解除阻擋）**：在 `CHANGELOG.md` 的 `## Unreleased` 補一條 ZEN-006A
   （Console 早期切片：`runs.html` + `run.html`、`/console/*` 路由、api 映像帶 `apps/console`）。
   補完即 `DONE`，**不需要我複驗**。
2. **@orchestrator（BOARD，我不動）**：
   - ZEN-006A → `DONE`（條件達成後），驗證者 `@qa-verifier`，連結本交接單與 `acceptance.md`
   - 記下移交條件：**ZEN-006 未經 @ui-designer / @architect 對三條偏離裁決前，不得進 `SPEC_READY`**
   - 開 `ZEN-006A-D1`（@frontend-dev，次要）
   - 把 @frontend-dev 的 6 個 SPEC-QUESTION 候選正式開成 `Q-00X`
   - 記下觀察事項：未匹配路由的 404 信封（交 ZEN-004）
3. **@spec-keeper**：依 `AGENTS.md` §5，QA 通過後做 G1 漂移覆核。
4. **@frontend-dev**：修 `ZEN-006A-D1`（一行）。修好後
   `tests/zen006a/test_console_e2e.py::test_browser_check[run.running_elapsed_is_always_numeric]`
   會變 `XPASS`，請順手把 `KNOWN_DEFECTS` 裡那一筆刪掉。
5. **ZEN-006 接手的人**：`tests/zen006a/` 的範圍上限測試（只准三個端點、只准 13 個檔案）
   **在 ZEN-006 擴張畫面時一定會紅**。那是刻意的護欄 —— 屆時連同驗收條件一起更新，不要直接刪測試。

## 8. 我沒做的事

- **沒有改 `coordination/BOARD.md`**（依指示，裁定寫在這裡由主 session 整合）。
- **沒有寫 `specs/features/ZEN-006A/spec.md`**（那是 @architect 的區域）。
- **沒有改任何 `apps/**`、`docker/**`、`CHANGELOG.md`** —— 我只寫了 `tests/zen006a/**` 與
  `specs/features/ZEN-006A/acceptance.md`。`ZEN-006A-D1` 我**沒有順手修**（不是我的區域，鐵則 2/6）。
- **沒有引用主 session 的 16/16 或 14 passed/10 skipped 當證據** —— 全部自己重跑。
  結論與主 session一致，另外多驗出 `ZEN-006A-D1`、多跑通 10 條 DB 測試。

## 9. 不是我寫的檔案（回報用）

工作區裡這三項**不是我產生的**，我也沒有碰、沒有納入本次驗收範圍：

```
 M docs/architecture/detailed-architecture.md
?? docs/architecture/zentra-request-flow.svg
?? coordination/handoffs/DOC-REQUEST-FLOW/architect-to-orchestrator.md
```

我在本任務只寫入 `specs/features/ZEN-006A/acceptance.md` 與 `tests/zen006a/**`。請主 session 追來源。

## 10. 未決的 SPEC-QUESTION

- [x] 我沒有新開。
  @frontend-dev 的 6 個候選與 BOARD 的 3 條偏離仍待 @architect / @ui-designer 裁決（§3），
  **在裁決之前都不得視為契約**。
