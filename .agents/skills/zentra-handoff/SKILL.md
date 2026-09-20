---
name: zentra-handoff
description: 為 Zentra 已進行的工作依模板撰寫交接單，記錄契約影響、驗證證據、未完成事項與接手步驟。
---

# Zentra 工作交接

定位專案根目錄，讀 AGENTS.md、specs/SPEC.md、coordination/BOARD.md，
以及 coordination/handoffs/TEMPLATE.md、已有交接與本次變更證據。
從請求與目前工作取得 task-id、from-role、to-role；必要識別資訊無法判定才詢問。

寫 coordination/handoffs/<task-id>/<from>-to-<to>.md，填寫模板八個段落。
保留既有交接紀錄，可追加有日期的修訂；勿抹掉前一輪失敗或尚未解決的限制。
記下依據的實際 SPEC／契約版本、修改的檔案、下一棒可執行步驟。
清楚列出未做的部分；測試未跑就寫未跑，不能從程式碼看起來正確推導已通過。
契約若有變更，確認 architect 的裁決與 frontend-dev／backend-dev／qa-verifier 通知。
角色權限依 AGENTS.md；BOARD 的狀態更新交主 session 串行整合。
交接本身不表示 DONE，也不會取代 QA 與 spec-keeper 的驗證。
