# ADR-0009：內部 OCI Registry

> R1 原標題為「內部 OCI Registry 與 Console 上傳 image tar」。**R2 撤回了 tar 上傳**，
> 改為使用者在平台主機上直接 `docker push`。
> **檔名刻意不改**（`ADR-0009-internal-registry-image-upload.md`）——
> 它已被 SPEC、api-contract、BOARD、glossary、架構文件與 `.claude/agents/` 多處連結，
> 為了「修訂註記而非重寫歷史」，連結穩定性優先於檔名精確性。

| | |
|---|---|
| 狀態 | **已採納，並於 R2 部分修訂 (Accepted, amended by R2)** |
| 日期 | R1 2026-09-22 · **R2 2026-09-23** |
| 決策者 | @architect（R1 依使用者裁示選路 2；**R2 依使用者裁示改為同機直接 `docker push`**） |
| 相關 | **推翻 [ADR-0008](ADR-0008-oci-image-agent-packaging.md) 對選項 B 的否決**；修訂 [ADR-0005](ADR-0005-mvp-single-host-scope.md) 的長駐服務數；P1–P5 全部不動 |

> ⚠️ **本 ADR 已被自己的修訂 R2 部分推翻。先讀文末的 §修訂 R2，再讀下面的 R1 原文。**
>
> **R2 保留的**：內部 OCI Registry 作為第 5 個長駐服務、只綁 `127.0.0.1:5000`、
> 不接 `zentra-sandbox-net`、無 TLS／無認證／無自動 GC、`version_id` 語意、
> 「沒有可重新 pull 的來源就不得 `PUBLISHED`」這條 P5 不變式、Q-008／Q-009／Q-010 的裁決。
>
> **R2 撤回的**：Console 上傳 `docker save` tar（R1 §3 路徑 B、R1 §4 全部）、
> `LOAD`／`PUSH` 兩個 build stage、`artifact.origin`／`image_origin` 欄位、
> `image_ref`／`image_digest`／`version_id` 的 nullable 化、`uploads` volume 與 2 GiB 上限、
> `QUOTA_EXCEEDED` 對磁碟餘裕的放寬。
>
> **R2 新增的**：使用者可以**在跑平台的那台主機上**直接 `docker push localhost:5000/<repo>:<tag>`，
> 再用**既有的** JSON `POST /v1/agents/{id}/versions` 以 digest 註冊。
> 下面 R1 原文中凡標 `🔻 R2 撤回` 者一律以 R2 為準，**原文保留是為了留下當時的推理，不是還有效**。

## 背景

ADR-0008 決定 Agent 版本以 canonical OCI digest 註冊，image 本體留在既有的外部 Registry。
這條路已經實作並可運作（ZEN-008），但它有一個前提：**使用者必須先有一個 Zentra VM 連得到的 Registry**。
對「內部工具、單機、一個人開發」的實際情境，這個前提是最大的採用阻力——
使用者手上已經有 `docker build` 出來的 image，卻必須先去申請 GHCR／架一個 Registry 才能用平台。

使用者要求：**在 Console 直接上傳 docker image 來建立 Agent 版本**。
ADR-0008 當初評估過兩個相關選項並都否決了：**A（API 直接收 `docker save` tar，以本機 image 執行）**
與 **B（Zentra 自帶 OCI Registry）**。使用者選的是「**A 的 UX + B 的架構**」：
Console 收 tar → worker `docker load` → tag → push 進 compose 內的 Registry → 取得真正的 manifest digest
→ 接既有的 pull/inspect/validate/run 流程。

### 決定性的實測（主 session，2026-09-22）

```
registry 上的 manifest digest : sha256:5f177b80485c0d77b4acf733a6945f488efe3a242a4ecf60d55c04ed9473e14b
docker save → docker load 之後 : RepoDigests=[]
                                 ImageID=sha256:a6040472a3b0c4ccee6fad948f93cf71ca1e4c4954e7a559baf31c89693afcff
tar 大小                       : 216 MB（python:3.12-slim + langgraph 的最小 agent）
```

架構師對這三個數字的判讀（**同意主 session 的三個推論**，並補一點）：

1. `save`／`load` 丟失的是 **registry manifest digest**。載入後只剩 image config 的 digest
   （`ImageID`），兩者是**不同的識別**：manifest digest 涵蓋 config + 全部 layer descriptor，
   config digest 只涵蓋 config JSON。**不能互相代替，也不能互相推導。**
2. `RepoDigests=[]` 的意思是 daemon 知道這個 image 沒有任何「可以再拉一次」的來源。
   一次 `docker image prune`、一次 VM 重建、一次 daemon 資料目錄損毀，該 `PUBLISHED` 版本就永久不可執行。
   P5 的字面是「版本不可變」，但它隱含的承諾是「**這個版本以後還跑得出同樣的東西**」——
   沒有可重新取得的來源，不可變就退化成「不可修復」。
3. 因此路 1（直接以本機 image 執行）有結構性缺陷，不是可以用工程手段補起來的細節。
4. **補充**：推論 2 還有第二個後果——沒有 manifest digest 就沒有 `version_id`。
   若硬用 `ImageID` 當 `version_id`，兩條路徑的 `version_id` 語意會不同（一個是 manifest digest、
   一個是 config digest），而它們寫在同一個欄位、印在同一個 UI 上。這是會長期誤導人的設計。

路 2 把 tar 推進 Registry 後，digest 語意**完全不變**：`version_id` 定義不必改、`image_ref` 格式不必改、
`q.build` 之後的每一步（inspect → validate → PUBLISHED → run）都不必改。
**這是它真正的價值：它不是新的 artifact 型別，只是同一個 artifact 的第二條進場路線。**

## 選項

### 選項 1：API 直接收 tar，以本機 image 執行（否決）

- 好處：不需要任何新服務，長駐服務維持 4 個。實作最少。
- 代價：上面的實測已經說明——`PUBLISHED` 版本失去可重新取得的來源；`version_id` 語意要被迫改成
  config digest，兩條路徑的識別意義不同；`docker image prune` 變成破壞性操作。
