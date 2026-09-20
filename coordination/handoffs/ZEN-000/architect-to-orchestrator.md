# ZEN-000 架構師交接

**任務**：`ZEN-000` 貫穿骨架  
**從**：@architect　**給**：@orchestrator  
**日期**：2026-09-21  
**Spec 版本**：`v0.2.2`；API 契約 `v0.2.2`

## 1. 我做了什麼

ZEN-000 原本引用舊版 API 契約，並漏列必要的 `/internal/runs/{id}/result`。
已把 builtin Agent 的固定 fixture、三條 Internal Gateway 路由、結果與終態的權責、
事件重送冪等、失敗／逾時語意收斂進規格與 ADR-0007。
**架構裁定：ZEN-000 仍可維持 `SPEC_READY`**；目前沒有待裁決的 SPEC-QUESTION。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/SPEC.md` | v0.2.2：事件冪等與 worker 完成權責 |
| `specs/api-contract.md` | v0.2.2：Internal Gateway 內容格式、GET Run null 語意及終態責任 |
| `specs/features/ZEN-000/spec.md` | 三條路由、builtin fixture、失敗與驗收界線 |
| `specs/adr/ADR-0007-run-completion-and-event-retry.md` | 選項、決定、負面後果與重檢條件 |
| 本交接單 | 對主 session 和下一棒的執行要求 |

## 3. 契約影響

- [x] `specs/api-contract.md` 已更新至 `v0.2.2`；公開路由與枚舉未新增。
- `POST /internal/runs/{id}/events` 的 body 是**頂層事件陣列**，不是 `{ "events": [...] }`。
  每筆事件有重送不變的 UUID v7 `event_id`；`(run_id,event_id)` 在 DB 唯一。
  DB commit 後 Redis `XADD` 失敗，要用原事件與原 `seq` 補發，SSE 以 `seq` 去重排序。
- `/result` 只保存 output／error／usage；worker 確認沙盒退出後唯一寫終態與終局 `run.status`。
  兩者須在同一 DB transaction 提交；Redis 發布失敗時重送任務補發既有事件。
  非零退出、無結果、啟動失敗、逾時均不得報成功；逾時是 `FAILED` + `SANDBOX_TIMEOUT`。
- `GET Run` 未產生的 output／error／usage／時間欄位仍出現，值為 `null`；
  builtin 無 LLM 用量時數值為零、`cost_usd` 是字串 `"0"`。
- `/internal/*` 在同一 FastAPI 8000 port，物理上可從主機映射埠到達；
  MVP 的控制是 run token 驗證，不能把「非公開契約」誤當成來源 IP 已封鎖。
- `Idempotency-Key` 的 24 小時 HTTP 建立 Run 保證留給 ZEN-004；ZEN-000 的 Celery／事件冪等必做。

## 4. 下一個人要做什麼

1. 主 session 把 `specs/SPEC.md v0.2.2` 對齊註記更新到 `AGENTS.md`、`coordination/BOARD.md`，
   並將本交接傳給 @backend-dev、@platform-eng、@qa-verifier；看板狀態可保留 `SPEC_READY`。
2. @backend-dev 以固定 Agent ID `01930000-0000-7000-8000-000000000001`、`default`、`0.0.0-builtin`
   實作 `question` 字串 → `echo` 同值，並修正現有 UUIDv4 Run ID、events wrapper、token 發放／撤銷、
   result／終態事件及同步 `send_task` 等骨架問題。指定錯誤 entrypoint 回 `VALIDATION_ERROR`，
   非 builtin version 回 `AGENT_NOT_PUBLISHED`。
3. @platform-eng 讓 sandbox runtime 產生穩定 `event_id`、送三條 Gateway 路由，且在退出前送 `/result`；
   `SandboxDriver.destroy()` 必須冪等。一 Run 一盒與網路邊界不變。
4. @qa-verifier 先完成 `acceptance.md`，涵蓋 DB 成功／Redis 失敗後重送、失敗／逾時不得假報成功、
   token 範圍與終態撤銷、builtin input/output、UUID v7、契約 body/response；之後再執行測試。

## 5. 我沒做的事

沒有改 `apps/**`、`tests/**` 或 `coordination/BOARD.md`。沒有執行產品測試。
完整沙盒四層硬化仍屬 ZEN-003；cancel、列表、SSE `Last-Event-ID` 與 HTTP 建立 Run 的 24 小時去重仍屬 ZEN-004。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| 現有程式碼使用 UUIDv4 且以 `{events:[...]}` 接收 | 與 v0.2.2 契約不符 | backend-dev 修正，QA 契約測試鎖定格式 |
| DB commit 與 Redis `XADD` 分兩步 | 可能部分成功 | 穩定 `event_id` + DB 唯一索引 + 重送原 `seq`，SSE 去重 |
| Internal Gateway 與公開 API 共用 8000 | 網路上能到達路由 | 強制驗 run token；對外商用前重檢網路邊界 |
| 架構圖未展開事件重送所有失敗分支 | 圖只表達主線 | 以 SPEC v0.2.2、API 契約 §4 與 ADR-0007 為準 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 規格交叉核對 | 已做 | SPEC、API contract、ZEN-000 feature 與 ADR-0001～0006 對照 |
| 單元測試 | 未跑 | 本棒只修改規格 |
| 契約／整合測試 | 未跑 | QA 接手後執行 |

## 8. 未決的 SPEC-QUESTION

- [x] 無。BOARD 的 Q-001 是範例，非 ZEN-000 真實未決問題；主 session 將移出正式問題表。
