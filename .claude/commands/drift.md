---
description: 規格漂移稽核 — 檢查程式碼是否偏離 SPEC 與契約
---

用 `spec-keeper` agent 執行漂移稽核（範圍：$ARGUMENTS，未指定則全專案）。

重點檢查（詳見 `.claude/agents/spec-keeper.md`）：

**A. 核心立場 P1–P5**（紅旗）
- P1：`grep -rn "exec(\|eval(\|importlib\|__import__\|subprocess" apps/worker apps/api`
- P4：沙盒 manifest / env 注入裡有沒有長期憑證
- P5：有沒有修改已發布 agent version 的路徑

**B. 契約漂移**（黃旗）
- `apps/api/schemas/**` vs `specs/api-contract.md` 逐欄位比對
- 前端型別是否從 codegen 來，還是有人手刻

**C. 硬編碼的規格**
- Run 狀態字串、錯誤 code 是否都引用枚舉常數
- 前端有沒有寫死色碼、間距

**D. 分層違規**
- 沙盒程式碼直連 DB、路由層寫業務邏輯、跨層 import

**E. 未決事項**
- BOARD 上懸而未決的 SPEC-QUESTION
- 程式碼裡的 `TODO: 待確認`（私自繞過提問流程）

結果寫進 `coordination/DRIFT.md`，**每一則都要指名該由誰處理**。
只回報，不要動手修。
