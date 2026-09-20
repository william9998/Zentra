# 交接單 Handoff

**任務**：`ZEN-000` 貫穿骨架  
**從**：@backend-dev　**給**：@qa-verifier  
**日期**：2026-09-21  
**Spec 版本**：`v0.2.2`

## 1. 我做了什麼

補上 worker 的完成判定路徑。沙盒只有在 exit code 為 0、且 Gateway 已提交 `/result` 時才會變成 `SUCCEEDED`；無結果、非零退出與逾時會變成 `FAILED`，並保留既有 `USER_CODE_ERROR`。狀態更新、終局 `run.status` 事件與 finished timestamp 在同一個資料庫 transaction；已終結 Run 再次執行會直接冪等返回。

另提供 async `finish()` adapter，供 Gateway/DB 驗證測試以 `AsyncSession` 驗證同一套終態規則；Celery task 仍使用同步 DB 實作，不會 import 或執行沙盒內 Agent 程式碼。

補上 async／sync 共用 run-token 撤銷 API。每個 Run 以 Redis set 索引其 token，單 token revoke 與 run-wide revoke_run 都是冪等操作；worker 使用 sync issue/revoke_run，終態 cleanup 會撤銷該 Run 所有 token。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `apps/worker/tasks/run.py` | 新增 `_finish_sync()` 與 async `finish()`；修正零 exit 但沒有 result 時不得假報成功。 |
| `apps/api/services/runtoken.py` | 新增 `revoke`、`revoke_run` 與 worker 使用的 sync counterparts；issue 建立 per-run token index。 |

## 3. 契約影響

- [x] 沒有修改 `specs/api-contract.md`；沿用 v0.2.2 既有錯誤 code 與終態責任。
- Internal Gateway 事件格式未改。API 422 若再現，請用新建 runtime image 檢查實際送出的 `type`；契約允許 `custom`，不允許舊版 `progress`。

## 4. 下一個人要做什麼

1. 在 compose 網路內套用 migrated PostgreSQL/Redis 連線，執行 `tests/zen000/test_gateway_db_integration.py`；host 的 `127.0.0.1:5432` 不是目前 Colima compose PostgreSQL，不能用它判定 backend 失敗。
2. 重新跑 live public API/SSE 測試，確認 fresh runtime image 的 node events、`custom` event 與 terminal status 均為 202/成功。

## 5. 我沒做的事（誠實列出）

- 沒有修改 `tests/**`。
- 沒有修改 `apps/sandbox/**`；runtime image 舊版 422 的根因屬 image/runtime 內容，現有 image 內已可確認送出 `custom`。
- 沒有在 host 完成 DB integration：host port 對應到另一個 PostgreSQL，`zentra` role 不存在。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| worker image 曾在 `docker_driver.py` 加入前建立 | worker 會因缺少 `DockerSandboxDriver` 而退出 | 已重新 build，確認 compose worker 正常啟動。 |
| host `127.0.0.1:5432` 非 compose DB | Gateway integration 三項在 host 直接失敗 | 從 compose 網路執行測試，或使用可連到 Colima compose DB 的正確轉發。 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| Python compile | ✅ | `python -m py_compile apps/api/services/runtoken.py apps/worker/tasks/run.py` |
| 單元測試 | 未跑 | runtime/public tests 因 host 未安裝 LangGraph 而 skip |
| Gateway integration | 未跑 | host DB 回 `role "zentra" does not exist` |
| Token revoke smoke | ✅ | compose worker 內驗證 issue、單 token revoke、run-wide revoke_run |
| API live smoke | ✅ | POST Run → worker/sandbox → `SUCCEEDED`，output `{"echo":"token-smoke"}` |
| Backend image build | ✅ | `docker compose build api worker`; api/worker healthy |

## 8. 未決的 SPEC-QUESTION

- [x] 無
