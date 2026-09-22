# ZEN-009：在 Console 上傳 docker image 建立 Agent 版本

| 欄位 | 值 |
|---|---|
| 狀態 | `SPEC_READY`（待 @ui-designer 的 `ui-contract` v0.6.0 與 @qa-verifier 的 `acceptance.md`；見 §7 閘門） |
| 擁有者 | @architect → @ui-designer + @qa-verifier → @platform-eng + @backend-dev + @frontend-dev |
| 對齊 Spec | `specs/SPEC.md v0.4.0`、`specs/api-contract.md v0.4.0` |
| 依據 | [ADR-0009](../../adr/ADR-0009-internal-registry-image-upload.md)（推翻 ADR-0008 對選項 B 的否決） |
| 依賴 | ZEN-008（digest 註冊 → pull → validate → PUBLISHED → run 已可運作） |
| 涉及分層 | L0 接入+控制、L1 派工、L3 資料（新增內部 Registry）。**L2 執行零改動** |

## 1. 要解決什麼問題

使用者手上已經有 `docker build` 出來的 image，卻必須先去申請 GHCR 或自己架一個 Registry，
才能把它變成 Agent 版本。對「單機、內部工具、一個人開發」而言，這個前置條件是最大的採用阻力。
本任務讓使用者在 Console 直接上傳 `docker save` 產生的 tar，其餘流程與現行完全相同。

## 2. 範圍

### 做

- compose 新增第 5 個長駐服務：內部 OCI Registry（`registry:2`），只掛 `zentra-net`，只發布 `127.0.0.1:5000`。
- `POST /v1/agents/{id}/versions` 同時接受 `multipart/form-data`（tar 上傳）與既有的 `application/json`（digest 註冊）。
- `api` 串流落檔到共享 volume；`worker` 在 `q.build` 新增 `LOAD` 與 `PUSH` 兩個 stage，
  push 後以 registry 回的 **manifest digest** 當版本身份，然後接既有的 `INSPECT → VALIDATE → PUBLISHED`。
- `agent_versions` 新增 `image_origin`，並讓 `image_ref` / `image_digest` / `version_id` 在 `BUILDING` 期間可為 `NULL`，
  同時用 DB CHECK 保證 `PUBLISHED` 一定有完整 digest。
- Console 的版本註冊頁新增「上傳 image」模式（含上傳進度、`BUILDING` 期間 digest 未知的呈現）。
- 官方 base image `zentra/agent-base:1.0`（Q-010 裁決）與 `build()` 簽章、SDK 依賴的契約收斂（Q-008 / Q-009）。

### **明確不做**（同等重要）

- **不做 Registry 的 TLS、認證、帳號、token。** 理由見 ADR-0009；唯一的寫入者是平台自己。
- **不做自動 GC、不做配額系統、不做自動備份。** `delete.enabled=false`，手動 GC 寫進維運說明。
- **不把 Registry 接上 `zentra-sandbox-net`。** 一行都不准（P4 / SPEC §5 硬規則 3）。
- **不對外發布 Registry port**（不得綁 `0.0.0.0`），不讓使用者直接 `docker push` 到它。
- **不做斷點續傳、分塊上傳、上傳暫停／恢復、背景上傳佇列。** 中斷就重來。
- **不做 Dockerfile 上傳、遠端 build、從 URL 拉 tar、docker-compose 匯入。** 第三條路徑需要新 ADR。
- **不做版本刪除、image 刪除、Registry 瀏覽 UI。**
- **不在 `api` 掛 docker.sock。** 一個字都不改現行規則。
- **不用 Python `tarfile` 解析使用者上傳的 archive**（見 §5.3）。
- **不改 `version_id` 語意、不新增 Run 狀態、不新增錯誤 code。**
- **不改沙盒**：L2 的四層隔離、SandboxSpec、run token 全部零改動。
- 不把 base image 發佈到公開 Registry、不做簽章、不做多架構 build。

## 3. 對核心立場的影響

