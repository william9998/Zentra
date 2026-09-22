# 看板封存 (Board Archive)

> 從 [`BOARD.md`](BOARD.md) 移出的歷史段落。**一般開工不需要讀這份**——
> 只有在考古舊編號、或想確認某件事為什麼被判定不做的時候才打開。
> 現行任務、SPEC-QUESTION、進行中狀態一律看 `BOARD.md`。

## 已作廢的舊編號

v0.1 的編號在降維後重排。**舊交接單或註解提到這些編號時，對照這張表**：

| 舊 ID | 舊任務 | 現況 |
|---|---|---|
| ZEN-001 | 純骨架 | 已於 v0.1 併入 ZEN-000 |
| ZEN-002 | Manifest schema 與 Registry 資料模型 | → 併入新的 ZEN-001 |
| ZEN-003 | 沙盒五層隔離（K8s driver + gVisor） | → 新的 ZEN-003，降為四層 Docker 隔離 |
| ZEN-004 | Celery 多佇列 tier + 死信 + Beat | 作廢（兩條佇列，無 Beat） |
| ZEN-005 | Zentra SDK v1 | → 新的 ZEN-002 |
| ZEN-006 | Run API 完整版 | → 新的 ZEN-004 |
| ZEN-007 | Interface Generator | → 新的 ZEN-005 |
| v0.1 ZEN-008 | Console Run 觀測頁 | → 併入新的 ZEN-006；與目前 v0.3 的 ZEN-008 無關 |
| ZEN-009 | 知識庫 Ingestion + Hybrid Search | 作廢（MVP 外） |
| ZEN-010 | 沙盒安全驗證套件（CI 上跑） | → 併入新的 ZEN-003 與 ZEN-007 |
| ZEN-011 | Build pipeline（CVE / License 掃描） | 作廢；發布流程併入新的 ZEN-001，不含掃描 |
| v0.2 ZEN-001 | bundle → uv venv → validation sandbox | v0.3 由 ZEN-008 OCI image digest registration 取代，未曾進入實作 |

## 刻意不做（不要自己加回來）

依 [ADR-0005](../specs/adr/ADR-0005-mvp-single-host-scope.md) 與 [SPEC §11](../specs/SPEC.md)，
下列項目**不在八週內做**。發現「順手可以加」→ 記在這裡，不要動手（鐵則 6）。

| 項目 | 為什麼不做 |
|---|---|
| 平台組裝式 Agent（Console 精靈） | 第二條建立路徑，八週做不完 |
| 知識庫 / Ingestion / Hybrid Search | 整個知識層移出 MVP |
| Tool Registry / Skill Registry | 使用者可在自己的 Agent 程式碼裡寫工具 |
| HITL resume / `WAITING_INPUT` | 狀態機已移除該值 |
| Agent Flow 視覺化編排 | 原本就是 MVP 外 |
| 多租戶 / RLS / Alias 流量切換 | 單租戶 |
| Kubernetes / gVisor / Warm Pool / CVE 掃描 | 單機 + 信任的程式碼 |
| Python bundle + per-version venv 發布 | ZEN-001 尚未實作即由 ADR-0008 的 OCI image 單一路徑取代；恢復需新 ADR 與雙路徑驗收 |
| React / Vue / Alpine / htmx / bundler / TypeScript / npm | 前端是原生三件套，零建置（[ADR-0006](../specs/adr/ADR-0006-vanilla-frontend.md)） |
