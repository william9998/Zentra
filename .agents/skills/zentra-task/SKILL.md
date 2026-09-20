---
name: zentra-task
description: 依 Zentra 契約優先流程拆解並派遣功能任務，串接架構、設計、開發、QA 與規格稽核；用於實作或接續具體專案任務。
---

# Zentra 任務派工

先定位專案根目錄，讀 AGENTS.md、specs/SPEC.md、coordination/BOARD.md，
以及指定任務的交接單、feature spec、驗收條件與相關契約。缺少產品規格先交 architect；
有 UI 再交 ui-designer；實作前由 qa-verifier 確定驗收條件。
主 session 負責協調，不自己寫產品功能程式碼。

使用當前環境實際可用的子 agent 工具與 .codex/agents/ 的角色名稱。
依依賴選擇必要角色，不固定全部啟動。角色或工具不可用時如實說明，
完成可做的範圍盤點與交接準備，不冒充已委派或通過獨立驗證。

每次派工給出：
- 任務 ID、目標與完成條件。
- 先讀的交接單，再讀 AGENTS.md、SPEC、對應 .claude/agents/<role>.md、feature 與契約。
- 明確可寫路徑、驗證方式、交付物與下一棒交接單路徑。
- 提醒有其他工作者，不得還原他人修改，並適應已有變更。

只有可獨立且寫入範圍不重疊的工作才能平行；同檔與前置契約變更串行處理。
子 agent 可交換問題或由主 session 轉交，決策必须落在規格／ADR／handoff。
有 SPEC-QUESTION 交 architect 裁決，受影響部分停止，繼續不相依工作。
主 session 串行整合 BOARD 更新，保留 architect 的 SPEC_READY 與 QA 的 DONE 裁定權。

實作交接後，派 qa-verifier 驗證，再由 spec-keeper 稽核；有問題退回責任角色。
只有 QA 確認所有適用閘門與稽核結果後才能 DONE。
收尾回報改動、驗證證據、限制與交接位置；不自動 commit、push 或部署。