| 立場 | 有無觸碰 | 說明 |
|---|---|---|
| P1 使用者程式碼不在平台行程執行 | **有，且必須擋住** | `docker load` 由 daemon 執行，不是平台行程；`api`/`worker` 只串流 bytes，**不得解析 tar 內容**。取得 image 資訊一律 load 完問 daemon。validate 仍在一次性容器內。 |
| P2 框架統一 LangChain/LangGraph | 無 | 上傳的仍必須是 `zentra.image/v1` image。 |
| P3 API + MCP 雙門面 | 無 | 匯流點之後零改動。 |
| P4 沙盒無長期憑證 | **有，且必須擋住** | Registry 不接沙盒網段；沙盒不注入 Registry 位址或憑證；內部 Registry 本來就沒有憑證可外洩。 |
| P5 版本不可變 | **有，且被強化** | `version_id` 仍是 manifest digest 前 12 碼。新增不變式：`RepoDigests` 非空 + DB CHECK，**沒有可重新 pull 的來源就不得 `PUBLISHED`**。 |

## 4. 契約變更

- [x] `specs/api-contract.md` → **v0.4.0**（已由 @architect 更新）：
  `POST /v1/agents/{id}/versions` 新增 multipart 形狀；`artifact.origin`；
  `version_id`/`artifact.image_ref`/`artifact.digest` 在 `BUILDING` 可為 `null`；
  `details.stage` 值域加 `LOAD`/`PUSH`；`QUOTA_EXCEEDED` 說明放寬。**沒有新增錯誤 code。**
- [x] `specs/SPEC.md` → **v0.4.0**（已由 @architect 更新）：§5 / §6 / §6.1 / §7.1 / §8.1 / §10 / §11。
- [ ] `specs/ui-contract.md` → **@ui-designer 負責 bump 到 v0.6.0**。
  現行 §5.4 `version-registration.js` 明文寫「這是文字表單，**沒有檔案輸入、拖放區、upload progress**」，
  ZEN-009 與它直接衝突，**必須由 owner 改，不是實作時順手改**。需要涵蓋：
  兩種模式的切換、檔案選擇與（可選的）拖放、上傳進度與取消、
  `BUILDING` 期間 digest／`version_id` 為 `null` 的呈現、上傳失敗與 `BUILD_FAILED stage=LOAD|PUSH` 的文案、
  以及「上傳 vs 外部 Registry 各適合什麼」的一行說明。
  **實作允許**：上傳進度必須用原生 `XMLHttpRequest`（`fetch` 沒有 upload progress 事件）。
  這不是引入函式庫，符合 ADR-0006，@frontend-dev 不需要為此開 SPEC-QUESTION。

## 5. 設計要點

### 5.1 拓撲

```
compose（長駐 5 個）
  postgres  zentra-net
  redis     zentra-net
  registry  zentra-net                     ← 新增；ports: ["127.0.0.1:5000:5000"]
  api       zentra-net + zentra-sandbox-net
  worker    zentra-net + /var/run/docker.sock

volumes: pgdata, registrydata, uploads
```

- `registry` **只掛 `zentra-net`**。接上 `zentra-sandbox-net` 是違反 P4 的 bug，不是設定選項。
- **為什麼一定要發布 loopback port**：push／pull 的執行者是主機上的 Docker daemon，
  它不在 `zentra-net` 上，解析不到服務名 `registry`。daemon 唯一能用的名字是主機 loopback。
  `localhost:5000` 另外免去改 `daemon.json` 的 `insecure-registries`（Docker 對 localhost 預設允許 HTTP）。
- `uploads` volume 由 `api`（讀寫）與 `worker`（讀寫，含刪除）共掛於 `/var/lib/zentra/uploads`。
- 設定項（平台端，**絕不接受 client 提供**）：
  `ZENTRA_INTERNAL_REGISTRY`（預設 `localhost:5000`）、
  `ZENTRA_UPLOAD_DIR`（預設 `/var/lib/zentra/uploads`）、
  `ZENTRA_MAX_IMAGE_UPLOAD_BYTES`（預設 `2147483648`）。

### 5.2 上傳（L0，`api`）

