# 規格漂移報告 (Spec Drift)

> 由 `@spec-keeper` 維護。這不是 bug list，是「規格與現實脫鉤」清單。
> 分級：🔴 違反核心立場 P1–P5｜🟡 契約漂移｜🔵 規格缺漏（程式碼比規格完整，該補規格）

## 稽核紀錄

_（尚未執行首次稽核。建議在 ZEN-003 與 ZEN-004 完成後執行第一次。）_

## 稽核 2026-09-20 — DEV-CODEX 協作設定

範圍：`AGENTS.md`、七份 Claude/Codex 角色、五個專案 skills、`docs/codex.md` 與 README 協作說明。
依據：SPEC v0.2.1、API 契約 v0.2.1、UI 契約 v0.4.0、既有 ADR、DEV-CODEX 架構交接。
本次不稽核產品程式碼或判定任何 ZEN 任務完成。

### 🔴 違反核心立場
- 在本次工具設定範圍內未發現新增違反 P1–P5 的指示；產品實作及沙盒安全未驗證。後續產品驗證由 @qa-verifier 與對應執行角色負責。

### 🟡 契約與協作流程漂移
- [D-CODEX-01] 初查 `.claude/agents/qa-verifier.md:30` 的驗收範例仍要求 `error.code` 屬於「SPEC §8.4 的枚舉」；對應 Codex TOML 正文相同。SPEC §8.4 只有錯誤格式，完整枚舉在 `specs/api-contract.md` §0，且架構交接已要求修正。
  → 由 @orchestrator 依 @architect 既有裁決同步修正兩份角色文件，@qa-verifier 重驗。
- [D-CODEX-02] 初查 `.claude/agents/architect.md:51` 與 `.claude/agents/qa-verifier.md:104` 仍直接要求角色更新 BOARD；對應 TOML 正文相同。`AGENTS.md:83` 與 `docs/codex.md:73` 已規定 BOARD 由主 session 串行整合，角色保留裁定權。兩種指令並存會使同一份 BOARD 的寫入責任不一致。
  → 由 @orchestrator 同步角色交接段，明示角色提交裁定、主 session 整合 BOARD；@qa-verifier 重驗。

### 🔵 規格缺漏
- 本次未發現需要新增產品規格的事項。`AGENTS.md:196` 已區分開發流程 skills 與 MVP 外的產品 Skill Registry。維護責任為 @orchestrator；產品範圍裁定仍屬 @architect。

### ✅ 本次確認一致
- 七份 TOML 均能由 Python `tomllib` 解析，`developer_instructions.strip()` 與同名 Claude Markdown 去除 frontmatter 後正文相等；未設定額外模型或權限欄位。後續同步維護由 @orchestrator 負責。
- `.claude/agents/platform-eng.md:28` 已引用現行 driver 方法；`:37`、`:84` 對齊四服務拓撲。`.claude/agents/backend-dev.md:62` 明示事件由沙盒 POST 到 Internal Gateway。對應維護角色為 @platform-eng、@backend-dev。
- `.claude/agents/ui-designer.md:43` 對齊 15 分鐘上限；spec-keeper 改核對原生 JS 與契約，不要求 TypeScript/codegen。對應維護角色為 @ui-designer、@spec-keeper。
- 五個 skills 保留角色分工、契約優先、持久交接、独立 QA 與唯讀稽核；缺工具時不假稱獨立驗證。`docs/codex.md` 明示角色邊界並非作業系統 ACL。後續維護由 @orchestrator 負責。
- BOARD 的 Q-001 明確標為範例，不能當成 DEV-CODEX 真實未決提問；本次完成條件另列於 BOARD 的 DEV-CODEX 區段。最終驗收由 @qa-verifier 負責。

### 未驗證範圍
- 未重啟客戶端驗證新設定載入，未完整執行五個 skills，未跑產品測試；不能由以上靜態稽核推導產品功能或安全通過。

### 同日修正複查
- D-CODEX-01 已由 @orchestrator 修正：`.claude/agents/qa-verifier.md:30` 現在引用 `api-contract.md` §0；對應 TOML 正文再次比對相等。
- D-CODEX-02 已由 @orchestrator 修正：`.claude/agents/architect.md:51` 與 `.claude/agents/qa-verifier.md:104` 現在要求角色在交接單裁定，由主 session 串行整合 BOARD；兩份 TOML 正文再次比對相等。
- 本次發現均已關閉，工具設定範圍未留下阻擋性漂移。最終 DEV-CODEX 驗收裁定交 @qa-verifier。

## 稽核 2026-09-21 — ZEN-000 最終規格稽核

範圍：`apps/api/**`、`apps/worker/**`、`apps/sandbox/**`、`docker/**`、`compose.yaml`、ZEN-000 契約、驗收與交接單。
依據：SPEC/API 契約 `v0.2.2`、ZEN-000 feature spec、ADR-0001～0007。

### 🔴 違反核心立場

- 本次未發現 P1～P5 的直接紅旗。`apps/worker/tasks/run.py` 沒有載入使用者模組的 `exec`／`eval`／`importlib`／`subprocess` 路徑；`compose.yaml` 只有 `worker` 掛 `docker.sock`，`postgres`／`redis`／`worker` 沒接 `zentra-sandbox-net`，且 `apps/sandbox/manager.py` 只注入 `ZENTRA_RUN_ID`、`ZENTRA_RUN_TOKEN`、`ZENTRA_GATEWAY`。P3/P5 在 ZEN-000 依 feature spec 明確留給後續任務，不能據此宣稱已完成。