- **否決理由：它用 P5 的可重現性換 1 個容器。這個交換在任何時間尺度上都不划算。**

### 選項 2：內部 Registry（採用）

Console 收 tar → worker `docker load` → tag → push 進 compose 內的 `registry:2` →
以 registry 回的 manifest digest 當版本身份 → 之後與 ADR-0008 完全同一條路。

- 好處：digest 語意不變、P5 不動、`q.build` 之後零分歧；tar 用完即刪；上傳路徑與註冊路徑在
  **push 完成的那一刻匯流**，之後只有一套程式碼、一套測試、一套失敗模式。
- 代價：長駐服務 4 → 5；多一個 volume 要進備份範圍；沒有 GC。
- 風險：Registry volume 被刪＝該批上傳版本不可執行（與外部 Registry 不可用是同一類風險，
  差別在責任歸屬變成我們自己）。

### 選項 3：不改架構，只在 Console 生成 build/push 指令（否決）

- 好處：零新服務、零新契約、一小時可做完。是誠實的「教使用者做對的事」。
- 代價：沒有解決使用者的問題。使用者要的是「我手上有 image，讓我上傳」，
  而不是「教我怎麼自己架 Registry」。
- **否決理由：這是把產品問題退回給使用者。** 但它是好的**補充**——
  ZEN-009 仍要保留外部 Registry 註冊路徑，且 Console 的文案要說清楚兩條路各自適合什麼場景。

## 正面回應：為什麼現在推翻 ADR-0008 對選項 B 的否決

ADR-0008 否決 B 的原文是：「長駐服務由 4 個增加為 5 個，還要處理 Registry 的 TLS、認證、垃圾回收、
容量與備份」「違反 ADR-0005 的單機降維原則」。

那個定價是**假設要做一個對外通用的 Registry**：開發者從自己的筆電 `docker push` 進來、
跨主機可達、多人共用。這次要做的不是那個東西。這次的 Registry 是**平台內部的 image 存放處**，
只有 `worker`（透過本機 Docker daemon）會 push／pull，**使用者永遠不會直接接觸它**。
逐條重新定價：

| ADR-0008 列的代價 | 在「內部、不對外、不跨主機」下還成立嗎 | 處置 |
|---|---|---|
| **TLS** | **不成立。** 流量只在單機 loopback 與 `zentra-net` 內，與 `postgres`／`redis` 同一個信任邊界。現行 compose 的 postgres 是 `0.0.0.0:5432` 明文發布，registry 只綁 `127.0.0.1:5000`，**比現況更緊**。 | 不做 TLS。發布範圍僅 loopback。 |
| **認證** | **不成立。** 沒有第二個 principal——唯一的寫入者是平台自己的 worker。加一組帳密只是多一個要保管、要輪替、要塞進 compose secret 的長期憑證，反而是 P4 的負擔。 | 不做認證。**改以網路面積控制**：不對外發布、不接 `zentra-sandbox-net`。 |
| **垃圾回收 (GC)** | **成立，但可以誠實不做。** 內部 Registry 只會因為「上傳後 validate 失敗」而留下垃圾，成長速率是人手動上傳的速率，不是 CI 的速率。 | MVP **不做自動 GC**，並把 `delete.enabled=false` 寫死（連誤刪都做不到）。提供維運手冊的手動 GC 程序。見下方「後果」。 |
| **容量** | **成立。** 216 MB 一個 image 是真的會吃磁碟。 | 單檔上限 2 GiB（可設定）＋上傳前的磁碟餘裕預檢（`429 QUOTA_EXCEEDED`）。不做配額系統。 |
| **備份** | **成立，而且這是本決定最真實的代價。** 在此之前，備份 `pgdata` 就能還原平台狀態（image 在別人家）；現在上傳來源的版本，其 image 本體只存在我們的 volume 裡。 | 明白寫進 SPEC 與維運說明：`registrydata` 與 `pgdata` **同屬備份範圍**。不做自動備份。 |

### 與 ADR-0005 的衝突：修訂，不是例外

ADR-0005 的三刀是「部署降維／基礎設施降維／範圍降維」，它砍掉七項的共同理由是
**「為不存在的威脅或不存在的使用者付錢」**。內部 Registry 不屬於那一類：
它服務的是一個**已經存在、使用者已經明確提出**的需求，而且它替代的是「使用者自己去架一個 Registry」，
總體複雜度是往下的，不是往上的。

因此本 ADR 的處置是**修訂 ADR-0005 的服務清單（4 → 5），而不是宣告一個例外**。
例外會變成先例（「既然 registry 可以，那 MinIO 為什麼不行」）；修訂則必須把理由寫在這裡，
下一個人要加第 6 個服務時得同樣舉證。ADR-0005 的其餘部分——單機、無 K8s、無 gVisor、信任程式碼、
一條主線——**一個字都沒改**。

同時**否決 profile-gated 變體**（把 registry 設成 `profiles: [upload]`，預設不啟動，維持 4 個服務）：
它會產生兩種拓撲、兩種行為（上傳功能在 A 拓撲可用、B 拓撲回錯），還要多一個能力探測端點讓 Console
決定要不要顯示上傳 UI。**收斂 > 彈性**：只有一種拓撲，上傳永遠可用，測試矩陣不分岔。

## 決定

### 1. 新增第 5 個長駐服務：內部 OCI Registry

```
docker compose（長駐，5 個）
  postgres    zentra-net
  redis       zentra-net
  registry    zentra-net          ← 新增。image: registry:2
  api         zentra-net + zentra-sandbox-net
  worker      zentra-net
```

- **網段：只接 `zentra-net`。絕對不接 `zentra-sandbox-net`。**
  這是 P4 與 SPEC §5 硬規則 3（沙盒的網路對端有且只有一個：`api`）的直接要求。
  沙盒拿到 image 是 daemon 給的，不是沙盒自己去拉的；沙盒沒有任何理由連得到 Registry。
