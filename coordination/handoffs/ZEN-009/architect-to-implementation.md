# 交接單 Handoff

**任務**：`ZEN-009` 在 Console 上傳 docker image 建立 Agent 版本
**從**：@architect　**給**：@ui-designer、@qa-verifier、@platform-eng、@backend-dev、@frontend-dev（主 session 分派）
**日期**：2026-09-22
**Spec 版本**：`v0.4.0`（api-contract 同步 `v0.4.0`）

---

## 0. 裁定：**ZEN-009 可進 `SPEC_READY`**

理由：使用者已裁示路線（路 2）、ADR-0009 已落地、SPEC 與 api-contract 已 bump、
範圍界線與「明確不做」清單已寫死、三則未決提問（Q-008/009/010）已一併裁決。

**但有兩個開工閘門（不阻擋 `SPEC_READY`，阻擋對應角色開工）**：

| 閘門 | 誰 | 擋住誰 |
|---|---|---|
| `specs/ui-contract.md` bump 到 **v0.6.0** | @ui-designer | **@frontend-dev 不得開工** |
| `specs/features/ZEN-009/acceptance.md` | @qa-verifier | 全體（開發前必須有驗收條件） |

@platform-eng 與 @backend-dev **不受 UI 閘門限制**，acceptance 一寫完即可開工。

## 1. 我做了什麼

