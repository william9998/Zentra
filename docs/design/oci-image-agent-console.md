# ZEN-008 Console：OCI image Agent 註冊與呼叫

| 欄位 | 值 |
|---|---|
| 狀態 | UI 規格完成，待 architect / QA 檢查 |
| 對齊 | SPEC v0.3.1、API contract v0.3.1、UI contract v0.5.1、ADR-0008 |
| 實作 | 原生 HTML + CSS + ES modules，零建置 |

## 1. 設計目標

讓使用者在不接觸 Docker runtime 控制項或長期憑證的前提下完成一條清楚主線：建立 Agent metadata、
註冊已推送到 Registry 的 immutable image digest、等待平台驗證、選擇已發布版本與 entrypoint 建立 Run，
最後進入既有 Run 觀測頁。Console 不直接上傳 image，不增加第六個 route。

## 2. 資訊架構

```text
/console/agents
  ├─ Agent 列表
  └─ 「建立 Agent」dialog
       └─ success → /console/agents/{agent_id}

/console/agents/{agent_id}
  ├─ metadata
  ├─ versions ──「註冊版本」→ /console/agents/{agent_id}/versions/new
  ├─ run composer
  ├─ REST / MCP / OpenAPI endpoints
  └─ recent runs

/console/agents/{agent_id}/versions/new
  └─ semver + canonical image_ref → BUILDING → PUBLISHED / FAILED

/console/runs/{run_id}
  └─ 既有 SSE Run 觀測頁
```

建立 Agent 使用頁內 dialog，因為 API 只要求 `name` 與選填 `description`，沒有足以支撐新頁或精靈的複雜度。
版本註冊保留既有第三頁 route，因為它有不可逆確認、非同步等待與失敗除錯三個獨立階段。

## 3. 主要畫面

### 3.1 Agent 列表

```text
Agents                                             [建立 Agent]
搜尋
──────────────────────────────────────────────────────────────
名稱                  最新已發布版本       更新時間
research-agent        1.2.0                …
draft-agent           尚無已發布版本        …
```

列標題進 Agent 詳情。空清單說明完整下一步：「建立第一個 Agent，接著註冊 OCI image 版本」。建立 dialog
送出成功直接導頁，避免列表上的 transient success toast 成為第二個流程終點。

### 3.2 Agent 詳情

```text
Agents / research-agent                           [註冊版本]

版本
  1.2.0   ● 已發布      0123456789ab   linux/amd64   148 MB
  1.3.0   ● 驗證中      fedcba987654   —             —

執行 Agent
  版本 [1.2.0]  Entrypoint [default]
  Input JSON
  { "question": "..." }
                                              [執行 Agent]

Endpoints
  REST      … [Copy]
  MCP       … [Copy]
  OpenAPI   … [Copy]

最近 Runs
```

版本展開後才顯示長 image ref、完整 digest、runtime contract、entrypoints 或 build error。`BUILDING` 與 `FAILED`
保留在清單中供追蹤，但 Run composer 只列 `PUBLISHED`。最新 PUBLISHED 為預設版本。

### 3.3 註冊版本

```text
Agents / research-agent / 註冊版本

Semver
[1.2.0                                                       ]
同一 Agent 內不可重用。

Canonical image reference
[ghcr.io/acme/research-agent@sha256:…                         ]
請先 push 到 VM 可存取的 Registry；Console 不接收 Registry 密碼。

                                              [註冊並驗證]
```

確認 dialog 必須完整顯示 Agent、semver 與 image ref，並說明不可覆寫。確認後顯示 `BUILDING` 聚合進度：

```text
● 驗證中 · 已耗時 12s
平台正在拉取、檢查並驗證 image。
流程：pull exact digest → inspect runtime → isolated validate
```

API 未提供即時 build stage，UI 不把三步畫成已完成 checklist。失敗時才依 `details.stage` 精確指出
`PULL`、`INSPECT` 或 `VALIDATE`。

## 4. 操作流程

### 4.1 建立並發布

1. `GET /v1/agents` 載入列表。
2. 使用者開 dialog，填 name / description，前端送 `POST /v1/agents`。
3. `201` 後以 `agent_id` 導向詳情；沒有版本時顯示註冊 CTA，呼叫區為 Empty。
4. 使用者填 semver 與 canonical `image_ref`，本地格式檢查後再做不可逆確認。
5. 前端送 JSON `POST /v1/agents/{agent_id}/versions`；`202` 後保存 `agent_version_id` 並每 2 秒讀版本詳情。
6. `PUBLISHED`：停止輪詢、顯示 artifact/entrypoints，回詳情即可呼叫。
7. `FAILED`：停止輪詢、顯示 `BUILD_FAILED`、message、stage 與 version ID。原 semver 不可覆寫；使用者須返回詳情或以新 semver 註冊。

### 4.2 從 Console 建立 Run