- **Port：只發布 `127.0.0.1:5000:5000`，不得綁 `0.0.0.0`。**
  這一條是**必要的**，理由不是安全而是拓撲：push／pull 的執行者是**主機上的 Docker daemon**，
  不是 worker 行程。daemon 不在 `zentra-net` 上，解析不到 compose 服務名 `registry`。
  可行的名字只有主機 loopback。選 `localhost:5000` 還有一個附帶好處：
  Docker daemon 對 `localhost`／`127.0.0.0/8` 預設允許明文 HTTP，
  **不需要改 `/etc/docker/daemon.json` 的 `insecure-registries`**，部署零額外步驟。
- 設定：`REGISTRY_STORAGE_DELETE_ENABLED=false`、healthcheck 打 `GET /v2/`。
- 儲存：named volume `registrydata:/var/lib/registry`。
- 前提假設（寫下來，不要之後假裝忘記）：**Docker daemon 與 compose 在同一台主機**。
  用遠端 `DOCKER_HOST` 時 `localhost:5000` 會指向錯的機器——那已經不是 ADR-0005 的單機部署了。

### 2. `version_id` 語意不變，P5 不動

`version_id` = **push 進內部 Registry 之後，registry 回報的 manifest digest 的前 12 碼**。
權威身份仍是 `artifact.digest` 的完整值。兩條路徑的欄位語意**完全相同**。

新增一條硬規則：**版本不得在只有本機 image 的狀態下成為 `PUBLISHED`。**
（🔻 R2：這條**保留且加強**，但實作手段改變——R2 沒有 `BUILDING` 期間的 `NULL` 視窗，
三個欄位維持 `NOT NULL`，下面的條件式 CHECK 因此不需要了。）
push 成功、且 `docker image inspect` 的 `RepoDigests` 確實含有我們預期的
`<repo>@sha256:<digest>` 之前，`image_ref`／`image_digest`／`version_id` 一律是 `NULL`，
版本停在 `BUILDING`。這條規則直接用 DB CHECK 約束表達：

```sql
CHECK (status <> 'PUBLISHED'
       OR (image_ref IS NOT NULL AND image_digest IS NOT NULL AND version_id IS NOT NULL))
```

「`RepoDigests` 必須非空」就是把那個實測結果寫成不變式：
**沒有可重新 pull 的來源，就不是一個發布過的版本。**

### 3. 上傳走既有端點，不另開端點　🔻 **R2 撤回（multipart 路徑 B 與 `artifact.origin` 全部取消；只留 JSON 這一條）**

`POST /v1/agents/{agent_id}/versions` 同時接受兩種 `Content-Type`：

| Content-Type | 語意 | `artifact.origin` |
|---|---|---|
| `application/json` | 註冊外部 Registry 的 canonical digest（ADR-0008，不變） | `REGISTERED` |
| `multipart/form-data` | 上傳 `docker save` tar（本 ADR 新增） | `UPLOADED` |

理由：兩者建立的是**同一種資源**，有同一個 response shape、同一個狀態機、同一套
`409 VERSION_CONFLICT` 與輪詢語意。另開端點等於把這些語意複製一份，並強迫 Console
對同一個物件維護兩條程式碼路徑。**`source_type` 維持固定 `OCI_IMAGE`——上傳不是第二種 artifact 型別。**

`artifact.origin` 是**純描述性 metadata**：記錄「這個版本是怎麼進來的」，
**任何執行期行為都不得依它分支**。這是兩條路徑不會偷偷長成兩套系統的護欄。

### 4. tar 落地在哪：共享 volume，用完即刪，絕不進 PostgreSQL　🔻 **R2 全節撤回（沒有 tar，也沒有 `uploads` volume）**

```
api   →  寫  /var/lib/zentra/uploads/<agent_version_id>.tar   (named volume uploads, 0600)
worker→  讀  同一路徑 → docker.images.load() → tag → push → 記下 digest → commit → 刪檔
```

- 路徑由 `agent_version_id` **決定性推導**，不存 DB 欄位、不接受任何 client 提供的路徑或檔名。
- `api` **不掛 docker.sock**（compose 既有規則，本決定不鬆動），所以 load 必須由 worker 做。
- 刪檔時機：**push 成功且 digest 已 commit 之後**立即刪；終態失敗時在 `finally` 刪。
  這個順序讓 Celery 重送可以在「digest 已存在」時跳過 LOAD／PUSH，直接從 INSPECT 續跑——
  重送冪等不依賴暫存檔還在。
- 上限 2 GiB（`ZENTRA_MAX_IMAGE_UPLOAD_BYTES`）。超過即中止串流、刪除半檔、回 `422 VALIDATION_ERROR`，
  **不建立版本列**。

### 5. 錯誤碼：不新增任何 code

沿用 `api-contract.md` §0 既有枚舉。本決定只做兩件事，都在契約 v0.4.0 記錄：

- 🔻 **R2 撤回**：`error.details.stage` 的值域從 `PULL | INSPECT | VALIDATE` **擴充**為
  `LOAD | PUSH | PULL | INSPECT | VALIDATE`。這是既有欄位的值域擴充，不是新 code。
- 🔻 **R2 撤回**：`QUOTA_EXCEEDED` 的說明從「超過併發 Run 上限」**放寬**為「超過資源上限（併發 Run 或磁碟餘裕）」，
  上傳前磁碟預檢失敗時回 `429 QUOTA_EXCEEDED` + `Retry-After`，`details.reason="insufficient_disk"`。

刻意**不**新增 `PAYLOAD_TOO_LARGE`／`UNSUPPORTED_MEDIA_TYPE`／`REGISTRY_ERROR`：
前兩者是 `VALIDATION_ERROR` 的特例（`details` 已足以區分），第三者是 `BUILD_FAILED` 的階段
（`details.stage=PUSH`）。錯誤碼枚舉每多一個，前端、SDK、QA 就多一條分支。

### 6. 兩條路徑並存的規則　🔻 **R2 改寫（只剩一條程式碼路徑；image 可以存在外部或內部 Registry，見 R2-1）**

1. **匯流點**：兩條路都在「`agent_versions.image_ref` 拿到 canonical `repository@sha256:<manifest digest>`」
   這一刻匯流。匯流之後**只有一條程式碼路徑**：INSPECT → VALIDATE → PUBLISHED → Run。
   匯流後還有任何依來源分支的行為，都是 bug。
