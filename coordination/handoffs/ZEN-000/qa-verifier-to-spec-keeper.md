# ZEN-000 QA 預驗收交接

**任務**：`ZEN-000` 貫穿骨架  
**從**：@qa-verifier　**給**：@spec-keeper（由主 session 整合）  
**日期**：2026-09-21  
**Spec 版本**：`v0.2.2`；API 契約 `v0.2.2`

## 1. 我做了什麼

修正驗收腳本的事件數、echo 大小寫、實際 Docker labels，並讓終態後零殘留檢查有 10 秒輪詢。獨立重跑真實 API → Celery → 沙盒 → Gateway → SSE 路徑，確認 27 通過／0 失敗／2 skipped。Compose 網路內的 23 個 pytest 測試通過，包含雙 delivery 的 PostgreSQL row lock、Redis 終態待補事件與精確 token TTL；另以 40 次 Run 的保守冷啟上界量得 nearest-rank P95 1.341s，40 個容器均不同且已銷毀。四個節點事件、結果與契約相符；未覆蓋的故障與隔離斷言詳見下文，**QA 不裁定 DONE**。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/features/ZEN-000/acceptance.md` | Z0-01～Z0-19 的當前證據與未覆蓋欄位；撤回舊版 11 事件及 `ctx.logger`／`ctx.emit` 宣稱。 |
| `scripts/verify-zen000.sh` | 對齊四個 node events、輸出大小寫與容器 labels；零殘留有界輪詢。 |
| `tests/zen000/test_worker_recovery.py` | 真 PostgreSQL claim 競爭、終態補發、四種失敗終態及 token TTL。 |
| `tests/zen000/benchmark_cold_start.py` | 40 次端到端冷啟上界、容器唯一與銷毀的可重跑量測。 |
| `tests/zen000/test_public_api_contract.py`、`test_gateway_db_integration.py`、`test_runtime_unit.py`、`test_driver_unit.py` | 先前本棒建立的公開 API、Gateway DB、runtime 與 driver 驗證。 |

## 3. 契約影響

- [x] 未修改 `specs/api-contract.md` 或 `specs/ui-contract.md`；測試依 v0.2.2 的 UUID v7、頂層 events 陣列、run token、worker 終態權責與 SSE `seq`。
- 目前 runtime 固定兩節點各一筆 start/end，共四筆 node events；`log` 和 `custom` 是允許型別，不是這個 fixture 的必發事件。

## 4. 下一個人要做什麼

1. @spec-keeper 對照最新程式、腳本與本驗收文件，重新裁定 `coordination/DRIFT.md` 中 ZEN000-DRIFT-01～06 和 G1；不要沿用修正前的行號／結果。
2. @backend-dev／@platform-eng 與 QA 安排真 create/start 失敗、非零退出、無 `/result` 及 1 秒 wall-clock timeout 注入，驗 `FAILED`、契約錯誤、一次終局、token 撤銷及容器銷毀。現有 DB 單元測試僅驗終態邏輯，不證明真沙盒清理。
3. QA 補有效 token **從 sandbox 內**呼叫 bootstrap、真容器 env/docker.sock 檢查、fresh DB migration／重啟持久性、失敗情境完整契約，再重新評估 G2～G4。BOARD 由主 session 串行整合。

## 5. 我沒做的事

- 未對真 Docker create/start、非零退出、無結果或 timeout 路徑做故障注入；`Z0-10/11` 只通過 `_finish_sync()` 對已知退出結果的資料庫測試。
- 未在沙盒內持有效 token 呼叫 bootstrap；只分別驗證沙盒對 API TCP 可達與 Gateway token 路由可用。未實讀真容器 env、檢查 socket 不存在。
- 未量 SSE 事件延遲、全新 DB 遷移／API 重啟持久性、Gateway DB commit 失敗與 Redis 部分失敗後的 live SSE 重播。
- 未跑 ZEN-003 的 gVisor、核心逃逸、cgroup 壓力或完整四層隔離攻擊測試；依 ADR-0005／任務範圍列 skipped 或未覆蓋。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| `coordination/DRIFT.md` 尚保留修正前 G1 不通過裁定 | QA 不能替 spec-keeper 宣稱 G1 過關 | 重新稽核後再由 QA 決定是否進 DONE。 |
| 40 次量測是 `POST → 首筆 node.start SSE` | 含 queue、bootstrap 後 graph 與 SSE，比純 Docker create/start 更長；若低於 2s，能作為 A1 保守上界，但不是原始 Docker API 時長 | 量測方法與數值一併保留。 |
| worker image 按 P1 沒有 LangGraph | 容器 pytest 跳過 runtime 3 tests | host 的 Python 3.11.7 另跑 3 passed，結果不混算。 |
| host `127.0.0.1:5432` 指向另一個 PostgreSQL | 主機 DB 測試會報 `role "zentra" does not exist` | 使用 Compose 內的 `postgres:5432`；本輪已如此執行。 |

## 7. 驗證狀態與 QA 裁定

| 檢查 | 結果 | 證據 |
|---|---|---|
| 真端到端／最小隔離 | 27 通過、0 失敗、2 skipped | `bash -n scripts/verify-zen000.sh && bash scripts/verify-zen000.sh`；gVisor／逃逸依 ADR-0005 跳過。 |
| Compose API／DB／Redis 契約與整合 | 23 passed、1 skipped | one-off worker：`PYTHONPATH=/srv`、三個 `ZENTRA_TEST_*` 指向 `api`／`postgres`／`redis`，`python -m pytest -q /srv/tests/zen000`；skip 為 worker 不裝 LangGraph。 |
| Runtime 單元 | 3 passed | host Python 3.11.7：`python -m pytest -q tests/zen000/test_runtime_unit.py`。 |
| 冷啟與沙盒唯一性 | 40 次、P95 1.341s、40/40 銷毀 | Compose one-off worker：`python /srv/tests/zen000/benchmark_cold_start.py`；最小 1.266s、中位 1.297s、最大 1.359s。 |
| G1 | **未過／待複核** | `coordination/DRIFT.md` 現有裁定仍不通過；本輪測試證明部分 backend 修正，但 QA 無此閘門裁定權。 |
| G2／G3／G4 | **部分通過，尚不放行** | 已列測試綠燈；真故障注入、token＋sandbox 正反驗證與隔離完整項仍缺。 |
| G5／G6 | 待主 session／architect 最終核對 | handoff／CHANGELOG 存在；未新開 SPEC-QUESTION，不能以此替代最終核對。 |

**QA verdict：維持 `REVIEW`，退回補驗；不得進 `DONE`。**

## 8. 未決的 SPEC-QUESTION

- [x] 本棒沒有新開 SPEC-QUESTION；v0.2.2 與 ADR-0007 已裁定結果、終態及事件重送權責。