裁決「Console 上傳 image tar」的架構，寫成 ADR-0009：**推翻 ADR-0008 對選項 B（自帶 Registry）的否決**，
新增第 5 個長駐服務（內部 `registry:2`），並把上傳做成 `POST /v1/agents/{id}/versions` 的
第二個 `Content-Type`，而**不是**第二個端點、**不是**第二種 artifact 型別。
`version_id` 語意一個字沒改，P1–P5 一條沒鬆，P5 的可重現性反而被寫成 DB 層的不變式。
順帶裁決 Q-008（`build()` 簽章）、Q-009（SDK 依賴宣告）、Q-010（官方 base image）。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/adr/ADR-0009-internal-registry-image-upload.md` | 新增。含對 ADR-0008 否決理由的逐條重新定價、與 ADR-0005 的衝突處置（**修訂，不是例外**）、三則提問的裁決 |
| `specs/features/ZEN-009/spec.md` | 新增。範圍界線、明確不做、設計要點、分工順序、QA 必測集合、完成定義 |
| `specs/SPEC.md` | v0.3.1 → **v0.4.0**：§1 §4 §5 §6 §6.1 §7.1 §8.1 §8.2 §10 §11 §13 |
| `specs/api-contract.md` | v0.3.1 → **v0.4.0**：§0 `QUOTA_EXCEEDED` 說明放寬、§2 versions 端點改寫成路徑 A/B、§5 變更紀錄 |
| `docs/architecture/oci-image-agent-flow.md` | 新增「上傳路徑」資料流圖 |
| `docs/architecture/detailed-architecture.md` | 頁首加註：4 個長駐服務 → 5 個（其餘圖尚未重畫，已標示） |

## 3. 契約影響

- [x] **有影響** → `specs/api-contract.md` **v0.4.0**、`specs/SPEC.md` **v0.4.0**（已同步更新）。
- **沒有新增任何錯誤 code。** 只做兩件事：`details.stage` 值域加 `LOAD`/`PUSH`；
  `QUOTA_EXCEEDED` 的說明從「併發 Run 上限」放寬到「併發 Run 上限或磁碟餘裕不足」。
- **`version_id` 語意不變**：push 後 registry 回報的 manifest digest 前 12 碼。
- Runs / SSE / MCP 形狀**零改動**。
- `specs/ui-contract.md` **尚未** bump——那是 @ui-designer 的。

## 4. 下一個人要做什麼

### 4.1 @ui-designer（先做，擋住前端）

把 `ui-contract.md` bump 到 **v0.6.0**，改寫 §5.4 `version-registration.js`。
**現行 §5.4 明文寫「這是文字表單，沒有檔案輸入、拖放區、upload progress」，與 ZEN-009 直接衝突。**
需要涵蓋：

1. 兩種模式（「貼 digest」／「上傳 image tar」）的切換與各自五態。**兩者對等呈現**，上傳不是次等選項。
2. 檔案選擇（拖放可選）、上傳進度、取消上傳。
3. `BUILDING` 期間 `version_id` / `artifact.digest` / `artifact.image_ref` **都是 `null`** 的呈現——
   現行 §5.4 假設 `202` 之後就有 digest 可顯示，上傳路徑沒有。
4. `BUILD_FAILED` 新增 `stage=LOAD` / `stage=PUSH` 的文案。
5. `image_ref` 會長成 `localhost:5000/zentra/<agent_id>@sha256:…`——**使用者連不到那個位址**，
   需要一行說明它是平台內部位址，避免被當成可複製去 `docker pull` 的東西。
6. 一行說明「上傳 vs 外部 Registry 各適合什麼」。

**已預先核准的實作手段（不必開 SPEC-QUESTION）**：上傳進度用原生 `XMLHttpRequest`
（`fetch` 沒有 upload progress 事件）。這不是引入函式庫，符合 ADR-0006。

### 4.2 @qa-verifier（開發前）

寫 `specs/features/ZEN-009/acceptance.md`。**最低必測集合見 feature spec §7**，可加不可減。
架構師特別要求、**缺了就不算通過**的一條：

> **image 被 prune 後仍能按 digest 重新 pull 的證據。**
> 上傳 → `PUBLISHED` → `docker image rm` 移除該 image 的**所有**本機參照（tag 與 digest ref）
> → 確認 `docker image inspect` 找不到 → 觸發一次 Run → **Run 成功** →
> 事後 `RepoDigests` 含有與 DB `image_digest` 完全相同的 digest。
> **要有終端輸出當證據，不接受口頭宣稱。** 這條是整個 ADR-0009 的立論基礎，沒驗＝核心未驗。

另外三條容易被忽略但同樣重要：
- `registry` 容器的 networks **不含** `zentra-sandbox-net`；沙盒連 `registry:5000` 必須失敗。
- 上傳 216 MB 期間 `api` 行程 RSS 不得等量增長（證明是串流不是 `await file.read()`）。
- 全文檢索確認 `INSPECT` 之後**沒有任何**依 `image_origin` 的分支。

### 4.3 @platform-eng（硬順序：後端的 PUSH stage 等你）

`compose.yaml` 新增：

```yaml
  registry:
    image: registry:2
    environment:
      REGISTRY_STORAGE_DELETE_ENABLED: "false"
    volumes: [registrydata:/var/lib/registry]
    ports: ["127.0.0.1:5000:5000"]     # 只綁 loopback，不得用 0.0.0.0
    networks: [zentra-net]             # 絕對不要接 zentra-sandbox-net
    healthcheck: <打 GET /v2/，回 200>
