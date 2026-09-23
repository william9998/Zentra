---
description: 產出交接單 — 只有跨 session 的大功能需要，小改動不用寫
---

依 [`coordination/handoffs/TEMPLATE.md`](../../coordination/handoffs/TEMPLATE.md) 的格式寫交接單。

> **先確認真的需要。** 只有**大**級改動（改契約、新 ADR、動沙盒與憑證邊界）
> 或工作要跨 session 接手時才寫。小改動寫交接單是純粹的浪費 —— 直接在回報裡講就好。

參數：$ARGUMENTS（格式：`<task-id> <from-role> <to-role>`）
寫到 `coordination/handoffs/<task-id>/<from>-to-<to>.md`。

八個段落全部要填：
1. 我做了什麼（結果導向，不是流水帳）
2. 產出的檔案
3. 契約影響（有改契約就要寫清楚並確認 `api-contract.md` 已同步）
4. 下一個人要做什麼（具體、可執行）
5. **我沒做的事**（這欄空白通常代表沒想清楚）
6. 已知限制與風險
7. 驗證狀態（**測試沒跑就寫「未跑」，不要寫「應該沒問題」**）
8. 待使用者裁決的事