2. **外部 Registry 註冊仍是正途**，不降級、不隱藏、不加警語。適合：CI 產生的 image、
   大 image、之後要多機部署、已有 Registry 的團隊。
3. **上傳是第二條路徑**，定位是「手上已經有 image、還沒有 Registry」的人的捷徑。
4. 兩條路徑共用 `semver` 命名空間與 `409 VERSION_CONFLICT`；同一個 Agent 可以一個版本來自上傳、
   下一個版本來自外部 Registry，反之亦然。
5. **不會有第三條路徑。** 平台不收 Dockerfile、不做遠端 build、不從 URL 拉 tar。要加 → 新 ADR。
6. 沙盒對兩條路徑都一樣：拿不到 Registry 位址、拿不到憑證、連不到 `registry:5000`。

### 7. 順帶裁決三則與 image 契約有關的提問（Q-008 / Q-009 / Q-010）

這三則直接影響上傳路徑的使用者體驗——上傳的人更可能是手工做 image 的人，
契約含糊的代價會直接變成「上傳完等兩分鐘、拿到一個看不懂的 BUILD_FAILED」。

- **Q-008（`build()` 簽章）：契約明訂簽章。**
  `def build(ctx: ZentraContext) -> StateGraph`——**必須接受且只接受一個位置參數**，
  參數名稱不限，回傳未 compile 的 `StateGraph`。
  並要求 `validate` mode 在**呼叫之前**先用 `inspect.signature` 檢查 arity，
  不符時回 `BUILD_FAILED` 且 message 明確指出「`build()` 必須接受一個 ctx 參數」。
  理由：這個錯誤只在驗證沙盒裡看得到，使用者手上沒有堆疊，訊息就是唯一的線索。
- **Q-009（SDK 的執行期依賴）：由 SDK 自己宣告，契約不列舉。**
  新增 `packages/sdk/pyproject.toml`，把 `jsonschema`、`httpx`、`langgraph` 列為 SDK 的
  runtime dependency。image 契約只說「image 內要有一份可用的 `zentra_sdk`」，
  **不列舉它的傳遞依賴**——列舉會過期，過期的契約比沒有契約更糟。
  另要求 runtime 啟動時先做 import 自檢，缺件時報出**缺哪個模組**。
- **Q-010（官方 base image）：升級為正式產出。**
  `docker/agent-base.Dockerfile` → `zentra/agent-base:1.0`（Python 3.12 + zentra_sdk + langgraph
  + `/opt/zentra/bin/zentra-agent` + label + `USER 65534:65534`）。
  使用者的 Dockerfile 縮成三行：`FROM zentra/agent-base:1.0` + `COPY agent.py /opt/zentra/app/agent.py`。
  **範圍上限**：本機 build，**不發佈到任何公開 Registry、不做簽章、不做多架構、不承諾相容性政策**，
  tag 固定 `1.0`。`examples/demo-echo/` 改成以它為基底。
  理由：上傳路徑讓「手工做 image」成為常見動作，base image 是讓「上傳的 tar 能通過驗證」
  變成預設結果的最便宜手段。

## 後果

### 正面

- 使用者不需要任何外部 Registry 就能用整個平台。這是 ADR-0008 留下的最大採用阻力，現在移除了。
- P5 的可重現性**變強而不是變弱**：`RepoDigests` 非空檢查 + DB CHECK 把「可重新 pull」
  從口頭承諾變成不變式。連既有的外部 Registry 路徑也一起受惠。
- 匯流點設計讓新路徑的增量只有 LOAD + PUSH 兩個 stage；validate／run／MCP／Console 觀測全部零改動。
- 上傳的 image 是**離線可得**的：外部 Registry 掛掉不影響已上傳的版本（反向也成立，風險分散了）。

### 負面（誠實列出）

- **長駐服務 4 → 5。** ADR-0005 的服務清單被修訂。這是真實的複雜度增加，不是「幾乎沒成本」。
- **備份範圍擴大。** `pgdata` 不再足以還原平台狀態；`registrydata` 被刪 = 上傳來源的
  `PUBLISHED` 版本永久不可執行。MVP **不提供自動備份**，只在文件寫清楚。
- **沒有 GC。** validate 失敗的上傳會在 Registry 裡留下永久 blob（`delete.enabled=false`，
  連手動刪都要走停機 + `registry garbage-collect`）。成長是線性且由人手驅動的，
  但**沒有上限保護**——磁碟滿了會表現為 `BUILD_FAILED / stage=PUSH`。
- **`docker load` 是一個新的攻擊面。** 解 tar 的是 daemon（root）。MVP 的信任模型是
  「只跑自己信得過的程式碼」，所以接受；同時要求 `api` 與 `worker` **不得自行用
  Python `tarfile` 解析使用者的 archive**——那會把解析器搬進平台行程。要 image 資訊就 load 完問 daemon。
  跑不可信的 image 之前，這一條必須重評。
- **216 MB 級別的上傳是真的慢。** 沒有斷點續傳、沒有分塊、沒有壓縮協商。
  上傳中斷 = 整包重來，而且失敗後必須換一個新 `semver`（`BUILD_FAILED` 不可重試原請求）。
- **`localhost:5000` 會出現在 `image_ref` 裡**，被印在 Console 與 API 回應上。
  它對使用者沒有意義（他們連不到），只是平台內部的位址。這是為了維持單一 `image_ref` 格式付出的醜陋代價。
- `127.0.0.1:5000` 對**主機上的任何行程**可見。單機單租戶下可接受（且比現行 postgres 的
  `0.0.0.0:5432` 緊），但這不是「沒有暴露」。

### 何時該重新檢討

- **要多機部署，或 Registry 要被第二台機器存取**：TLS + 認證從「不成立」變成「必要」，
  `localhost:5000` 這個名字也會失效（`image_ref` 已寫進 DB，會是一次痛苦的遷移）。先評 k3s + 外部 Registry。
