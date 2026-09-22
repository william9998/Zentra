# ADR-0009：內部 OCI Registry 與 Console 上傳 image tar

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-22 |
| 決策者 | @architect（依使用者明確裁示：三條路中選路 2） |
| 相關 | **推翻 [ADR-0008](ADR-0008-oci-image-agent-packaging.md) 對選項 B 的否決**；修訂 [ADR-0005](ADR-0005-mvp-single-host-scope.md) 的長駐服務數；P1–P5 全部不動 |

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
push 成功、且 `docker image inspect` 的 `RepoDigests` 確實含有我們預期的
`<repo>@sha256:<digest>` 之前，`image_ref`／`image_digest`／`version_id` 一律是 `NULL`，
版本停在 `BUILDING`。這條規則直接用 DB CHECK 約束表達：

```sql
CHECK (status <> 'PUBLISHED'
       OR (image_ref IS NOT NULL AND image_digest IS NOT NULL AND version_id IS NOT NULL))
```

「`RepoDigests` 必須非空」就是把那個實測結果寫成不變式：
**沒有可重新 pull 的來源，就不是一個發布過的版本。**

### 3. 上傳走既有端點，不另開端點

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

### 4. tar 落地在哪：共享 volume，用完即刪，絕不進 PostgreSQL

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

- `error.details.stage` 的值域從 `PULL | INSPECT | VALIDATE` **擴充**為
  `LOAD | PUSH | PULL | INSPECT | VALIDATE`。這是既有欄位的值域擴充，不是新 code。
- `QUOTA_EXCEEDED` 的說明從「超過併發 Run 上限」**放寬**為「超過資源上限（併發 Run 或磁碟餘裕）」，
  上傳前磁碟預檢失敗時回 `429 QUOTA_EXCEEDED` + `Retry-After`，`details.reason="insufficient_disk"`。

刻意**不**新增 `PAYLOAD_TOO_LARGE`／`UNSUPPORTED_MEDIA_TYPE`／`REGISTRY_ERROR`：
前兩者是 `VALIDATION_ERROR` 的特例（`details` 已足以區分），第三者是 `BUILD_FAILED` 的階段
（`details.stage=PUSH`）。錯誤碼枚舉每多一個，前端、SDK、QA 就多一條分支。

### 6. 兩條路徑並存的規則

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
