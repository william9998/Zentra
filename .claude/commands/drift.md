---
description: 規格漂移稽核 — 檢查程式碼是否偏離 SPEC 與契約
---

用 `reviewer` agent 執行漂移稽核（範圍：$ARGUMENTS，未指定則全專案）。
完整稽核清單在 [`.claude/agents/reviewer.md`](../agents/reviewer.md) §三。

快速起手：

```bash
# P1：平台行程內有沒有執行使用者程式碼的痕跡
grep -rn "exec(\|eval(\|importlib\|__import__\|subprocess" apps/worker apps/api

# 已移除的規格值還在不在
grep -rn "WAITING_INPUT\|tool\.call\|tool\.result\|interrupt\|tenant_id\|source_type\|alias" apps tests

# Run 狀態字串有沒有散落的字面值（應全部引用枚舉常數）
grep -rn "QUEUED\|SCHEDULING\|RUNNING\|SUCCEEDED\|FAILED\|CANCELLED" apps
```

再逐項比對：Pydantic schema vs `api-contract.md` 的欄位名/型別/必填；
`api.js` 讀的欄位；compose 的網路拓撲（只有 `api` 能接 `zentra-sandbox-net`，只有 `worker` 能掛 `docker.sock`）。

結果寫進 `coordination/DRIFT.md`，分紅旗（P1–P5）／黃旗（契約漂移）／藍旗（規格缺漏）。
**只回報，不要動手修。**
