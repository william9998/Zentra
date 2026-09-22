# ZEN-008：OCI image 型 Agent 註冊、呼叫與一次性執行

| 欄位 | 值 |
|---|---|
| 狀態 | `SPEC_READY`（SPEC/API v0.3.1、UI v0.5.1、開發前 acceptance 已對齊） |
| 擁有者 | @architect → @ui-designer + @qa-verifier → @backend-dev + @platform-eng + @frontend-dev |
| 依賴 | ZEN-000；實作整合需 ZEN-003、ZEN-004、ZEN-005、ZEN-006 對應能力 |
| 依據 | SPEC v0.3.1、API contract v0.3.1、ADR-0008 |

## 1. 目標

讓開發者把自己 build 的 OCI/Docker image 註冊為不可變 Agent 版本。版本通過隔離驗證後，
Console、REST 與 MCP 都能呼叫它；每次呼叫仍建立一個 Run、送入 Celery `q.run`、
建立一個臨時容器，完成／失敗／逾時／取消後撤銷 token 並銷毀容器。

完成後的使用路徑：

```text
docker build → push OCI Registry → 以 digest 註冊版本 → q.build pull/validate → PUBLISHED
  → Console / REST / MCP 建立 Run → q.run → 一 Run 一盒 → Gateway 回寫 → destroy
```

## 2. 範圍界線

### 做什麼

- 建立 Agent metadata 與 PostgreSQL Agent Registry。
- 以 `repository@sha256:...` 註冊 OCI image 版本。
- 非同步 pull、inspect、runtime contract 驗證、entrypoints schema 抽取與 `PUBLISHED`。
- Run 時使用發布時固定的 digest 建立沙盒；沿用 ZEN-000 的 Celery、Internal Gateway、事件、結果與銷毀流程。
- Console 可建立 Agent、註冊 image 版本、查看狀態、選 Agent/版本/entrypoint 並發起 Run。
- 外部系統以既有 REST 與 MCP 雙門面呼叫。

### 明確不做

- 不接收 `docker save` tar／OCI archive，不把 image blob 存 PostgreSQL 或本機 artifact volume。
- 不在 Zentra compose 內架 OCI Registry，不代替 Docker Hub、GHCR 或企業 Registry。
- 不接受 tag-only reference；執行時不解析 `latest` 或其他可變 tag。
- 不替使用者 build Dockerfile，不做遠端 build service。
- 不讓使用者自訂 container command、port、network、volume、user、privileged、capabilities 或 Docker socket。
- 不做 image 簽章、SBOM、CVE／license 掃描；MVP 仍只跑自己信任的 image。
- 不保留 Python bundle + per-version venv 發布路徑；ADR-0008 已裁定由 OCI image 取代。

## 3. 分層與核心立場

| 項目 | 影響 |
|---|---|
| L0 接入 + 控制 | Agent/Version REST、MCP schema、Console、Registry metadata、Internal validation endpoint |
| L1 派工 | `q.build` pull + validate；`q.run` 一 Run 一 task |
| L2 執行 | validation container 與 Run sandbox 都使用已 pin 的 image digest |
| L3 資料 | PostgreSQL metadata、Redis broker/events、Docker daemon local image cache |
| P1 | API/worker 不 import/exec image 內程式碼；validate/run 都只在一次性容器內執行 |
| P2 | image 必須實作 `zentra.image/v1`，由 runtime 取得未 compile `StateGraph` 並由平台 compile |
| P3 | validation 抽出的同一份 entrypoints 同時生成 REST/OpenAPI 與 MCP tools |
| P4 | Registry/LLM 長期憑證不進 image container；只注入 validation token 或 run token |
| P5 | 發布與 Run 都使用完整 OCI digest；同一 semver 不可重註冊 |

## 4. 註冊與發布流程

