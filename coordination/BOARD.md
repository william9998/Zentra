# 任務看板

> 只記「還沒做完」的事。做完就刪掉那一列，歷史看 `git log`。
> 小改動不用上板，直接做。狀態：`BACKLOG → IN_PROGRESS → REVIEW（已交付、使用者未驗）→ 刪除`。
> 舊版看板（含逐日裁決紀錄）在 `git show fbea4d5:coordination/BOARD.md`。

## 進行中 / 待驗

| ID | 任務 | 狀態 | 備註 |
|---|---|---|---|
| ZEN-019 | 每個 Agent 自己的設定、機密與對外能力（[gateway.md](../specs/gateway.md)） | `REVIEW` | — |
| ZEN-008 | OCI image 型 Agent：digest 註冊 → validate → REST/MCP | `REVIEW` | 被 ZEN008-ISO-01 擋（見下） |
| ZEN-000 / 002 / 009 / 012 / 013 / 014 / 015 / 018 | 已交付，未正式驗收 | `REVIEW` | 使用者自己用過沒問題就直接刪列 |
| LIVENESS | 常駐容器存活檢查：死掉／卡死 → 自動重啟（10 分鐘內 3 次）→ 放棄後新 Run 一律 `409 AGENT_NOT_RUNNING` 並留 FAILED 紀錄、監控告警；GHOST-POLL；`inflight_runs` 冷啟動計數；逾時清掃改由 60 秒排程（[container-orchestration.md](../specs/container-orchestration.md) §存活檢查） | `REVIEW` | OOMKilled 的死因字串沒實測（測試用 `docker kill`／`docker pause`） |

## 待辦

| ID | 任務 | 備註 |
|---|---|---|
| ZEN-011 | 測試 fixture 自己清理（teardown 或可辨識前綴 + session 結束批次清） | 做完就不用每次收工手動 `make clean-test-data` |
| ZEN-016 | `response_model_exclude_none` 吃掉契約規定的 `null` 欄位（`next_cursor`、`description`、`latest_published_version`） | 先確認 Console 有沒有依賴現行行為 |
| ZEN-017 | serve 模式的驗收補一個會打 `ctx.llm()` 的 agent | bug 已修，缺測試 |
| — | 刪除單一版本（`DELETE /v1/agents/{id}/versions/{semver}`，連 image 一起清） | 做同版號取代時使用者決定先不做；同版號取代已解決大部分 image 堆積 |
| ZEN-003 / 004 / 005 / 006 / 007 | 隔離硬化 · Run API 完整版 · Interface Generator · Console 五畫面 · 收尾 demo | 原八週路線圖剩下的；部分已被後續任務做掉，動工前先對 `specs/` 確認 |

## 已知缺陷（未確認是否已修）