- **出現第二個租戶**：Registry 沒有認證＝沒有租戶隔離，任何人只要能碰到 daemon 就能覆寫 tag。
- **要跑不可信的 image**：`docker load` 的攻擊面與缺少簽章／掃描會變成主要風險，回去讀 ADR-0001。
- **Registry volume 成長到需要配額或自動 GC**：那時要先做「版本刪除」這個產品功能
  （現在沒有），GC 才有正確的判斷依據——沒有刪除語意就做 GC，等於發明一個會刪掉 `PUBLISHED` 版本的定時炸彈。
- **需要斷點續傳或 > 2 GiB 的上傳**：那要獨立的 upload session 資源
  （`POST /v1/uploads` → `upload_id`，再由 JSON 註冊引用），屆時 §3「共用端點」的決定要重評。
- **上傳路徑的使用率接近零**：把它砍掉，回到純 ADR-0008，並把 registry 從 compose 拿掉。
  多留一個沒人用的服務比少一個功能貴。


---

# 修訂 R2（2026-09-23）：改為同機直接 `docker push`，撤回 tar 上傳

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-23 |
| 決策者 | @architect（依使用者明確裁示：允許直接 push，但**只限從跑平台的那台主機**） |
| 影響 | 推翻 R1 §3 路徑 B、R1 §4；改寫 R1 §5 §6。R1 §1 §2 §7 **維持有效** |

## R2 背景

R1 的假設是「**使用者永遠不會直接接觸 Registry**」（見上方「正面回應」一節的原文）。
使用者裁示推翻了這個假設：**允許使用者直接 `docker push` 到內部 Registry，範圍限定只從跑平台那台機器推。**

促成改變的是一次事實查證。使用者原本的描述是「像 AWS 或 Azure 那樣上傳 image 建立獨立 agent」，
但 **AWS 與 Azure 都不收 `docker save` tar**：ECS / Fargate / App Runner / Lambda container
一律是 `docker push` 到 **ECR**；Azure Container Apps / ACI 是 push 到 **ACR**。
它們給使用者的「上傳感」來自**託管一個 Registry**，不是接收檔案。
使用者看到這個事實後改選了同一條路。**我們原本就已經有那個 Registry 了**（R1 §1 已裁定），
所以這件事的增量幾乎為零。

### 三個已確認的技術事實（R2 的立論基礎）

1. **R1 的 `127.0.0.1:5000` 綁定本來就允許同機 push。** R1 選 loopback 是為了拓撲
   （daemon 不在 `zentra-net` 上），不是為了擋人。所以 R2 **是政策改變，不是拓撲改變**：
   compose 一個字都不必為了「允許 push」而改。
2. **同機 push 不需要認證，也不牽動 P4。** 綁 `127.0.0.1` 時只有該主機上的行程連得到；
   能 `docker push` 的人本來就能碰 `docker.sock`（等同主機 root）。在這個前提下加一組帳密
   不增加任何安全性，只增加一個要保管的長期憑證。Docker 對 `localhost` 開頭的 registry
   預設允許明文 HTTP，**不需要改 `daemon.json`、不需要 TLS**。
3. **直接 push 這條路徑的後端增量是零行程式碼**（@architect 讀碼確認，2026-09-23）：
   - `apps/api/schemas.py` 的 `parse_image_ref` 已接受 `localhost:5000/<repo>@sha256:<64hex>`
     （它要求 repository 含 `/`、全小寫、最後一段無冒號——`localhost:5000/x/y` 全部符合）。
   - `apps/sandbox/drivers/docker_driver.py` 的 `_registry_for()` 已把 `localhost` 當成 registry host，
     在憑證檔找不到對應 entry 時回 `None`，`_pull_exact` 因此以**不帶 auth** 的方式 pull——正確行為。
   - `_inspect_object` 的 `RepoDigests` 字面比對對 `localhost:5000/...` **成立**，
     因為 Docker 只對預設 registry（`docker.io`）做正規化，不動非預設 registry 的前綴。

第 3 點是決定性的：**使用者要的功能，在 `registry` 服務起來之後就已經可用了。**
R2 要做的不是「實作 push」，而是「**把它宣告為受支援的路徑並寫進文件與 UI**」。

## R2 選項

### 選項 R2-a：兩條都做（直接 push ＋ Console 上傳 tar）

- 好處：覆蓋面最廣。完全不熟 Docker CLI 的人也能發布。
- 代價（照 R1 的設計逐項點名，這些都是**真的要寫、要測、要維運**的）：
  multipart 串流端點與 2 GiB 上限、磁碟餘裕預檢與 `429`、`uploads` volume 與孤兒檔清理、
  `LOAD`／`PUSH` 兩個 build stage 與它們的冪等與 `finally` 刪檔、
  `image_ref`／`image_digest`／`version_id` 三欄 nullable 化 ＋ 兩條條件式 CHECK ＋ migration、
  `artifact.origin` 欄位與「不得依它分支」的護欄測試、
  Console 的第二種模式（檔案選擇、`XMLHttpRequest` 進度、取消、null digest 呈現）、
  以及 ZEN-009 §7.3／§7.4 約三十條對應驗收。
- **否決理由：代價不變，但它買到的東西縮水了。** 直接 push 之後，上傳 tar 唯一剩下的理由是
  「給不熟 Docker 的人」——而這個理由撐不住：要產出 `docker save` 的 tar，你必須**已經**裝了 Docker、
  **已經**跑過 `docker build`。`docker save -o a.tar img` ＋ 開瀏覽器上傳，跟
  `docker tag` ＋ `docker push` 相比，差的是一個指令，不是一個技能等級。
  **沒有一種使用者是會前者、不會後者的。**

### 選項 R2-b：只做直接 push，撤回 tar 上傳（採用）