```

- 新增 volumes：`registrydata`、`uploads`。`uploads` 由 `api` 與 `worker` 共掛
  `/var/lib/zentra/uploads`（兩邊都要讀寫）。
- `api`、`worker` 加 `depends_on: registry`。**`api` 仍然不掛 docker.sock。**
- 新增 env（預設值）：`ZENTRA_INTERNAL_REGISTRY=localhost:5000`、
  `ZENTRA_UPLOAD_DIR=/var/lib/zentra/uploads`、`ZENTRA_MAX_IMAGE_UPLOAD_BYTES=2147483648`。
- 新增 `docker/agent-base.Dockerfile` → `zentra/agent-base:1.0`
  （Python 3.12 + `zentra_sdk` + langgraph + `/opt/zentra/bin/zentra-agent` + label
  `io.zentra.image-contract=zentra.image/v1` + `USER 65534:65534`）。
  `examples/demo-echo/Dockerfile` 改成 `FROM zentra/agent-base:1.0` + 一行 `COPY`。
  **不推公開 Registry、不簽章、不多架構、tag 固定 `1.0`。**
- 維運說明要寫：**`registrydata` 與 `pgdata` 同屬備份範圍**；手動 GC 程序（停機 + `registry garbage-collect`）。

**已知的坑（會浪費你半天，先讀）**：
- push／pull 的執行者是**主機上的 Docker daemon**，不是 worker 行程。daemon 不在 `zentra-net` 上，
  **解析不到服務名 `registry`**。所以 image ref 必須用 `localhost:5000`，也因此必須發布 loopback port。
- 選 `localhost` 而不是容器 IP 或自訂 host name，是因為 Docker daemon 對 `localhost`／`127.0.0.0/8`
  **預設允許明文 HTTP**，不必改 `/etc/docker/daemon.json` 的 `insecure-registries`。換名字＝多一個部署步驟。
- 前提假設：daemon 與 compose 同一台主機。遠端 `DOCKER_HOST` 會 push 成功、pull 失敗，不在 MVP 範圍。

### 4.4 @backend-dev

**端點**：`POST /v1/agents/{agent_id}/versions`，同一條路由，依 `Content-Type` 分流。

`multipart/form-data` 只接受兩個 part：`semver`（text）、`file`（binary，`docker save` tar）。
**其他 part 一律 `422`**（含 `image_ref`、registry 位址、憑證、Dockerfile）。非 JSON 非 multipart → `422`。

處理順序（**順序本身是規格**）：
1. agent 存在且未軟刪除 → 否則 `404`。
2. `semver` 未使用 → 否則 `409 VERSION_CONFLICT`。**這兩步要在讀 body 之前**（別收完 216 MB 才說重複）。
3. 磁碟預檢：可用空間 < `2 × Content-Length + 1 GiB` → `429 QUOTA_EXCEEDED` + `Retry-After: 60`，
   `details.reason="insufficient_disk"`。
4. **串流**寫 `${ZENTRA_UPLOAD_DIR}/<agent_version_id>.tar`（0600）。
   **不得 `await file.read()`。** 超過 `ZENTRA_MAX_IMAGE_UPLOAD_BYTES` → 中止、刪半檔、
   `422 VALIDATION_ERROR`，`details={"field":"file","reason":"too_large","limit_bytes":N}`，**不建立版本列**。
5. 落檔成功才 INSERT（`status=BUILDING`、`image_origin=UPLOADED`、
   `image_ref`/`image_digest`/`version_id` 皆 `NULL`）→ commit → 送 `q.build`。
   insert 或 enqueue 失敗 → **刪檔**，不留孤兒。
6. 回 `202`，shape 與 JSON 路徑相同，digest 三欄為 `null`（**欄位仍要出現**）。

`202` 實際形狀（照抄）：
```json
{"agent_version_id":"0192...","semver":"1.2.0","version_id":null,"source_type":"OCI_IMAGE",
 "artifact":{"origin":"UPLOADED","image_ref":null,"digest":null,"platform":null,
             "size_bytes":null,"runtime_contract":null},
 "status":"BUILDING","error":null,"created_at":"..."}