1. 先查 `agent_id` 存在且未軟刪除、`semver` 未被使用 → 不符直接 `404` / `409 VERSION_CONFLICT`。
   **在讀 body 之前做**，不要收完 216 MB 才說版本號重複。
2. 磁碟預檢：`shutil.disk_usage(ZENTRA_UPLOAD_DIR)` 的可用空間 < `2 × Content-Length + 1 GiB`
   → `429 QUOTA_EXCEEDED` + `Retry-After: 60` + `details.reason="insufficient_disk"`。
3. **串流**寫入 `${ZENTRA_UPLOAD_DIR}/<agent_version_id>.tar`，mode `0600`。
   **不得 `await file.read()` 一次讀進記憶體。** 累計位元組超過上限 → 立刻中止、刪半檔、
   `422 VALIDATION_ERROR`（`details.field="file"`、`reason="too_large"`、`limit_bytes`）。
4. 落檔成功後才 `INSERT agent_versions(status=BUILDING, image_origin=UPLOADED,
   image_ref=NULL, image_digest=NULL, version_id=NULL)`，commit 後送 `q.build`。
   DB insert 或 enqueue 失敗 → 刪檔並回錯，**不留孤兒檔**。
5. 回 `202`，shape 與 JSON 路徑相同（digest 三欄為 `null`）。
6. 路徑由 `agent_version_id` 決定性推導；**不接受 client 的檔名或路徑**，`filename` 只當 log 用。
7. 逾時：不設 request 總時限；單次 socket 讀取無資料超過 300 秒視為中斷 → 刪檔、不建立版本。

### 5.3 建置（L1，`worker` / `q.build`）

新增兩個 stage，插在既有流程之前：

```
UPLOADED :  LOAD → PUSH → INSPECT → VALIDATE → PUBLISHED
REGISTERED:              PULL → INSPECT → VALIDATE → PUBLISHED   （ZEN-008，不變）
```

- **LOAD**：以 stream 把 tar 交給 daemon（`client.images.load(data)`）。
  必須恰好得到 **1 個** image，0 或 >1 皆 `BUILD_FAILED stage=LOAD`。
  **`worker` 不得自行解壓或解析 tar**（不用 `tarfile`、不讀 `manifest.json`）——
  那會把使用者提供的 archive 搬進平台行程解析，違反 P1 的精神。要資訊就 load 完 `inspect`。
- **PUSH**：tag 成 `${ZENTRA_INTERNAL_REGISTRY}/zentra/<agent_id>:<semver>` → push。
  push 完 **必須**以 `inspect` 確認 `RepoDigests` 含有 `<repo>@sha256:<digest>`；
  缺少就 `BUILD_FAILED stage=PUSH`，訊息點明「registry 未回報 manifest digest」。
  **這一條就是那個實測結果的不變式版本，不准省略。**
- 取得 digest 後於同一 transaction 寫入 `image_ref` / `image_digest` / `version_id`，commit，
  **然後刪 tar**。
- **INSPECT 之後與 ZEN-008 共用同一段程式碼**，不得複製第二份。
- 冪等：重送時若 `image_digest` 已存在 → **跳過 LOAD/PUSH**，從 INSPECT 續跑（tar 已刪也無妨）。
  若 `image_digest` 為 `NULL` 且 tar 不存在（worker 在 push 前死掉、claim lease 到期後重送）
  → `BUILD_FAILED stage=LOAD`，訊息要明說「上傳檔已不存在，請以新的 semver 重新上傳」。
  **不得 hang、不得靜默成功。**
- 終態失敗一律在 `finally` 刪 tar。

### 5.4 資料模型（`agent_versions` 增量）

| 欄位 | 變更 | 說明 |
|---|---|---|
| `image_origin` | **新增** `String(16) NOT NULL DEFAULT 'REGISTERED'` | `REGISTERED` \| `UPLOADED`，加 CHECK 限制兩值 |
| `image_ref` | `NOT NULL` → **nullable** | `UPLOADED` 在 push 成功前為 `NULL`；unique 約束保留（Postgres 多個 NULL 不衝突） |
| `image_digest` | `NOT NULL` → **nullable** | 同上；既有 regex CHECK 對 NULL 自動通過，不需改 |
| `version_id` | `NOT NULL` → **nullable** | 同上 |
| — | **新增 CHECK** | `status <> 'PUBLISHED' OR (image_ref IS NOT NULL AND image_digest IS NOT NULL AND version_id IS NOT NULL)` |
| — | **新增 CHECK** | `image_origin <> 'REGISTERED' OR image_ref IS NOT NULL` |