1. 使用者自行 build image，且 image 符合 §6 runtime contract。
2. 使用者 push 到 VM 能存取的 OCI Registry，取得 canonical digest reference。
3. `POST /v1/agents` 建立 Agent metadata；PostgreSQL 新增 `agents` row。
4. `POST /v1/agents/{id}/versions` 送 `semver` 與 `image_ref`；API 只接受 digest reference。
5. API 在同一 transaction 建立 `agent_versions(status=BUILDING)`，提交後送 `q.build(agent_version_id)`；
   回 `202`。同一 `agent_id + semver` 唯一，HTTP 重送不得建立第二個版本。
6. build-worker 以 VM 預先配置的 Registry credential 拉 exact digest，inspect 並驗：
   - digest 與 request 完全相同
   - platform 與 VM host 相容
   - label `io.zentra.image-contract=zentra.image/v1`
   - 固定 runtime executable 存在，且可用 `65534:65534` + read-only rootfs 啟動
7. worker 核發綁定 `agent_version_id`、TTL ≤ 10 分鐘的 validation token，建立一次性 validation container。
8. validation runtime import 使用者模組、取得未 compile `StateGraph`、驗證 manifest 與 JSON Schema，
   再向 `/internal/versions/{agent_version_id}/validation` 回報。worker 本身不 import image 內容。
9. 成功時保存 manifest/entrypoints/image metadata，原子轉成 `PUBLISHED`；失敗時轉 `FAILED`，
   `error.code=BUILD_FAILED` 並在 `details.stage` 指出 `PULL`、`INSPECT` 或 `VALIDATE`。
10. 不論成功失敗都撤銷 validation token 並銷毀 validation container。`PUBLISHED` 版本不可修改。

`q.build` 必須以 `agent_version_id` 冪等：終態重送只回傳既有結果；`BUILDING` 的競爭 delivery 只能有一個取得 claim。

## 5. PostgreSQL 資料模型

### `agents`

| 欄位 | 型別／約束 | 說明 |
|---|---|---|
| `id` | UUID v7 PK | `agent_id` |
| `owner_id` | UUID nullable | 單租戶先留欄位，不做 RLS |
| `name` | text unique | 顯示名稱與穩定識別 |
| `description` | text nullable | metadata，可 PATCH |
| `created_at` / `updated_at` / `deleted_at` | timestamptz | soft delete |

### `agent_versions`

| 欄位 | 型別／約束 | 說明 |
|---|---|---|
| `id` | UUID v7 PK | `agent_version_id`，Run FK 指向此欄 |
| `agent_id` | FK agents | 與 `semver` 組唯一 |
| `semver` | text | 人類版號，發布後不可改 |
| `source_type` | enum，固定 `OCI_IMAGE` | 未來恢復別的來源需新 ADR；現在不得出現第二值 |
| `image_ref` | text unique | canonical `repository@sha256:...`；不得含可變 tag |
| `image_digest` | char(71) | 完整 `sha256:` + 64 hex；P5 的內容身份 |
| `version_id` | char(12) | digest hex 前 12 碼，只供短顯示，不取代完整 digest 唯一性 |
| `image_platform` | text | 例 `linux/amd64` |
| `image_size_bytes` | bigint nullable | inspect 可取得時保存 |
| `runtime_contract` | text | 固定 `zentra.image/v1` |
| `manifest` / `entrypoints` | jsonb | validation container 回報後保存；雙門面的單一來源 |
| `status` | enum | `BUILDING` / `PUBLISHED` / `FAILED` |
| `error` | jsonb nullable | 統一 error 形狀，失敗時保存 |
| `created_at` / `published_at` | timestamptz | 稽核時間 |

不得建立 `image_blob`、Registry password/token 欄位。私有 Registry 認證由平台部署設定管理。

## 6. `zentra.image/v1` container runtime contract

### Image 必須提供

- OCI label：`io.zentra.image-contract=zentra.image/v1`。
- executable：`/opt/zentra/bin/zentra-agent`。
- 兩個命令：
  - `/opt/zentra/bin/zentra-agent validate`
  - `/opt/zentra/bin/zentra-agent run`
- Python 3.12、相容的 `zentra_sdk` / LangGraph runtime 與 Agent 所有依賴都已在 image 內。
- 能在無 HOME、uid/gid `65534:65534`、read-only rootfs、`/workspace` tmpfs 的條件下執行。