```
JSON 路徑的 `artifact` 也要補 `"origin":"REGISTERED"`。

**Migration**（`agent_versions`）：
- 新增 `image_origin String(16) NOT NULL DEFAULT 'REGISTERED'` + CHECK 限 `REGISTERED|UPLOADED`，既有列回填。
- `image_ref` / `image_digest` / `version_id`：`NOT NULL` → **nullable**（既有 regex CHECK 不用改，
  Postgres 的 CHECK 對 NULL 自動通過）。
- 新增 CHECK：`status <> 'PUBLISHED' OR (image_ref IS NOT NULL AND image_digest IS NOT NULL AND version_id IS NOT NULL)`。
- 新增 CHECK：`image_origin <> 'REGISTERED' OR image_ref IS NOT NULL`。
- `source_type` 維持固定 `OCI_IMAGE`，**不得新增 artifact 型別**。

**`q.build` 新增兩個 stage**（插在既有 `INSPECT → VALIDATE` 之前，`apps/worker/tasks/build.py`）：
- `LOAD`：把 tar 以 stream 交給 daemon（`client.images.load(data)`）。必須恰好 1 個 image，
  0 或 >1 → `BUILD_FAILED stage=LOAD`。
  **不得用 Python `tarfile` 解壓或讀 `manifest.json`**——把使用者的 archive 搬進平台行程解析違反 P1 的精神。
  要資訊就 load 完 `inspect`。
- `PUSH`：tag `${ZENTRA_INTERNAL_REGISTRY}/zentra/<agent_id>:<semver>` → push →
  **以 `inspect` 確認 `RepoDigests` 含 `<repo>@sha256:<digest>`**，缺少即 `BUILD_FAILED stage=PUSH`
  （訊息點明「registry 未回報 manifest digest」）。
  取得 digest 後在同一 transaction 寫 `image_ref`/`image_digest`/`version_id`，commit，**然後刪 tar**。
- 之後 **重用** ZEN-008 既有的 INSPECT/VALIDATE 程式碼，**不得複製第二份**。
- 冪等：重送時 `image_digest` 已存在 → 跳過 LOAD/PUSH 從 INSPECT 續跑；
  `image_digest` 為 NULL 且 tar 不存在 → `BUILD_FAILED stage=LOAD`，訊息要說「請以新的 semver 重新上傳」。
  **不得 hang、不得靜默成功。** 終態失敗一律在 `finally` 刪 tar。

**`image_origin` 是純描述性**：`INSPECT` 之後的任何程式碼**不得依它分支**。QA 會全文檢索這件事。

**另外兩件小事（Q-008 / Q-009 的落地，`packages/sdk/**` 是你的）**：
- `validate` mode 在呼叫 `build` 前用 `inspect.signature` 檢查 arity，不符 → `BUILD_FAILED`，
  message 明說「`build()` 必須接受一個 ctx 參數」（不要丟 `TypeError` 堆疊給使用者）。
- 新增 `packages/sdk/pyproject.toml`，宣告 `jsonschema` / `httpx` / `langgraph` 為 runtime dependency；
  runtime 啟動先做 import 自檢，缺件時報出**缺哪個模組**。

### 4.5 @frontend-dev（等 ui-contract v0.6.0）

`apps/console/static/js/pages/version-registration.js` 加上傳模式。要點：
- 用 `XMLHttpRequest` 送 multipart 以取得 upload progress（**已預先核准**，不是引入函式庫）。
- `202` 之後 `version_id` / `artifact.digest` / `artifact.image_ref` 是 `null`，
  輪詢到 `PUBLISHED` 才有值——**不要假設 202 就有 digest 可顯示**。
- 錯誤 code 只會是 `VALIDATION_ERROR` / `VERSION_CONFLICT` / `QUOTA_EXCEEDED` / `NOT_FOUND`
  / `BUILD_FAILED`（`details.stage` ∈ `LOAD|PUSH|PULL|INSPECT|VALIDATE`）。**沒有新 code。**
- 一律 `textContent`，不要 `innerHTML`。

## 5. 我沒做的事（誠實列出）

- **沒有寫任何產品程式碼**，也沒有改 `compose.yaml`（那是 @platform-eng 的）。
- **沒有實測** `docker load → tag → push → RepoDigests` 這條鏈；我採信主 session 2026-09-22 的實測數字
  （`RepoDigests=[]`、tar 216 MB、manifest digest ≠ config digest）並據以推論。
  **push 後 `RepoDigests` 會被填上**是 Docker 的標準行為，但**本任務沒有實測過**，
  它是 ADR-0009 的關鍵假設——@qa-verifier 的 §7.1 就是要驗它。
- **沒有改 `coordination/BOARD.md`**（紀律）。需要主 session 整合的內容見 §8。
- **沒有改 `ui-contract.md`**（@ui-designer 的）。
- 沒有重畫 `detailed-architecture.md` 的 SVG 與各節拓撲圖，只在頁首加了 4 → 5 的註記。
- 沒有處理 `AGENT-D1`（軟刪除的 Agent 永久佔用 name）與 ZEN-008 的兩個 QA 缺陷
  （`GET /v1/agents` 缺 `next_cursor`、`GET /v1/agents/{id}` 省略 null 欄位）——
  它們屬 ZEN-008，不在本任務範圍，但**會被 ZEN-009 的 Console 流程碰到**，建議一併排。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| `registrydata` 被刪或沒備份 | 上傳來源的 `PUBLISHED` 版本**永久不可執行** | 寫進維運說明與 README；MVP 不做自動備份 |
| 無自動 GC | 失敗的上傳永久佔空間，滿了表現為 `BUILD_FAILED stage=PUSH` | 做「版本刪除」功能之前不做 GC（沒有刪除語意的 GC 是定時炸彈） |
| `docker load` 解 tar 的是 root daemon | 惡意 tar ＝ 主機 root | 信任模型內接受；平台行程不解析 tar；跑不可信 image 前必須重評 ADR-0009 |
| `localhost:5000` 寫進 DB 的 `image_ref` | 遠端 daemon 或多機部署時是一次痛苦遷移 | 文件明示「daemon 與 compose 同機」是前提 |
| 216 MB 上傳無續傳 | 中斷＝重來，且要換新 `semver` | 明確不做；UI 要有清楚的失敗文案 |
| ZEN-008 尚未驗收通過（`IN_PROGRESS`，137 個 registry 相關測試 skipped） | ZEN-009 疊在未驗證的基礎上 | **建議**：ZEN-009 的 registry 一旦就位，那 137 個 skipped 有機會一起解封——請 @qa-verifier 評估併驗 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 單元測試 | **未跑** | 本任務未產生程式碼 |
| 契約測試 | **未跑** | 契約剛 bump，實作尚未開始 |
| 契約自洽性 | ✅ 人工檢查 | SPEC v0.4.0 與 api-contract v0.4.0 的 stage 值域、null 語意、`origin`、服務數互相一致 |
| 關鍵技術假設 | ⚠️ **未實測** | 「push 後 `RepoDigests` 非空」採信標準 Docker 行為，交由 QA §7.1 驗證 |

## 8. 未決的 SPEC-QUESTION 與要請主 session 整合進 BOARD 的內容

- [x] **Q-008（`build()` 簽章）→ RESOLVED**：契約明訂 `def build(ctx: ZentraContext) -> StateGraph`，
  必須接受且只接受一個位置參數；`validate` 在呼叫前檢查 arity，不符回 `BUILD_FAILED` 並給可行動訊息。
  已收斂進 SPEC v0.4.0 §8.1。
- [x] **Q-009（SDK 執行期依賴）→ RESOLVED**：由 SDK 自己宣告（新增 `packages/sdk/pyproject.toml`：
  `jsonschema`/`httpx`/`langgraph`），image 契約**不列舉**傳遞依賴；runtime 啟動做 import 自檢。
  已收斂進 SPEC v0.4.0 §8.1。
- [x] **Q-010（官方 base image）→ RESOLVED**：升級為正式產出 `zentra/agent-base:1.0`
  （`docker/agent-base.Dockerfile`），本機 build、不發佈公開 Registry、不簽章、不多架構、tag 固定 `1.0`。
  已收斂進 SPEC v0.4.0 §8.1，工作排進 ZEN-009（@platform-eng）。
- [ ] Q-002～Q-007 與 UI 的 Q-009（編輯／刪除 dialog 追認）仍 OPEN，**屬 @ui-designer**，不阻擋 ZEN-009。

**請主 session 一併更新（我不能寫的地方）**：
1. `coordination/BOARD.md`：ZEN-009 → `SPEC_READY`，補交接單連結與 ADR-0009 連結；
   Q-008/Q-009(orchestrator)/Q-010 標 RESOLVED 並填裁決。
2. `coordination/BOARD-archive.md` 的 **§刻意不做** 清單：移除「image tar 直傳」與「平台內建 OCI Registry」，
   **保留** Dockerfile build service。SPEC §11 已同步處理。
3. `CLAUDE.md`、`AGENTS.md`、`README.md` 中「4 個長駐服務」的敘述 → **5 個**
   （`postgres` · `redis` · **`registry`** · `api` · `worker`）。
   `CLAUDE.md` 的常用指令區也要補 `docker build -f docker/agent-base.Dockerfile -t zentra/agent-base:1.0 .`。