- 好處：
  1. **後端增量歸零**（見上方事實 3）。ZEN-009 從「一個跨四個角色的功能」縮成
     「加一個 compose 服務 ＋ 一段說明文字」。
  2. **資料模型變強而不是變弱**：`image_ref`／`image_digest`／`version_id` 維持 `NOT NULL`。
     `NOT NULL` 嚴格強於 R1 那條 `status <> 'PUBLISHED' OR (… IS NOT NULL)` 的條件式 CHECK，
     而且不必讓 API serializer、Console、MCP、Run 路徑全部學會處理 `null`。
  3. **`docker push` 客觀上比 HTTP 上傳好**：layer 去重（第二次只傳改動的層）、並行傳輸、
     失敗可重試、不需要先在本機生一個 216 MB 的 tar。R1 自己列的「216 MB 沒有續傳」這條負面後果，
     在 R2 直接消失。
  4. 整個平台只剩**一條**版本進場路徑（JSON digest 註冊）。一條路 = 一套測試、一種失敗模式。
- 代價（誠實列出，見「R2 後果」）：
  發布需要**在平台主機上有 shell**；平台失去「所有 image 都經過 API」的單一受控入口。

### 選項 R2-c：只做 push，tar 上傳「延後到後續版本」

- 好處：看起來比較溫和，不用現在下結論。
- **否決理由：延後不是免費的。** 留在規格裡的「之後要做」會持續影響現在的設計——
  為了將來的上傳，三個欄位得先 nullable、`artifact.origin` 得先留著、契約得先寫進兩種
  `Content-Type`。那正是 R2-b 想拿掉的成本，而我們一分錢好處都還沒拿到。
  **要就現在做，不做就現在撤。** 之後真的需要，它會以新 ADR 的身分回來，並被重新定價
  （那時的價格會比現在低，因為 Registry 已經在了）。

## R2 決定

### R2-1. 受支援的發布路徑（唯一一條，兩個 image 存放地點）

版本進場**只有一個形狀**：`POST /v1/agents/{agent_id}/versions` + `application/json` +
canonical digest reference。image 本體可以放在兩種地方，平台**不區分、不記錄、不分支**：

| image 放哪裡 | 使用者怎麼放進去 | `image_ref` 長相 |
|---|---|---|
| 外部 Registry（GHCR / 自建 / …） | 自己 `docker push` 到那裡（ADR-0008，不變） | `ghcr.io/acme/agent@sha256:…` |
| **內部 Registry（R2 新增的受支援用法）** | **在平台主機上** `docker push localhost:5000/<repo>:<tag>` | `localhost:5000/<repo>@sha256:…` |

受支援的完整流程（寫進 README、維運說明與 Console 說明區塊）：

```bash
# 1. 在跑 Zentra 的那台主機上（不是遠端筆電）
docker tag my-agent:dev localhost:5000/my-agent:1.0.0
docker push localhost:5000/my-agent:1.0.0
#    push 最後一行會印出： 1.0.0: digest: sha256:<64 hex> size: <n>

# 2. 拿 digest（上一步印出的，或事後查）
docker inspect --format='{{index .RepoDigests 0}}' localhost:5000/my-agent:1.0.0

# 3. 用既有的 JSON 端點註冊（Console 的版本註冊頁貼同一個字串即可）
curl -X POST http://localhost:8000/v1/agents/<agent_id>/versions \
  -H 'Content-Type: application/json' \
  -d '{"semver":"1.0.0","image_ref":"localhost:5000/my-agent@sha256:<64 hex>"}'
```

硬規則（不得放寬，放寬需新 ADR）：

1. **只限同機。** 唯一受支援的 push 來源是跑 compose 的那台主機上的 Docker daemon / CLI。
2. **port binding 維持 `127.0.0.1:5000:5000`，不得綁 `0.0.0.0`**（R1 §1 這條不變，且現在它同時是
   存取控制：loopback 綁定本身就是「只限同機」的執行手段）。
3. **不加 TLS、不加認證、不加帳號**。在只限同機的前提下它們買不到任何東西（R2 背景事實 2）。
4. **要從遠端 push 需要新 ADR**，且該 ADR **必須先解決 TLS 與認證**才能討論其他事。
   不得以「先開 `0.0.0.0` 之後再補 TLS」的順序進行——那個「之後」不會到來。
5. 推薦（**不強制、平台也無法強制**）tag 用 semver：`localhost:5000/<name>:<semver>`。
   理由見 R2-4 的 `--delete-untagged` 陷阱。

### R2-2. 撤回 Console 上傳 tar

R1 §3 的路徑 B、R1 §4 整節、`LOAD` 與 `PUSH` 兩個 build stage，**全部撤回，不是延後**。
平台**不接收 image 檔案**，`api` 與 `worker` 都沒有任何處理使用者上傳 archive 的程式碼路徑。
這順帶把 R1 那條「平台行程不得用 Python `tarfile` 解析使用者 archive」的顧慮從「要守的規則」
降級成「沒有機會發生」——**移除一個攻擊面，永遠好過守住它**。

### R2-3. 契約回退（`artifact.origin` 取消）

`artifact.origin` / `agent_versions.image_origin` **取消，不改值域、不改語意、直接不存在**。理由：

- 它唯一的用途是區分兩種**進場機制**。R2 之後只有一種機制，欄位永遠是同一個值。
  **在 API 回應裡放一個恆定值，等於邀請客戶端對它分支**——而 R1 §3 自己就禁止分支。
- 若改定義成「image 在外部還是內部 Registry」，它就變成**從 `image_ref` 衍生**的值
  （看第一段是不是 `localhost:5000`）。衍生值存進 DB 會有一天和來源不一致。
  要顯示徽章，Console 自己從 `image_ref` 判斷即可——那是呈現，不是契約。
- **契約穩定性沒有受損**：`artifact.origin`、`details.stage` 的 `LOAD`/`PUSH`、
  `QUOTA_EXCEEDED` 的磁碟語意，都是 v0.4.0 加進契約但**從未實作**的（`apps/` 全文檢索為零命中）。
  相對於**實際跑著的** v0.3.1 契約，R2 是零變更。這是在墨水乾掉之前擦掉，不是撕毀已發布的契約。

連帶回退：`image_ref` / `image_digest` / `version_id` 維持 **`NOT NULL`**；
R1 §2 那條條件式 CHECK 不需要（`NOT NULL` 嚴格更強）；
`details.stage` 值域退回 `PULL | INSPECT | VALIDATE`；
`QUOTA_EXCEEDED` 退回只表示併發 Run 上限。**ZEN-009 不需要任何 migration。**