### 平台控制的啟動設定

- 平台覆寫 image 的 ENTRYPOINT/CMD，固定呼叫上述 executable；image 宣告的 port、healthcheck、user、volume 不具控制權。
- `validate` 注入：`ZENTRA_GATEWAY_URL`、`ZENTRA_AGENT_VERSION_ID`、`ZENTRA_VALIDATION_TOKEN`。
- `run` 注入：`ZENTRA_GATEWAY_URL`、`ZENTRA_RUN_ID`、`ZENTRA_RUN_TOKEN`。
- 不注入 Registry credential、LLM key、PostgreSQL/Redis URL、Docker socket 或其他長期 secret。
- `validate` / `run` 都接 `zentra-sandbox-net`，無外網與資料層路由，沿用 SPEC §6.2 全部限制。

### `run` 行為

1. `GET /internal/runs/{run_id}/bootstrap` 取得 manifest、entrypoint、input。
2. import Agent `build()`，取得未 compile `StateGraph`，由 Zentra runtime compile 並注入事件 hooks。
3. events/result 依 API contract §4 回寫；不得直接寫 Redis/PostgreSQL。
4. result 被 Gateway 接受後正常退出；退出碼只供 worker 判斷，不能自行宣告 Run 終態。

容器內不開 HTTP server。REST/MCP 是平台 `api` 的門面，不是直連 image port。

## 7. Run 與呼叫流程

### Console

1. `/console/agents` 以 `GET /v1/agents` 顯示可選 Agent。
2. Agent 詳情顯示 `PUBLISHED` 版本、image digest、entrypoints 與 REST/MCP/OpenAPI 位址。
3. 使用者選版本（預設最新 PUBLISHED）、entrypoint，輸入 JSON；前端先依 schema 提示，後端仍做權威驗證。
4. 送 `POST /v1/agents/{agent_id}/runs`，取得 `run_id` 後導到 `/console/runs/{run_id}`，沿用 SSE 觀測。

Console 與外部 REST 使用同一個 HTTP ingress，兩者建立的 Run 都是 `trigger=API`。
Console 不送 `trigger` 欄位或來源 header；同源不是可信 caller discriminator。

### REST

```http
POST /v1/agents/{agent_id}/runs
Content-Type: application/json

{"version":"1.2.0","entrypoint":"default","input":{"question":"..."}}
```

API 只從 `agent_versions` 解析已發布 digest；呼叫端不能傳 `image_ref`。
REST router 在伺服器內固定寫入 `trigger=API`；request 夾帶 `trigger` 為 `422 VALIDATION_ERROR`。

### MCP

連線 `/mcp/agents/{agent_id}`。`tools/list` 來自同一份 entrypoints；`tools/call` 建立同一種 Run。
同步模式最多等 60 秒，`_async: true` 回 `run_id`。
FastMCP handler 在伺服器內固定寫入 `trigger=MCP`。

### 共同底層

`REST / MCP / Console → api 建 Run → Redis/Celery q.run → runner-worker → Sandbox Manager`
`→ create(image_ref@digest) → Internal Gateway → worker 定終態 → revoke token → destroy container`

一次 Run 仍只有一個 Celery task 和一個 sandbox；LangGraph node 不拆成 Celery task。

## 8. API 契約變更

- `POST /v1/agents/{id}/versions` 從 multipart bundle 改為 JSON image registration。
- 版本回應新增 `source_type` 與 `artifact`；`artifact.digest` 是完整 OCI digest。
- `GET Agent` / `GET Versions` 必須提供 Console 選擇與輪詢所需欄位。
- Run、SSE、MCP 與公開錯誤 envelope 維持既有形狀；錯誤枚舉新增 `VERSION_CONFLICT`。
- `trigger` 值域收斂為 `API | MCP`：Console 經 REST 建立時為 `API`；不接受 client discriminator。
- `/internal/versions/{id}/validation` 明定 validation token 與 body。
- 完整欄位以 `specs/api-contract.md v0.3.1` 為準。

