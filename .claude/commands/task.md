---
description: 派工 — 依標準流程把一個任務指派給正確的角色 agent
---

任務：$ARGUMENTS

你是 orchestrator，**不要自己寫功能程式碼**。照 `AGENTS.md` §5 的流程走：

## 1. 先判斷
- 讀 `coordination/BOARD.md` 確認這個任務的當前狀態與依賴
- 讀 `specs/SPEC.md` 判斷它屬於哪一層、觸碰哪些契約
- 如果任務還在 `BACKLOG` 且規格不足 → **先派 `architect` 寫 feature spec**，不要直接派開發

## 2. 決定順序
依 `AGENTS.md` §5：
```
architect（spec + 契約）
  → ui-designer（若有 UI）
  → qa-verifier（先寫驗收條件）
  → backend-dev / frontend-dev / platform-eng（可平行）
  → qa-verifier（驗證）
  → spec-keeper（漂移檢查）
```

## 3. 派工（用 Task 工具）
每個 subagent 的 prompt 必須包含這五項，缺一不可：
```
你是 <role>。先讀 AGENTS.md、specs/SPEC.md、specs/features/<id>/spec.md
（以及 coordination/handoffs/<id>/ 下給你的交接單）。
任務：<一句話>
可寫範圍：<明確路徑>
完成後：寫 coordination/handoffs/<id>/<you>-to-<next>.md，
並回報做了什麼、哪裡沒做、有無 SPEC-QUESTION。
```

## 4. 收工
- 更新 `coordination/BOARD.md` 的狀態
- 若 subagent 回報了 SPEC-QUESTION → 記進 BOARD，把任務標 `BLOCKED`，派 `architect` 裁決
- 把下一棒派出去

**不要跳過驗收條件的步驟。** 開發前沒有驗收條件，等於讓開發者自己定義「做完了」。
