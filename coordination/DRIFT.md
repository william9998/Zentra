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