| ID | 缺陷 |
|---|---|
| ZEN008-ISO-01 🔴 | 使用者 image 的 Dockerfile 宣告 `VOLUME` → Docker 建出可寫匿名 volume，`read_only` 擋不住，沙盒銷毀後仍留在宿主機。重現：build 一個含 `VOLUME /escape` 的 image，跑一筆 Run 寫入 `/escape` 後看 `docker volume ls` |
| AGENT-D1 | `agents.name` 無條件 `UNIQUE` + 軟刪除 → 刪掉的名稱永久被佔。ZEN-018 改硬刪後可能已不成立 |
| REDIS-GROWTH | Redis 用量隨流量線性成長、跑完不會降。每筆 Run 留下 `celery-task-meta-*`（每個任務約 376 B，保留 1 天，**平台不讀**；一筆 Run 至少兩個任務）與 `run:*` Stream（約 4.8 KB，保留 1 小時）。壓測 2026-11-01：約 2,900 筆 Run 讓 Redis 從 16 MB 漲到 30 MB。持續每秒 50 筆時估計穩定在約 4 GB，而 `maxmemory` 是 0（沒上限），會吃到主機記憶體。修法方向：`task_ignore_result=True`（或縮短 `result_expires`）、檢討 Stream 的保留時間、設 `maxmemory`。要先改 specs/redis.md §4 |
| SDK-ROUTER-EVENTS | SDK（`image_runtime.py` 的 `astream_events` 迴圈）把**條件分支的判斷函式**也回報成 `node.start` / `node.end`，Run 觀測頁會多出不是節點的「節點」。重現：跑 `probe-agent`，事件裡有 `pick_mode`、`pick_reply`。修法方向：只回報 `metadata.langgraph_node == name` 的事件。冒煙測試目前改成「真節點都在」而不數總數 |
| CANCEL-404 | `POST /v1/runs/{id}/cancel` 沒有後端路由，Console 的「停止」按了會 404（整理規格時發現） |
| MANUAL-STOP-RUNS | 手動停止／重啟常駐容器（`POST …/restart`、`DELETE …/containers/{id}`）時，裡面跑到一半的 Run 不會立即 FAILED，要等逾時清掃收成 `SANDBOX_TIMEOUT`。存活檢查那條路已經會立即收（`SANDBOX_ERROR`），可共用 `_fail_runs` |
| CONTAINER-API-FIELDS | `last_death_reason` / `last_died_at` / `restarts_in_window` 只在 DB，容器 API 和 Console 看不到，排查要下 SQL。要露出得先改 api.md 的容器回應 |
| NO-RESTART-POLICY | `compose.yaml` 五個服務都沒有 `restart:`。Docker／主機重啟後要手動 `make up`（實測 registry 停在 Exited 255）。正式機要決定是否加 `restart: unless-stopped` |
| ZEN-006A-D1 | RUNNING 時「已耗時」可能顯示 `—` 一秒（時鐘偏差）。修法：`Math.max(0, …)` |

## 未決問題（要問使用者）

| # | 問題 |
|---|---|
| Q-002 | 狀態圓點 6px 是否新增 `--dot-size` token（目前借 `--radius-sm`） |
| Q-003 | 子區塊 Empty 態可否省略按鈕 |
| Q-004 | `static/js/dom.js` 保留或併入既有檔 |
| Q-005 | 契約欄位名當**標籤文字**顯示算不算違反 ui-contract §6.2 |
| Q-006 | media query 的 `768px` 字面值斷點可否接受 |
| Q-007 | 自帶字型檔還是接受系統 fallback（目前 fallback） |
| Q-009 | Agent 詳情頁的編輯 / 刪除 dialog 要補進 ui-contract §5（已授權先實作） |

## 刻意不做（不要自己加回來）

依 [overview.md](../specs/overview.md) 的交付條件。發現「順手可以加」→ 記在這裡，不要動手。

| 項目 | 為什麼不做 |
|---|---|
| 平台組裝式 Agent（Console 精靈） | 第二條建立路徑 |
| 知識庫 / Ingestion / Hybrid Search | 知識層移出 MVP |
| Tool Registry / Skill Registry | 工具寫在使用者自己的 Agent 程式碼裡 |
| HITL resume / `WAITING_INPUT` | 狀態機已移除該值 |
| Agent Flow 視覺化編排 | MVP 外 |
| 多租戶 / RLS / Alias 流量切換 | 單租戶 |
| Kubernetes / gVisor / Warm Pool / CVE 掃描 | 單機 + 信任的程式碼 |
| Python bundle + per-version venv 發布 | 已由 OCI image 單一路徑取代 |
| image tar 直傳 | 已撤回；同機 `docker push` 即可 |
| 從遠端 `docker push` 到平台 Registry | 要綁 `0.0.0.0` 就得先解決 TLS 與認證（牽動 P4），要重新設計 |
| 平台代理 / 嵌入 Registry 端點、Console 的 Registry 瀏覽 UI | `api` 橫跨兩個網段，代理會讓沙盒有跳板 |
| 平台內建排程器 / cron | 範例 agent 由外部定時打 REST 觸發；要做需重新設計 |
| React / Vue / Alpine / htmx / bundler / TypeScript / npm | 原生三件套零建置 |
