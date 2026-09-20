# ZEN-000 驗收條件與 QA 狀態

| 欄位 | 值 |
|---|---|
| 依據 | `SPEC.md v0.2.2`、`api-contract.md v0.2.2`、ZEN-000 feature spec、ADR-0007 |
| 範圍 | API → Celery → 一次性沙盒 → 兩節點 LangGraph → Gateway → SSE |
| 狀態 | **預驗收中；未裁定 DONE** |
| 閘門腳本 | [`scripts/verify-zen000.sh`](../../../scripts/verify-zen000.sh)；本輪獨立重跑 27 通過／0 失敗／2 skipped |

先前文件宣稱的 11 筆事件與 `ctx.logger`／`ctx.emit` 已出現在串流，與目前 runtime 不符，**撤回**。本輪修正腳本後，27／0／2 是可重跑的新結果。固定 Agent 只發 `echo_node`、`finalize_node` 各一筆 `node.start` 與 `node.end`，共 **4 筆節點事件**；平台另發 `run.status`。ZEN-000 不要求 `log` 或 `custom`。固定輸入 `{"question":"verify"}` 的預期輸出是 `{"echo":"verify"}`，保留大小寫。

## 可執行的驗收條件

「未跑」表示獨立 QA 尚未取得對應輸出；「部分」只證明列出的窄範圍。整合測試由 `ZENTRA_TEST_BASE_URL`、`ZENTRA_TEST_DB_URL`、`ZENTRA_TEST_REDIS_URL` 顯式啟用；缺少變數而 `skipped` 絕不算通過。所有測試不得依賴外網或 LLM。

| ID | 通過斷言 | 目前 QA 證據 |
|---|---|---|
| `Z0-01` 主線 | POST 固定 Agent `01930000-0000-7000-8000-000000000001` 回 `202`、UUID v7 `run_id`、`QUEUED`；GET 最終 `SUCCEEDED`，input.question 與 output.echo 同為 `hello`，LLM 用量為零且 `cost_usd="0"`。 | 通過：live API 契約測試與端到端腳本。 |
| `Z0-02` A1 冷啟 | image 預建後至少 40 次 Run，量 Docker create/start 到 bootstrap 可用，nearest-rank P95 `<2s`；每次有 create/destroy，終態後 inspect 不存在。 | 通過保守上界：40 次 `POST → 首筆 node.start SSE` P95 1.341s（含 bootstrap 後續步驟），40 個不同容器，40 個終態後 inspect 不存在。 |
| `Z0-03` 一 Run 一盒 | 兩個 Run 的 container ID 不同，各自終態後不存在；以 `zentra.role=sandbox`、`zentra.run_id=<id>` 查殘留。 | 通過：端到端腳本實測兩個不同容器；零殘留以 10 秒有界輪詢。 |
| `Z0-04` A2／P1 | API/worker 不 import、exec、eval、runpy 使用者模組；worker 映像不含 sandbox runtime 與 LangGraph；Agent 只在沙盒進程執行。 | 部分：腳本驗 worker 原始碼 import 與映像無 runtime/LangGraph；未機器覆蓋所有動態執行路徑。 |
| `Z0-05` A3 compile | `build()` 回未 compile `StateGraph`；runtime compile 後 `question="hello"` 得 `echo="hello"`，兩節點各一組 start/end。 | 通過：host runtime 3 tests；live SSE 恰四筆 node events。 |
| `Z0-06` A4 SSE | Gateway 寫 DB／Redis 後，SSE 在終態前逐筆收到節點事件；`id=run_events.seq` 遞增不重複，終局 `run.status: SUCCEEDED` 恰一次。記錄延遲，規格未定數值 SLO。 | 部分：live SSE 順序、唯一 seq、四筆 node events、一次終局已通過；未量事件延遲。 |
| `Z0-07` 持久化 | 全新 PostgreSQL 可 Alembic 升到 head；`runs.owner_id`、`run_events.event_id` 與 `(run_id,event_id)` 唯一約束存在；API 重啟後結果仍可 GET。 | 部分：現有 migrated PostgreSQL 的 DB 整合測試通過；全新庫遷移、API 重啟持久性未跑。 |
| `Z0-08` 邊界 | 缺 input/question、question 非字串、timeout 0／901、非 default entrypoint 回 `422 VALIDATION_ERROR`；錯版本回 `409 AGENT_NOT_PUBLISHED`；未知 Agent 回 `404 NOT_FOUND`；皆不建 Run。 | 部分：live HTTP 錯誤碼／body 測試通過；未查每次錯誤後 DB 無新增 Run。 |
| `Z0-09` Celery 重送 | `acks_late=True`；同 `run_id` 兩個 delivery 競爭時，row lock 內只有一個 claim、至多一個 reserve/create；終態重送不新增容器、事件或改結果。 | 部分：真 PostgreSQL 兩執行緒同時 `_claim_run` 只一個成功；終態補發不新增事件；未雙 Celery task 真投遞。 |
| `Z0-10` 失敗終態 | create/start 失敗、非零退出、零退出但沒有 `/result` 均 `FAILED`，`output=null`、契約錯誤碼、終局事件各一筆、容器銷毀。 | 部分：DB 整合測試驗非零退出、無結果與 runtime error 終態只一筆；真 create/start 故障注入與容器清理未跑。 |
| `Z0-11` 逾時 | `timeout_seconds=1` 阻塞圖被 wall-clock 停止，Run `FAILED`、`error.code=SANDBOX_TIMEOUT`，部分事件保留且容器銷毀。 | 部分：DB 整合測試驗 `timed_out=True` 導致 FAILED/SANDBOX_TIMEOUT 與一次終局；真 wall-clock 停止／清理未跑。 |
| `Z0-12` 部分提交 | DB commit 成功、Redis XADD 失敗後，同 UUID v7 `event_id` 重送得 `accepted=0` 與原 seq；DB 恰一筆，SSE 對該 seq 恰一次。DB commit 失敗不得發不存在的事件。 | 部分：DB 整合測試驗 XADD 注入失敗、同 seq 補發與一筆 DB；終態 pending 事件補發亦通過；未在 live SSE 注入該故障或測 DB commit 失敗。 |
| `Z0-13` 最小 L1 | 沙盒連 Postgres、Redis、`1.1.1.1`、metadata IP 失敗；帶 token 連 `api:8000/internal/runs/{id}/bootstrap` 成功；inspect 只接 internal 網段。 | 部分：真 sandbox 配置下 TCP 隔離四個阻擋、一個 api 可通；Gateway token bootstrap 通過，但未從沙盒內帶有效 token 驗且未 inspect 網段。 |
| `Z0-14` P4 環境 | 沙盒應用注入只有三個 `ZENTRA_*`；無長期 secret/docker.sock；token TTL 等於 Run timeout，終態後撤銷。 | 部分：driver fake 驗三個注入變數，DB 單元驗精確 TTL；Gateway 撤銷後 401；未真容器讀 env/docker.sock。 |
| `Z0-15` token 越權 | bootstrap、events、result 三路由無效／缺 token 為 `401`；A token 打 B Run 為 `403` 且 B 資料不變；終態後 token 為 `401`。主機映射埠同樣需 token。 | 部分：live Gateway 三路由缺 token 401、跨 Run 403 與撤銷後 401；終態自動撤銷未獨立測。 |
| `Z0-16` capabilities | `l2_kernel_isolation is False`；gVisor／核心逃逸及實體隔離列 skipped／未覆蓋，完整 L2–L4／L5 留後續任務。 | 通過誠實性：driver fake + 真容器基礎隔離檢查；gVisor、逃逸明列 2 skipped，不宣稱完整四層。 |
| `Z0-17` 公開契約 | 三個公開端點逐欄位比 `api-contract v0.2.2`；ID UUID v7、UTC、null 欄位、usage/cost 型別正確，無 `WAITING_INPUT`／`tool.*`／`interrupt`／`tier`。 | 部分：live create/get/SSE 的 schema、ID、UTC、usage 與事件型別測試通過；未涵蓋所有狀態與 null 組合。 |
| `Z0-18` Gateway 契約 | bootstrap 回四欄位；events 接頂層陣列、UUID v7 `event_id`，拒沙盒 `run.status`；result 只存資料，沙盒退出前不轉終態；三路由不列公開 OpenAPI。 | 通過已列斷言：live Gateway HTTP＋DB 整合三測。 |
| `Z0-19` G1–G6 | spec-keeper 無未解漂移；契約、單元、核心整合與最小 L1/token 驗證通過；交接、CHANGELOG、版本與 SPEC-QUESTION 齊全。 | **未過**：`coordination/DRIFT.md` 仍記錄 G1 不通過，需 spec-keeper 重新稽核；Z0-10/11/13/14 尚有未覆蓋斷言。 |