### 🟡 契約漂移

- **[ZEN000-DRIFT-01／冪等重送競態]** `apps/worker/tasks/run.py:192-215` 先在未加鎖的物件上檢查非終態，再以 `_transition(..., SCHEDULING)` 取得 row lock；`_transition` 沒有在鎖內重新確認狀態。兩個同一 `run_id` 的 Celery delivery 可先後都進入 `manager.reserve()`／`manager.create()`，而 `apps/sandbox/manager.py:92-99` 以相同 Redis member 只會覆寫租約，無法阻止第二個沙盒。這違反 ZEN-000「重送不得同時建立兩個沙盒」與 ADR-0002。→ 由 `@backend-dev` 修正並由 `@qa-verifier` 加入併發／重送證據。

- **[ZEN000-DRIFT-02／終局事件補發]** `apps/worker/tasks/run.py:192-195` 對任何已終態 Run 直接 `idempotent_skip` 返回；但 `_finish_sync()` 在 `apps/worker/tasks/run.py:121-125` 是先提交終態事件、再呼叫 `publish_pending()`。若 Redis `XADD` 在該步驟失敗，Celery 重送會走終態 early return，沒有檢查或補發 `published_at IS NULL` 的終局事件。這違反 `specs/api-contract.md:284-296` 要求的重送補發。→ 由 `@backend-dev` 修正，並由 `@qa-verifier` 驗證 DB commit 後 Redis 失敗的 SSE 證據。

- **[ZEN000-DRIFT-03／run token TTL]** 契約 §4 與 ZEN-000 feature spec 都定義 token TTL 等於 Run 的 `timeout_seconds`；`apps/worker/tasks/run.py:36,210-212` 卻使用 `TOKEN_TTL_BUFFER = 30`，把 token 設為 timeout + 30 秒。這也讓 worker 崩潰時 token 的存活時間超出規格。→ 由 `@backend-dev` 對齊 TTL；若要保留 buffer，先由 `@architect` 走 SPEC-QUESTION／bump 契約。

- **[ZEN000-DRIFT-04／驗收結果與 runtime 不一致]** `apps/sandbox/runtime/__main__.py:71-95` 目前只送四筆 `node.start`／`node.end` 與一筆 `/result`，沒有 `log` 或 `custom` 事件，也沒有建立 `ZentraContext` 呼叫 `ctx.logger`／`ctx.emit`；但 `specs/features/ZEN-000/acceptance.md:21-31,53` 宣稱實測有 11 筆且包含 `log`×2、`custom`×1，並宣稱 SDK 輸出出現在事件流。`tests/zen000/test_runtime_unit.py:46-65` 反而只驗四筆節點事件。→ 由 `@platform-eng` 與 `@qa-verifier` 先對齊驗收範圍與可重現證據；在此以前不得把 acceptance.md 的 27/0/2 宣稱當成 G1/G2 證據。

- **[ZEN000-DRIFT-05／驗收閘門有無效斷言]** `scripts/verify-zen000.sh:27-29` 要求 `log`／`custom`，依目前 runtime 必然失敗；`scripts/verify-zen000.sh:39` 以 `label=zentra.managed=true` 判定零殘留，但 `apps/sandbox/drivers/docker_driver.py:44` 只設定 `zentra.role` 與 `zentra.run_id`，所以這個檢查不會觀察到實際建立的沙盒。驗收腳本目前不能支持 acceptance.md:7 的結果。→ 由 `@qa-verifier` 修正腳本與重新執行；沙盒 label 由 `@platform-eng` 或測試改為使用實際契約欄位。

- **[ZEN000-DRIFT-06／套件安裝工具]** SPEC §6 與 §11 明定 Python 套件管理使用 `uv` only；`docker/agent-runtime.Dockerfile:5` 仍直接執行 `pip install`，而 API/worker Dockerfile 使用 `uv pip install`。→ 由 `@platform-eng` 統一 runtime image 的安裝方式，或由 `@architect` 記錄明確例外。

### 🔵 規格缺漏

- 本次沒有新增需要架構師裁決的產品規格缺漏；上述事件內容與驗收報告屬實作／測試證據未對齊，不能用補規格取代修正或重驗。

### ✅ 本次確認一致

- Run 狀態資料庫 constraint 與 `RunStatus` 都只接受 SPEC §9 的六個值；未發現實際產生 `WAITING_INPUT`、`tool.call`、`tool.result` 或 `interrupt` 的路徑。
- `SandboxEvent` 強制事件 `event_id` 為 UUID v7，Gateway 以 `(run_id,event_id)` 去重；`SandboxDriver.capabilities()` 明確回報 `l2_kernel_isolation=false`、`l4_filesystem_isolation=false`、`l5_sdk_interception=false`，未把未提供的層誤報為通過。
- `POST /internal/*` 經 run token 驗證；無 token 為 `UNAUTHENTICATED`，跨 Run token 為 `PERMISSION_DENIED`；API 未掛 Docker socket。

### G1 裁定

**G1 不通過。** P1～P5 沒有直接紅旗，但上述 `ZEN000-DRIFT-01`、`02`、`03` 是 ZEN-000 契約／核心流程的未修正偏離，`04`、`05` 使目前 acceptance 結果不可作為一致性證據。完整四層隔離的未覆蓋項目（gVisor／容器逃逸／L3 壓力測試）依 ZEN-003 的範圍標為未驗證或 skipped，沒有被本次誤判為通過。
