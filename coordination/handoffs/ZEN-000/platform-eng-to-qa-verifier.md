# ZEN-000 平台工程交接

**任務**：`ZEN-000` 貫穿骨架  
**從**：@platform-eng　**給**：@qa-verifier  
**日期**：2026-09-21  
**Spec 版本**：`v0.2.2`；API 契約 `v0.2.2`

## 1. 我做了什麼

新增 `SandboxDriver` 非 Docker async 介面與 `DockerSandboxDriver`，由 worker 建立一 Run 一個容器，限制為 compose 的 internal 沙盒網段，僅注入三個規定的 `ZENTRA_*` 環境值；`destroy()` 可重複呼叫。Celery 是同步 task，`SandboxManager` 以同步 adapter 呼叫 async driver，worker 的原呼叫方式仍可用。沙盒 runtime 將內建的兩節點 `StateGraph` 由平台 compile，逐節點以 UUID v7 `event_id` POST 頂層 JSON 事件陣列，最後向 `/result` 回報與輸入完全相同的 `{"echo": question}`；事件短暫失敗會用原 payload 重試。Colima 的 PostgreSQL host bind 初始化因 `chown` 權限錯誤失敗後，compose 已改用 Docker named volume `pgdata` 保存資料。第一次真實 runtime image 曾因舊 fixture 的 `progress` 事件遭 Gateway 422；現已移除非契約事件並用 `uv` 重建 runtime image，端到端 Run 仍待重驗。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `apps/sandbox/drivers/base.py` | `SandboxSpec`、`SandboxHandle`、`DriverCapabilities`、async `SandboxDriver` 契約 |
| `apps/sandbox/drivers/docker_driver.py` | Docker 建盒、退出碼等待與硬逾時、日誌上限、冪等銷毀、能力聲明 |
| `apps/sandbox/drivers/__init__.py` | 匯出平台中立型別 |
| `apps/sandbox/manager.py` | 讓同步 Celery task 經 adapter 呼叫 async driver，不繞過介面 |
| `apps/sandbox/runtime/builtin_agent.py` | ZEN-000 未 compile 的兩節點 LangGraph fixture |
| `apps/sandbox/runtime/__main__.py` | bootstrap → compile → 四筆合法 node 事件 → result；UUID v7 event_id 與固定 payload 重試 |
| `apps/sandbox/runtime/executor.py`、`gateway.py` | 移除未使用的舊路徑，避免 422 被吞掉後仍宣告成功 |
| `docker/api.Dockerfile`、`docker/worker.Dockerfile` | 避免把 sandbox runtime 匯入平台映像 |
| `docker/agent-runtime.Dockerfile` | 改用 `uv pip install --system`，對齊 SPEC 的 `uv only` |
| `compose.yaml` | PostgreSQL 資料改用 Docker named volume `pgdata`，避開 Colima host bind 的 `chown` 錯誤；四服務與網段拓撲不變 |

## 3. 契約影響

- [x] 沒有修改對外契約；實作遵循 `api-contract.md v0.2.2` §4。
- `SandboxSpec` 同時接受 worker 的 `env` 字典與直接呼叫者的 `token`／`gateway` 參數，但兩者不可混用，並會檢查注入值恰好只有 `ZENTRA_RUN_ID`、`ZENTRA_RUN_TOKEN`、`ZENTRA_GATEWAY`。driver 的 `create/wait/logs/destroy/capabilities` 為 async；`SandboxManager.driver` 提供同步 adapter 給現有 Celery worker。`wait` 回 `SandboxExit(exit_code,timed_out,logs)`；逾時先銷毀。`SandboxHandle.id` 為容器 ID，`container_id` 是相容別名。
- runtime 向 `ZENTRA_GATEWAY`（例如 `http://api:8000/internal`）的 `/runs/{run_id}/bootstrap`、`/events`、`/result` 發送 Bearer run token。成功結果包含 `usage.llm_calls/input_tokens/output_tokens=0` 與字串 `cost_usd: "0"`；只發 `node.start`／`node.end`，終局 `run.status` 由 worker 發。

## 4. 下一個人要做什麼

1. 依 `acceptance.md` 的 `Z0-02`、`Z0-03`、`Z0-13`～`Z0-16` 在真正 Docker daemon 上驗證建立、刪除、網段正反斷言、環境與能力聲明；記錄容器 ID 與 Docker events。不能把本交接的 mock 結果視為容器隔離通過。
2. 依 `Z0-05`、`Z0-06`、`Z0-12`、`Z0-18` 驗證 runtime 編譯、四個節點事件、UUID v7、Gateway 部分成功後同 `event_id` 補送、`/result` 與 SSE 順序。
3. 檢查 worker 以 driver 介面使用容器，包含非零退出、逾時、無結果、重送時不建立第二盒，以及終態後撤銷 token。

## 5. 我沒做的事

