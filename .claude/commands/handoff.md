---
description: 產出交接單 — 依 TEMPLATE 格式寫下這次工作的交接，供下一棒接手
---

依照 `coordination/handoffs/TEMPLATE.md` 的格式，為剛完成的工作寫一份交接單。

參數：$ARGUMENTS（格式：`<task-id> <from-role> <to-role>`）

寫到 `coordination/handoffs/<task-id>/<from>-to-<to>.md`。

**八個段落全部要填，不准跳過**：
1. 我做了什麼（結果導向，不是流水帳）
2. 產出的檔案
3. 契約影響（有改契約就必須寫清楚並確認 api-contract.md 已同步）
4. 下一個人要做什麼（具體、可執行）
5. **我沒做的事**（這欄空白通常代表沒想清楚）
6. 已知限制與風險
7. 驗證狀態（**測試沒跑就寫「未跑」，不要寫「應該沒問題」**）
8. 未決的 SPEC-QUESTION

寫完後更新 `coordination/BOARD.md` 的任務狀態。