`source_type` 維持固定 `OCI_IMAGE` 與既有 CHECK。**不得新增 artifact 型別。**
migration 必須對既有列回填 `image_origin='REGISTERED'`。

### 5.5 兩條路徑並存的規則（實作必守）

1. 匯流點 = `agent_versions.image_ref` 拿到 canonical digest reference。**之後只有一條程式碼路徑。**
2. `image_origin` / `artifact.origin` 是**純描述性**：
   `INSPECT`、`VALIDATE`、`PUBLISHED`、Run、SandboxSpec、MCP、Console 觀測
   **一律不得依它分支**。QA 必須有一條測試釘死這件事。
3. 外部 Registry 註冊仍是正途，不降級、不隱藏。Console 兩種模式對等呈現。
4. 兩條路共用 `semver` 命名空間與 `409 VERSION_CONFLICT`。
5. 沙盒對兩條路徑完全一致：沒有 Registry 位址、沒有憑證、連不到 `registry`。

### 5.6 image 契約收斂（Q-008 / Q-009 / Q-010 的落地）

- **Q-008**：契約明訂 `def build(ctx: ZentraContext) -> StateGraph`，必須接受且只接受一個位置參數。
  `validate` mode 在呼叫前先用 `inspect.signature` 檢查 arity，不符即 `BUILD_FAILED`，
  message 要明說「`build()` 必須接受一個 ctx 參數」。
- **Q-009**：新增 `packages/sdk/pyproject.toml`，宣告 `jsonschema` / `httpx` / `langgraph` 為 runtime dependency。
  image 契約**不列舉** SDK 的傳遞依賴。runtime 啟動先做 import 自檢，缺件時報出缺哪個模組。
- **Q-010**：新增 `docker/agent-base.Dockerfile` → `zentra/agent-base:1.0`
  （Python 3.12 + zentra_sdk + langgraph + `/opt/zentra/bin/zentra-agent` + label + `USER 65534:65534`）。
  `examples/demo-echo/` 改成 `FROM zentra/agent-base:1.0` + 一行 `COPY`。
  **不發佈公開 Registry、不簽章、不多架構、tag 固定 `1.0`。**

## 6. 分工與交付順序

### 順序（有閘門，不是建議）

1. **@ui-designer**：`ui-contract.md` → v0.6.0（§4 列的內容）。**Console 不得在這之前開工。**
2. **@qa-verifier**：`specs/features/ZEN-009/acceptance.md`（§7 是必須涵蓋的最低集合）。**開發前完成。**
3. **@architect**：檢查 UI／API／acceptance 三者無衝突（本 spec 已預先裁定 `SPEC_READY`，見交接單）。
4. 平行開工：
   - **@platform-eng** 先交 compose + registry + volumes + base image ——
     **後端的 PUSH stage 依賴它存在**，這是唯一的硬順序。
   - **@backend-dev** 在 registry 起得來之後接 multipart 端點、migration、LOAD/PUSH stage。
   - **@frontend-dev** 在 ui-contract v0.6.0 之後開工。
5. 整合 → @qa-verifier 驗收 → @spec-keeper drift 稽核。**只有 QA 能標 `DONE`。**

### 可寫範圍