## 本輪窄範圍結果與限制

- Compose one-off worker（Python 3.12、已 migration 的 Postgres／Redis／API，測試時在該即棄容器安裝 pytest）：`python -m pytest -q /srv/tests/zen000` **23 passed、1 skipped**。唯一跳過是 runtime 的 LangGraph 測試：worker 映像按 P1 不安裝 LangGraph。包含 live 公開 API、Gateway、DB row-lock 併發、終態冪等與失敗分支。
- `python -m pytest -q tests/zen000/test_runtime_unit.py`：**3 passed**（host Python 3.11.7；LangGraph／MockTransport，驗兩節點事件格式、UUID v7 與 retry payload 穩定）。主機與容器結果分列，不能相加成單一測試環境的通過數。
- `/private/tmp/zentra-backend-venv/bin/python -m pytest -q tests/zen000`：**4 passed、16 skipped**（Python 3.13.4；無 live URL/DB 設定且未裝 LangGraph），僅作為本機可用性紀錄。
- `bash -n scripts/verify-zen000.sh && bash scripts/verify-zen000.sh`：語法及端到端 **27 passed、0 failed、2 skipped**；跳過 gVisor 與容器逃逸依 ADR-0005，非安全通過。零殘留採 10 秒有界輪詢，避免 SSE 終局先於 worker `finally` 的競態。
- Compose one-off worker 執行 `PYTHONPATH=/srv ZENTRA_TEST_BASE_URL=http://api:8000 python /srv/tests/zen000/benchmark_cold_start.py`：**40 次、40 個獨立容器且全部銷毀**；從 POST 前到第一筆 `node.start` SSE 的 nearest-rank P95 **1.341s**、最小 1.266s、中位 1.297s、最大 1.359s。這段時間包含 API、派工、Docker create/start、bootstrap、graph 啟動及事件傳遞，是「create/start 到 bootstrap」的保守上界；低於 2s 因而支持 A1，但不是單獨量到 Docker API 的原始時長。
- 完整 cgroups 壓力、fork bomb、記憶體炸彈、read-only code／venv bind、L5 SDK 控制屬後續任務。gVisor 與實體主機隔離依 ADR-0005 缺席，列 skipped，不能寫「四層 Docker 隔離通過」。

`coordination/DRIFT.md` 的既有稽核指出 Celery 重送競態、終局補發與 token TTL 漂移；本輪聚焦測試已驗 backend-dev 修正，但 spec-keeper 尚未重新裁定 G1。**G1 目前未過，G2 有已列契約綠燈但尚未完成全部失敗／安全斷言；ZEN-000 不進 DONE。**
