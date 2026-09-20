---
description: 驗收 — 執行品質閘門 G1-G6，判定任務能否進 DONE
---

用 `qa-verifier` agent 驗收任務：$ARGUMENTS

依 `AGENTS.md` §6 的六道閘門逐項檢查，**每一項都要附證據**：

| 閘門 | 檢查 |
|---|---|
| G1 | 符合 SPEC 五條核心立場 P1–P5 |
| G2 | 實作與 `specs/api-contract.md` 一致（契約測試綠燈） |
| G3 | 單元測試覆蓋新增邏輯；核心路徑有整合測試 |
| G4 | 涉及沙盒 / 憑證 / 網路出口者，通過安全驗證清單 |
| G5 | 有交接單、SPEC 若有變已 bump 版本 |
| G6 | 沒有未解的 SPEC-QUESTION |

**多租戶隔離是每次必測**：
- A 租戶的 token 查 B 租戶的 `run_id` → 必須 404（不是 403，不洩漏存在性）
- A 租戶的 Agent 查 B 租戶的知識集合 → 必須查無結果

**若涉及沙盒**，跑完整安全清單（見 `.claude/agents/qa-verifier.md`）。

判定規則：
- 任何一項未過 → **退回**，寫 `coordination/handoffs/<id>/qa-verifier-to-<原 agent>.md`，附**重現步驟**
- 測試跑不起來 ≠ 通過。環境壞掉就說環境壞掉
- **絕不因為「看起來應該沒問題」而放行**
- 通過 → 更新 BOARD 為 `DONE`，並誠實列出**未覆蓋**的部分