| 角色 | 工作 | 可寫範圍 |
|---|---|---|
| @platform-eng | `compose.yaml` 加 `registry` + `registrydata` + `uploads` volume；base image Dockerfile；維運說明（備份範圍、手動 GC 程序） | `compose.yaml`、`docker/**`、`apps/sandbox/**` |
| @backend-dev | multipart 端點與串流落檔、磁碟預檢、migration、`q.build` 的 LOAD/PUSH stage 與冪等、`artifact.origin`、SDK `pyproject.toml` 與 `build()` 簽章檢查 | `apps/api/**`、`apps/worker/**`、`packages/sdk/**` |
| @ui-designer | `ui-contract` v0.6.0 §5.4 改寫 | `specs/ui-contract.md` |
| @frontend-dev | `version-registration.js` 上傳模式、`XMLHttpRequest` 進度、null digest 呈現 | `apps/console/**` |
| @qa-verifier | `acceptance.md` + `tests/zen009/` | `tests/**`、`specs/features/*/acceptance.md` |

## 7. 驗收條件（@qa-verifier 在開發前寫進 `acceptance.md`）

> 本節是**最低集合**，不是完整清單。@qa-verifier 可以加，不可以減。
> 架構師要求的重點如下。

### 7.1 P5 可重現性（**最重要，缺這條不算通過**）

- **prune 後仍能按 digest 重新 pull**：上傳 → `PUBLISHED` → 以 `docker image rm` 移除該 image 的
  **所有**本機參照（tag 與 digest ref）→ 確認 `docker image inspect` 找不到 →
  觸發一次 Run → **Run 成功**，且事後 `docker image inspect` 的 `RepoDigests`
  含有與 DB `image_digest` **完全相同**的 digest。**這條要有終端輸出當證據，不接受口頭宣稱。**
- push 後 `RepoDigests` 非空，且其中的 digest == DB `image_digest` == `version_id` 前 12 碼的來源。
- 以 Registry HTTP API 獨立核對：`HEAD /v2/zentra/<agent_id>/manifests/<semver>` 回的
  `Docker-Content-Digest` == DB `image_digest`。（**不是** `docker load` 後的 `ImageID`。）
- DB CHECK 生效：直接 SQL 試把一個 `image_digest IS NULL` 的列改成 `PUBLISHED` → **必須被拒絕**。

### 7.2 隔離與憑證邊界

- `registry` 容器的 networks **不含** `zentra-sandbox-net`（靜態檢查 compose + 執行期 `docker inspect`）。
- 沙盒內解析／連線 `registry`、`registry:5000`、`localhost:5000`、`127.0.0.1:5000` **全部失敗**。
- `registry` 的 port binding 只有 `127.0.0.1:5000`，**沒有 `0.0.0.0`**。
- 沙盒與 validation 容器的 env 不含任何 Registry 位址或憑證；`api` 未掛 docker.sock。
- PostgreSQL 內沒有 image blob、沒有 tar、沒有 Registry 憑證（欄位與內容都查）。

### 7.3 上傳路徑本身

- 正常上傳 `examples/demo-echo` 的 tar → `PUBLISHED` → REST 與 MCP 都能呼叫。
- 上限：超過 `ZENTRA_MAX_IMAGE_UPLOAD_BYTES` → `422 VALIDATION_ERROR`、**沒有版本列**、**沒有殘留檔**。
- 壞輸入：非 tar 的隨機位元組、空檔、含 2 個 image 的 tar、非 `zentra.image/v1` 的 image
  → 都是 `BUILD_FAILED`，`details.stage` 分別正確（`LOAD` / `LOAD` / `LOAD` / `INSPECT`）。
- `semver` 重複 → `409 VERSION_CONFLICT`，且**在讀完 body 之前**就回（用大檔驗證 fail-fast）。
- 未知的 multipart 欄位、缺 `file`、缺 `semver`、非 multipart 也非 JSON 的 Content-Type → `422`。
- tar 在終態後**一定**被刪除（成功與失敗各驗一次）。
- 冪等：`q.build` 重送（a）digest 已存在時跳過 LOAD/PUSH 且不產生第二個 image；
  （b）digest 為 NULL 且 tar 已不存在時 → `BUILD_FAILED stage=LOAD`，不 hang。
- API 記憶體：上傳 216 MB 期間 `api` 行程 RSS 不得出現等量增長（證明是串流不是一次讀完）。

### 7.4 兩條路徑等價