- ZEN-003 的完整四層 Docker 隔離攻擊測試與 ZEN-002 SDK 應用攔截層沒有在本任務完成；目前不能宣稱「四層隔離通過」。
- 主 session 已確認 Colima compose 與第一次 runtime image 可執行，但該次事件因舊 fixture 發出契約外 `progress` 而得到 422；本次修正後的 image 已重建，尚未跑完整 API→worker→sandbox→SSE。
- ZEN-000 使用的最小 SDK 目錄目前已由 backend-dev 建立，runtime image 的 COPY 前置檔案已存在；SDK 的完整攔截功能仍待 ZEN-002/003。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| 單機 Docker，無 gVisor | `l2_kernel_isolation=false`；無核心層或實體主機隔離 | 只執行信任的程式碼；gVisor／容器逃逸測試列 skipped |
| worker 掛 `docker.sock` | worker 取得等同主機 root 的權限 | 僅 worker 掛載；依 ADR-0005 的 MVP 風險接受條件運作 |
| read-only code／venv bind 尚屬 ZEN-001 | L4 目前只做到 read-only rootfs 與 tmpfs | `capabilities.l4_read_only_filesystem=false`；ZEN-001／003 補齊並驗證 |
| Gateway 與 API 共用 8000 port | 網路來源本身不阻擋公開用戶打 `/internal/*` | backend-dev 必須對每條路由驗 run token；QA 驗無效與跨 Run token |
| QA 腳本的零殘留檢查 | 舊腳本曾查未配置的 label，無法發現殘留；終態 SSE 也可能早於 worker 清理 | driver 現有 `zentra.managed=true`、`zentra.role=sandbox`、`zentra.run_id=<run_id>`；QA 腳本已改查後兩者，仍應有界輪詢清理完成 |
| PostgreSQL 儲存改為 Docker named volume | 舊 `./data/pgdata` 若已有資料不會自動遷入 `pgdata` | 本次原 bind 初始化失敗；正式切換既有資料前須另行備份／遷移，QA 檢查資料保存與容器重建後可讀 |

## 7. 驗證狀態（含隔離分層）

| 檢查 | 結果 | 證據／限制 |
|---|---|---|
| Python 語法與格式 | ✅ | `python -m compileall -q apps/sandbox`、`git diff --check` 退出 0 |
| runtime image `uv` 建置 | ✅ | `docker build -t zentra/agent-runtime:dev -f docker/agent-runtime.Dockerfile .` 在 Colima 退出 0；`uv pip install --system` 安裝 38 套件，image SHA `33bc0d7612c21319ed907289aa6915b9598bfe21f47f6db1a512112265fcb1b5` |
| runtime image 離線 smoke | ✅ | `docker run --rm --network none --entrypoint python zentra/agent-runtime:dev -c '…build().compile().ainvoke({"question":"hello"})…'` 退出 0，輸出 `hello`；此項未接 Gateway |
| compose 拓撲 | 靜態通過 | `docker compose config --quiet` 退出 0；解析 `config --format json` 斷言四服務、internal 網段、僅 api 雙網段且 api 無 socket；仍需 Docker inspect 實測 |
| PostgreSQL named volume 修復 | 待真容器驗證 | 原 `./data/pgdata` bind 在 Colima 啟動時 `chown ... permission denied`；已改 `pgdata:/var/lib/postgresql/data`，compose 靜態設定通過。主 session 待升權重啟並查 `healthy` |
| driver 生命週期 | mock 通過 | 主機未安裝 Docker SDK，原 pytest 列 skipped；以 fake SDK 對同一 `tests/zen000/test_driver_unit.py` 執行得 `3 passed`，涵蓋 `_create`、冪等 async destroy、internal 網段拒絕與能力聲明；非真 daemon |
| 殘留 label 對齊 | 靜態／mock 通過，真 Run 待驗 | fake Docker create kwargs 含 `zentra.managed=true`、`zentra.role=sandbox`、`zentra.run_id=<run_id>`；目前 `scripts/verify-zen000.sh` 以 role 與 run_id 雙 filter 查 `docker ps -aq`，皆與 driver 相符 |
| Celery 同步轉接 | mock 通過 | 以 fake async driver／Redis 驗證 `SandboxManager.create`、`manager.driver.wait/logs/destroy/capabilities` 同步呼叫，且建盒環境僅三個 `ZENTRA_*`；非真 worker |
| runtime／Gateway | 單元通過，整合待重跑 | `python -m pytest -q tests/zen000/test_runtime_unit.py` 得 `3 passed`：四筆合法節點事件、503 後同 ID 重試、UUID v7、echo 原字串與零用量結果。首次真 image 對 `progress` 得 422；修正後 image 已以 uv 重建但尚未重跑 Run |
| L1 網路 | **未實測** | driver 強制檢查 network `Internal=true`，compose 宣告 `zentra-sandbox-net internal: true`；目標封鎖和 api 可達尚需真容器驗證 |
| L2 Docker hardening | **未實測** | driver 設 non-root、cap_drop ALL、no-new-privileges；無 gVisor，`l2_kernel_isolation=false` |
| L3 資源 | **未實測** | driver 設 2g memory/no swap、256 PID、2 CPU、10MB Docker log、wall-clock wait；炸彈測試屬 ZEN-003 |
| L4 檔案 | **未實測且未完整** | read-only rootfs、512m `/workspace` tmpfs 已設定；code／venv 唯讀 bind 尚未出現 |
| L5 應用 | **未實作** | SDK 防自建 client／裸 socket 屬 ZEN-002／003 |
| `capabilities()` | mock 回報已核對 | `l1_internal_network=true`、`l2_kernel_isolation=false`、`l3_resource_limits=true`、`l4_read_only_filesystem=false`、`l5_application_controls=false`；true 是已配置能力，尚不代表攻擊驗證通過 |
| 真 Docker／整合測試 | 首次執行失敗，待重跑 | 主 session 已於 Colima 跑起 compose/runtime image；舊 `progress` 事件回 422。此 subagent 經升權完成修正後 image build；完整 Run/SSE 與容器零殘留仍待 QA／主 session 實測 |

## 8. 未決的 SPEC-QUESTION

- [x] 無。架構師已在 v0.2.2 與 ADR-0007 裁定 `/result`、終態與事件重試；ZEN-003 延期項目是已定範圍，不是未決規格。