1. 取得 Agent 詳情、版本列表，挑出 `PUBLISHED` versions。
2. 預選 `latest_published_version`，讀版本詳情取得 `entrypoints`。
3. 使用者選 entrypoint、查看 input schema、在 textarea 輸入 JSON object。
4. 前端先檢查 JSON parse；送 `{version, entrypoint, input}` 到 `POST /v1/agents/{agent_id}/runs`。
   不送 `trigger` 欄位、`X-Zentra-Trigger` 或其他來源 discriminator；server 固定將 Console 的 REST Run 記為 `API`。
5. `202` 後用 `run_id` 導向 `/console/runs/{run_id}`；既有 Run 頁負責 SSE、cancel 與終態。

前端不傳 `image_ref`。後端以 version 解析 PostgreSQL 中已發布的 exact digest，再走
`q.run → temporary container → Internal Gateway → destroy`。

## 5. UI 與 API 欄位對照

| UI 區域 | API | 使用欄位 |
|---|---|---|
| Agent list | `GET /v1/agents` | `items[].agent_id/name/description/latest_published_version/updated_at`、`next_cursor` |
| Create dialog | `POST /v1/agents` | request `name/description`；response `agent_id` |
| Agent detail | `GET /v1/agents/{id}` | metadata、`latest_published_version`、`endpoints.rest/mcp/openapi` |
| Version list | `GET /v1/agents/{id}/versions` | version summary、status、artifact、error、timestamps |
| Version registration | `POST /v1/agents/{id}/versions` | request `semver/image_ref`；response `agent_version_id/version_id/status/artifact` |
| Version polling/detail | `GET /v1/agents/{id}/versions/{semver}` | `status/artifact/manifest/entrypoints/error` |
| Run composer | `POST /v1/agents/{id}/runs` | request `version/entrypoint/input`；response `run_id/status/events_url` |
| Recent Runs | `GET /v1/runs?agent_id=…` | items、`next_cursor` |

欄位正規化集中在 `api.js`。元件只取得 view model，且所有 API／使用者文字都以 `textContent` 或 DOM node 寫入。
Run `trigger` 是唯讀的 server-known ingress protocol，值域只有 `API` / `MCP`；Console 不製造 `CONSOLE` 值。

## 6. 狀態與錯誤文案

| 條件 | 文案與動作 |
|---|---|
| 沒有版本 | 「尚未註冊版本。先註冊一個 canonical OCI image digest。」→ 註冊版本 |
| 有 BUILDING、無 PUBLISHED | 「版本仍在驗證，完成後即可執行。」→ 查看驗證狀態 |
| `VERSION_CONFLICT` | 「此版本號已存在且不可覆寫。請查看既有版本或改用新的 semver。」 |
| image ref `VALIDATION_ERROR` | 「請使用 `repository@sha256:<64 hex>`；tag-only reference 不接受。」 |
| `BUILD_FAILED` / `PULL` | 「平台無法按此 digest 拉取 image。請確認 Registry 可達與 VM credential。」 |
| `BUILD_FAILED` / `INSPECT` | 「image platform、label 或 runtime executable 不符合契約。請檢查 image metadata。」 |
| `BUILD_FAILED` / `VALIDATE` | 「image 無法在 non-root、唯讀環境通過 runtime 驗證。請查看完整錯誤。」 |
| `AGENT_NOT_PUBLISHED` | 「所選版本尚未發布。請選擇已發布版本。」→ 重新載入版本 |
| `QUOTA_EXCEEDED` | 「目前執行數已達上限。請稍後重試。」；依 `retryable` 顯示重試 |

API 的 `error.message` 必須完整保留；上述文案是下一步提示，不能取代後端訊息或自創 code。

## 7. 無障礙與版面

- label 與 input 以 `for` / `id` 關聯；欄位錯誤用 `aria-describedby`，送出失敗後 focus 第一個錯誤欄。
- dialog 使用原生 `<dialog>`，開啟時 focus name 或確認標題，關閉後 focus 回觸發按鈕。
- `BUILDING` 的狀態文案放 `aria-live="polite"`；2 秒輪詢只在狀態或訊息改變時更新可讀文字，避免重複播報。
- 長 image ref 與 digest 可水平捲動或換行，不造成頁面水平捲動；畫面可短顯示，Copy 永遠使用完整值。
- 表單使用 `--reading-max`，列表使用 `--content-max`；間距、圓角、字體、控制項高度只引用 UI contract token。
- `prefers-reduced-motion` 下 BUILDING 圓點不脈動，文字仍完整表達狀態。

## 8. 非目標

沒有 image tar 上傳、Registry credential 表單、Dockerfile editor、build log terminal、container command/port/network/
volume/user 設定、CVE 掃描或 Agent 組裝精靈。這些能力不應以 disabled 控制項出現在畫面中，以免暗示即將支援。