- 同一個 image：一次走外部 Registry 註冊、一次走上傳，兩者的
  `artifact.digest` **相同**，entrypoints schema 相同，Run 行為相同，只有 `artifact.origin` 不同。
- 全文檢索確認 `INSPECT` 之後的程式碼**沒有任何**依 `image_origin` 的分支。
- ZEN-008 既有測試全數重跑，**不得退化**（含 137 個 registry-dependent skipped 的解封狀況說明）。

### 7.5 契約一致性

- `202` 回應在 `UPLOADED` 且 `BUILDING` 時：`version_id`、`artifact.image_ref`、`artifact.digest`
  皆為 `null`，**欄位仍須出現**；`PUBLISHED` 後三者皆有值。
- 錯誤 code 全部來自 `api-contract.md` §0，**沒有自創**；`details.stage` 只出現
  `LOAD|PUSH|PULL|INSPECT|VALIDATE`。
- Console 不送 `image_ref`、不送 registry 位址、不送任何憑證。

### 7.6 Q-008 / Q-009 / Q-010

- `def build()`（零參數）的 image → `BUILD_FAILED`，message 明確指出簽章要求（**不是 TypeError 堆疊**）。
- 缺 `jsonschema` 的 image → `BUILD_FAILED`，message 指出缺哪個模組。
- 以 `zentra/agent-base:1.0` + 三行 Dockerfile 做出來的 image，上傳後**一次通過**驗證。

## 8. 風險與未決問題

| 風險 | 影響 | 對策 |
|---|---|---|
| `registrydata` volume 被刪或未納入備份 | 上傳來源的 `PUBLISHED` 版本**永久不可執行** | 寫進 SPEC §6.1、維運說明與 README；MVP 不做自動備份，誠實標示 |
| 無 GC，失敗的上傳永久佔空間 | 磁碟緩慢成長，滿了表現為 `BUILD_FAILED stage=PUSH` | `delete.enabled=false` 防誤刪 + 手動 GC 程序；上傳前磁碟預檢；做版本刪除功能前不做自動 GC |
| `docker load` 解 tar 的是 root daemon | 惡意 tar 可打 daemon＝主機 root | 信任模型內接受；平台行程不解析 tar；大小上限；跑不可信 image 前必須重評（ADR-0009） |
| 遠端 `DOCKER_HOST` 部署 | `localhost:5000` 指向錯的機器，push 會成功但 pull 失敗 | 文件明示「daemon 與 compose 同機」是前提；QA 不需覆蓋遠端 daemon |
| 216 MB 上傳無續傳 | 網路中斷＝整包重來，且要換新 semver | 明確不做續傳；Console 要顯示進度與明確的失敗文案 |
| `localhost:5000` 出現在使用者看得到的 `image_ref` | 使用者困惑（他們連不到那個位址） | UI 文案說明「這是平台內部位址」；@ui-designer 在 v0.6.0 處理 |
| 上傳路徑最後沒人用 | 白白多養一個服務 | ADR-0009「何時該重新檢討」已寫下移除條件 |

**未決 SPEC-QUESTION：無。** Q-008 / Q-009 / Q-010 已於 ADR-0009 §7 裁決。
Q-002～Q-007 與 Q-009(UI) 屬 ui-contract，歸 @ui-designer，不阻擋本任務。

## 9. 完成的定義

一個從未接觸過任何外部 Registry 的環境裡：

1. 使用者在 Console 上傳 `docker save` 的 tar，版本自動走 `LOAD → PUSH → INSPECT → VALIDATE`
   成為 `PUBLISHED`，且 `artifact.digest` 是**內部 Registry 回報的 manifest digest**。
2. REST 與 MCP 都能呼叫該版本，Run 正常完成、事件可查、終態後容器不存在。
3. **把該 image 的所有本機參照 prune 掉之後，Run 仍然成功**（daemon 依 digest 重新 pull）。
4. 同一個 image 經外部 Registry 註冊所得的版本，除了 `artifact.origin` 之外**行為完全相同**。
5. 沙盒連不到 Registry；PostgreSQL 裡沒有 image blob；暫存 tar 已刪除。
6. ZEN-008 的既有測試沒有退化。