### R2-4. 失去單一受控入口之後，容量與 GC 的責任歸屬

R1 的設計裡，每個 image 都經過 API 進來，平台因此知道 Registry 裡有什麼。
**R2 放棄了這個性質**，必須誠實處理：

1. **容量是維運者（主機擁有者）的責任，不是平台的。** 平台不做配額、不做磁碟預檢、
   不對 Registry 的內容負責。這不是偷懶，是**平台已經不在那條路徑上**——
   `docker push` 是使用者的 CLI 和主機 daemon 之間的事，`api` 與 `worker` 完全看不到它。
   宣稱能管理自己看不到的東西才是不誠實。
2. **磁碟滿掉的表現**：使用者的 `docker push` 在 client 端失敗（registry 自己的錯誤訊息），
   平台**不會**顯示任何東西，因為平台不知道有人在 push。
   已 `PUBLISHED` 的版本不受影響（它們的 blob 已經在裡面）。這是可接受的退化，寫進 NFR。
3. **未被任何 `agent_versions` 引用的 repository：平台不刪、不列、不管。** 理由：
   - 平台無從判斷那是垃圾還是「使用者五分鐘後要註冊的東西」，或他刻意放在那裡的 base image。
   - R1 已經裁定「**沒有版本刪除語意就做 GC，等於發明一個會刪掉 `PUBLISHED` 版本的定時炸彈**」。
     R2 之後平台連寫入者都不是，**更沒有資格代替使用者刪東西**。
   - `REGISTRY_STORAGE_DELETE_ENABLED=false` 維持寫死：連誤刪都做不到。
4. **回收是維運程序，不是產品功能。** @platform-eng 要在維運說明寫下這段程序：
   以 `GET /v2/_catalog` 列出 repository → 以 SQL 取出 `agent_versions` 中所有
   `status='PUBLISHED'` 的 `image_digest` → 比對 → **只有兩邊都確認過的 repository 才可以動手**，
   且必須停機、暫時開啟 delete、`registry garbage-collect`、關回去。
5. **⚠️ 陷阱，必須寫進維運說明**：`registry garbage-collect` 的 **`--delete-untagged` 絕對不准用**。
   使用者用同一個 tag（例如 `:latest`）推第二次，第一次的 manifest 會變成 untagged——
   但它可能正是某個 `PUBLISHED` 版本的 `image_digest`。`--delete-untagged` 會把它刪掉，
   那個版本立刻永久不可執行。這也是 R2-1 推薦「tag 用 semver」的原因：
   讓每個被註冊過的 manifest 都留著一個 tag。

### R2-5. Registry 原生端點（`/v2/_catalog` 等）：**不遮蔽**，但平台不得代理

裁決：**不做任何遮蔽**。理由：

- 能連到 `127.0.0.1:5000` 的，就是能在該主機上執行程式的人；而在單機單租戶下，
  那群人**同時也是能碰 `docker.sock`（等同主機 root）的人**。對主機 root 隱藏 catalog 是表演，不是控制。
- 要遮蔽就得在 Registry 前面加一個反向代理或 auth middleware——**那是多一個要跑、要設定、
  要測的元件**，用來防禦一個不存在的威脅。這正是 ADR-0005 要砍掉的那種東西。
- 遮蔽還會**打掉 R2-4 第 4 點的回收程序**，那個程序需要 `_catalog`。

真正要守住的是另一條，它是硬規則：

> **平台（`api` / `worker` / Console / MCP）不得代理、轉發、嵌入或連結任何 Registry HTTP 端點。**
> Registry 的 HTTP API 是**維運介面**，不是產品介面。它不出現在 `api` 的任何路由上、
> 不出現在 Console 的任何 `fetch` 裡、不出現在 OpenAPI 或 MCP tool schema 裡。

這條存在的原因很具體：`api` 同時掛在 `zentra-net` 與 `zentra-sandbox-net` 上。
`api` 上任何一個「幫你查 registry」的端點，都會變成沙盒觸達 Registry 的跳板，直接打穿 P4。
**Registry 對沙盒不可達這件事，必須靠「沒有任何一條路可以到」來保證，不是靠沙盒不知道位址。**

（`registry` 服務仍只掛 `zentra-net`，且**絕對不接 `zentra-sandbox-net`**——R1 §1 這條不變。
順帶記錄一個 R2 才看清的事實：**沒有任何平台服務會連到 registry**。
它的 client 只有主機 daemon 與使用者 CLI，兩者都走 loopback。
因此 `api` / `worker` **不需要** `depends_on: registry`。）

### R2-6. `image_ref` 正規化的護欄（給 Docker Hub 缺陷的修正用）

BOARD 記錄了一個 ZEN-008 既有缺陷：`apps/sandbox/drivers/docker_driver.py` 以**字面字串**比對
`image_ref` 與 `RepoDigests`，而 Docker 會把 `docker.io/` 前綴正規化掉，導致 Docker Hub 的 image
一個都註冊不了。**修正本身屬 @platform-eng 的獨立任務**，但它會直接碰到 R2 依賴的不變式，
因此在此先把護欄釘死：

1. **正規化只能是「比較時」的操作，絕不可改寫存進 DB 的 `image_ref`。**
   `agent_versions.image_ref` 必須原封不動保存呼叫端註冊的字串——它是 P5 的身份證，
   也是事後稽核「當初註冊的到底是什麼」的唯一依據。
2. **只有預設 registry 會被正規化。** 判斷規則照抄 `docker_driver.py` 既有的 `_registry_for()`：
   repository 的第一段若含 `.` 或 `:`，或等於 `localhost`，那一段就是 **registry host，必須原樣保留**。
   只有不符合這個條件的第一段才是 `docker.io` 的隱含前綴（且單段名稱隱含 `library/`）。
