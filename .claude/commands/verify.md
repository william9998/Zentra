---
description: 驗收 — 實際跑一次改到的路徑；動到沙盒/憑證才加跑冒煙與隔離清單
---

驗收：$ARGUMENTS

## 1. 驗什麼（照 CLAUDE.md §驗證）

| 改了什麼 | 怎麼驗 |
|---|---|
| 文件、規格、註解 | 不用驗，直接收工 |
| 一般程式碼 | 重建映像，用 curl 或 Console 把改到的路徑實際走一次 |
| 沙盒 / 憑證 / 網路出口 / Run 執行路徑 | 上面 + `make smoke` + 下面的隔離清單 |

## 2. 隔離清單（只有第三列才跑）

- 沙盒內連 `postgres:5432`、`redis:6379`、`1.1.1.1`、`169.254.169.254` → 全失敗；連 `app:8000/internal/...` → **成功**
- 寫 `/`、`/app` → 失敗；寫 `/workspace` → 成功；`id` → 非 root
- 沙盒 `env` 裡沒有任何 LLM key、DB 密碼、使用者機密
- 拿 run A 的 token 打 run B 的 `/internal/*` → `403`；Run 結束後再用 → `401`

driver `capabilities()` 沒提供的那層標 `skipped`，不准標 `passed`。
報告寫「四層 Docker 隔離，無核心隔離」，不要寫「沙盒安全」。

## 3. 回報

- 附指令與輸出。沒過就說沒過；環境跑不起來 ≠ 通過。
- 有在平台上建 Agent / Run → 最後跑 `make clean-test-data`。
- 列出沒覆蓋到的部分。