## 9. 驗收條件前置需求（交 @qa-verifier）

`acceptance.md` 必須在開發前完成，至少把下列項目變成可執行斷言：

- canonical digest：tag-only、錯 digest、非 sha256 全拒絕；tag 被改後已發布版本仍執行原 digest。
- DB 無 image blob 與 Registry credential；API 不回傳 credential。
- `q.build` 競爭 delivery、失敗重送與終態重送冪等；validation container 必定銷毀。
- pull/inspect/validate 三階段失敗均為 `FAILED + BUILD_FAILED`，`details.stage` 正確。
- runtime contract：label/path/platform/uid/read-only/tmpfs 缺一即發布失敗。
- P1：API/worker 不 import 或 exec image 程式碼；所有 validate/run 均在容器內。
- P4：validation/run 容器只有規定的環境變數；無 Docker socket、Registry/LLM/DB/Redis secret。
- 一 Run 一盒：兩個 Run container ID 不同，終態後均不存在；使用的 RepoDigest 等於 DB digest。
- REST、MCP、Console 三者建立的 Run 都走 `q.run`，且 entrypoint schema 語意等價。
- REST 與 Console 的 Run 都斷言 `trigger=API`，MCP 為 `trigger=MCP`；body 帶 `trigger` 必須拒絕，header 不得改寫。
- 事件冪等、worker 唯一終態、逾時/取消/崩潰清理沿用 ZEN-000/004 回歸。
- image cache 缺失與 Registry 暫時失敗時不得偷用 tag 或其他本地 image。
- 四層隔離與 `capabilities()` 誠實性完整回歸；gVisor 仍為 skipped，不得宣稱 passed。

## 10. 分工與交付順序

1. @ui-designer 更新 `specs/ui-contract.md`：version-new image 註冊、Agent 詳情 invoke flow 與五態。
2. @qa-verifier 先寫 `specs/features/ZEN-008/acceptance.md`。
3. @architect 檢查 UI/API/acceptance 無衝突後，才把任務推進 `SPEC_READY`。
4. 可平行：
   - @backend-dev：models/migration、Agents/Versions/Run registry、validation gateway、MCP schema。
   - @platform-eng：pull/inspect、OCI SandboxSpec、validation/run container、registry credential boundary。
   - @frontend-dev：只能在 ui-contract 完成後實作 Console。
5. 整合後 @qa-verifier 驗收，再交 @spec-keeper 做 drift audit；只有 QA 可標 `DONE`。

## 11. 風險與未決問題

| 風險 | 影響 | 對策 |
|---|---|---|
| image 過大 | 發布慢、VM 磁碟壓力 | 顯示 size；正式上限由 QA 壓測後寫入部署設定，不在呼叫時 pull |
| VM 被人工 `docker image prune` | PUBLISHED 版本本地 cache 消失 | Run 僅可按相同 digest 重新拉取；不可 fallback 到 tag；Registry 不通則 `SANDBOX_ERROR` |
| image 不是為 non-root/read-only 設計 | validate 失敗 | 官方 base image 與範例 Dockerfile留給後續文件；不放寬安全設定 |
| 私有 Registry 憑證管理 | pull 失敗或秘密外洩 | 平台側 compose secret / Docker credential store；永不進 DB 或沙盒 |
| 無供應鏈掃描 | 信任的 image 仍可能有漏洞 | 誠實標示；跑第三方 image 前重新檢討 ADR-0001/0005 |

**未決 SPEC-QUESTION：無。** UI 細節與驗收斷言已有明確 owner，不代表可在未交付前跳過。

## 12. 完成的定義

同一個 canonical image digest 能被註冊並發布；Console、REST 與 MCP 各自觸發一次後，三次 Run 都能證明：
使用 DB 記錄的相同 digest、經 Celery 建立不同的臨時容器、事件與結果可查、終態後 container 不存在；
PostgreSQL 沒有 image blob，沙盒也沒有 Registry 或其他長期憑證。
