# ADR-0007：Run 完成權責與事件重送

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-21 |
| 決策者 | @architect |
| 相關 | [ADR-0002](ADR-0002-celery-langgraph-boundary.md)、[ADR-0005](ADR-0005-mvp-single-host-scope.md)；P1、P4 |

## 背景

ZEN-000 的沙盒經 Internal Gateway 回寫事件和結果，worker 負責沙盒生命週期。
如果沙盒送出「成功」就直接改 Run 終態，崩潰或非零退出可能被誤報成功。
如果 Postgres 已保存事件而 Redis `XADD` 失敗，重送同一批事件可能在 `run_events` 留下重複紀錄。

## 選項

### A. worker 從容器日誌擷取全部事件和結果

- 好處：worker 可在一處判定終態。
- 代價：事件只能依賴日誌解析，難以給沙盒明確回寫確認；重送和部分日誌截斷更難處理，違背 Internal Gateway 已定的事件路徑。

### B. Gateway 接收沙盒 `/result` 時直接將 Run 設為終態，事件無來源 ID

- 好處：請求和資料表較少。
- 代價：沙盒可能在回報成功後異常退出；DB 成功而 Redis 失敗時，重試會重複落事件；worker 與 Gateway 都可能發終局事件。

### C. Gateway 冪等保存資料，worker 確認退出後唯一決定終態（採用）

- 好處：只有一方能宣布 Run 結束；每筆沙盒事件有重送不變的 UUID v7 `event_id`，
  `(run_id,event_id)` 唯一約束可在 Redis 發布失敗後安全重試。
- 代價：Gateway 需處理 DB 與 Redis 的部分成功，SSE 需以 DB `seq` 去重；worker 需核對退出狀態及結果是否已保存。

## 決定

採用 C。沙盒將事件以頂層 JSON 陣列 POST 到 `/internal/runs/{run_id}/events`，
並在退出前 POST `/result`。Gateway 先提交 `run_events`，再發布 Redis 通知。
重送相同 `event_id` 時查回原 `seq`，不能建立第二筆 DB 事件；SSE 依 `seq` 排序與去重。
`/result` 只保存 output、error、usage。worker 確認沙盒退出與結果後，在同一 DB transaction
轉換 Run 終態並新增唯一的終局 `run.status`；提交後發布 Redis 通知，然後撤銷 run token
並冪等銷毀沙盒。若 Redis 發布失敗，重送任務須查回已保存的終局事件並補發。
無結果、非零退出、啟動失敗或逾時都不能報 `SUCCEEDED`；逾時為 `FAILED` + `SANDBOX_TIMEOUT`。

## 後果

- 正面：Redis 暫時失敗不會讓 Postgres 產生重複事件；沙盒自報成功不能掩蓋失敗；`run_id` 可還原事件和結果。
- 負面：`run_events` 需要 `event_id` 欄位與唯一索引；Redis 可能收到重複通知，SSE 必須去重；部分成功路徑要有整合測試。
- `Idempotency-Key` 的 24 小時 HTTP 請求去重仍屬 ZEN-004；此決定只涵蓋同一 `run_id` 的任務與事件重送。

## 何時該重新檢討

若要跨主機擴展、事件吞吐使同步 DB→Redis 發布成為瓶頸，或需要比重試更強的交付保證，
改評估 transactional outbox 與獨立發布者。若要對外公開部署，另評估在網路邊界隔離 `/internal/*`；
MVP 同一 FastAPI port 的控制是 run token，沒有來源網段封鎖。