3. 因此 `docker.io/library/alpine` ≡ `docker.io/alpine` ≡ `alpine`，
   但 `localhost:5000/alpine` **不等於**上面任何一個。
   **任何把 `localhost:5000/x` 正規化成 `x` 或 `docker.io/x` 的實作都是嚴重 bug**，
   會讓內部 Registry 的版本比對到錯的 image。QA 必須有一條測試釘死這個否定案例。
4. 若修正順帶放寬 `parse_image_ref`（目前要求 repository 含 `/`，這正是短格式被擋的原因），
   放寬後仍**必須**拒絕 tag-only reference 與非 64 hex 的 digest。ADR-0008 的 digest-only 身份不鬆動。

### R2-7. R1 中維持有效的部分（逐條點名，免得下一個人要猜）

- **R1 §1**：內部 Registry 為第 5 個長駐服務、`registry:2`、只掛 `zentra-net`、
  **絕不接 `zentra-sandbox-net`**、只發布 `127.0.0.1:5000:5000`、`delete.enabled=false`、
  `registrydata` volume、「daemon 與 compose 同機」的前提。**一個字都沒改。**
- **R1 §2**：`version_id` = manifest digest 前 12 碼；「沒有可重新 pull 的來源就不得 `PUBLISHED`」。
  R2 **加強**了它：因為沒有上傳路徑，就沒有「本機有 image 但 registry 沒有」的狀態可言，
  三個欄位維持 `NOT NULL`，不變式由型別系統保證而不是由條件式 CHECK 保證。
- **R1 §5 的核心結論**：**不新增任何錯誤 code。**（v0.4.0 對 `stage` 與 `QUOTA_EXCEEDED` 的兩處放寬撤回。）
- **R1 §7（Q-008 / Q-009 / Q-010）全部維持**：`build(ctx)` 簽章與 arity 預檢、
  SDK 自行宣告依賴 + import 自檢、官方 base image `zentra/agent-base:1.0`。
  這三項與進場路徑無關，且在 R2 之後**更重要**——使用者現在必然是手工做 image 的人。
- **R1 對 ADR-0005 的修訂（4 → 5 個長駐服務）維持**。R2 沒有把服務數降回去：
  Registry 仍然在，只是它的 client 從「平台 worker」換成「使用者的 CLI」。

## R2 後果

### 正面

- **ZEN-009 的實作量掉了一個數量級。** 從「multipart 端點 + migration + 兩個 build stage + 新 UI 模式 +
  三十條驗收」縮成「compose 加一個服務 + 一段說明文字 + 維運程序」。八週 MVP 的敵人是範圍。
- **資料模型比 R1 乾淨**：三個關鍵欄位維持 `NOT NULL`，沒有 `BUILDING` 期間的 null 視窗，
  API / Console / MCP 都不必處理「digest 還不知道」這個狀態。
- **少一個攻擊面**：平台不接收檔案，就沒有 multipart 串流、暫存檔、磁碟耗盡、
  `tarfile` 解析這一整類問題。
- **使用者體驗實際上更好**：layer 去重、可重試、不必先產生 216 MB 的 tar。
- **與業界一致**：ECR / ACR 的心智模型就是 push。使用者不需要學一套 Zentra 專用的發布方式。

### 負面（誠實列出）

- **發布需要在平台主機上有 shell。** 這是 R2 最真實的代價。只有瀏覽器、沒有主機存取權的人
  **無法發布版本**。在 MVP 的交付條件下（單租戶、內部工具、開發者就是維運者）這成立；
  它一旦不成立，見「何時該重新檢討」。
- **平台失去單一受控入口。** Registry 裡會有平台不知道的 repository，平台**永遠不會知道**。
  容量、清理、備份全部落到維運者身上（R2-4）。R1 至少還能在上傳時做磁碟預檢，R2 連這個都沒有。
- **磁碟滿了平台沒有任何訊號。** 使用者的 push 在 client 端失敗，Console 上什麼都看不到。
- **`_catalog` 對主機上任何行程可見**，我們選擇不遮蔽（R2-5）。這不是「沒有暴露」，
  是「暴露給一群本來就有主機 root 的人」。
- **tag 可被覆寫。** 使用者可以對同一個 tag 推兩次。對 P5 **沒有影響**（註冊的是 digest，
  舊 manifest 仍以 digest 可定址），但它讓 `--delete-untagged` 變成一個會炸掉 `PUBLISHED`
  版本的指令（R2-4 第 5 點）。
- **`localhost:5000` 出現在 `image_ref` 裡**這個 R1 的負面後果**維持**，而且現在是使用者自己打的字，
  更容易被誤會成可以從別台機器 `docker pull` 的位址。
- **撤回了一個已經寫進契約的欄位。** `artifact.origin` 存在於 api-contract v0.4.0。
  它從未被實作，所以沒有破壞任何執行中的客戶端——但這仍然是一次規格反覆，
  記在這裡是為了讓下一次「先寫契約再確認需求」的衝動慢一點。

### 何時該重新檢討

- **出現「只有瀏覽器、沒有主機 shell」的發布者**（例如維運與開發分離、或要給非工程同事用）：
  這是**唯一**能讓 tar 上傳回來的理由。屆時以新 ADR 重新定價——好消息是 Registry 已經在了，
  R1 §3 §4 的設計可以直接抄回來，成本比現在低。
- **要從遠端主機 push**：TLS 與認證從「不成立」變成**前置條件**，必須先解決才能開 `0.0.0.0`。
  順序不可顛倒。同時 `localhost:5000` 這個已經寫進 DB 的名字會失效，是一次痛苦的遷移。
- **出現第二個租戶**：無認證的 Registry 沒有租戶隔離，任何能碰主機的人都能覆寫任何 tag。
- **要跑不可信的 image**：沒有簽章、沒有掃描、`docker.sock` 等同 root，回去讀 ADR-0001。
- **Registry 的垃圾成長到維運者受不了**：先做「版本刪除」這個產品功能，再做 GC。順序不可顛倒。
- **使用者開始用遠端 `DOCKER_HOST`**：`localhost:5000` 會指向錯的機器，push 成功、pull 失敗。
  那已經不是 ADR-0005 的單機部署了。
