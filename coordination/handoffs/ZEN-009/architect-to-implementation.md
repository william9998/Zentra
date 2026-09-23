# 交接單 Handoff — ZEN-009（**第二版裁決 v2，取代 2026-09-22 的第一版**）

**任務**：`ZEN-009` 內部 Registry — 讓使用者在平台主機上 `docker push` 建立 Agent 版本
**從**：@architect　**給**：@platform-eng（主要）、@qa-verifier、@ui-designer、@frontend-dev、@backend-dev
**日期**：2026-09-23（v1：2026-09-22）
**Spec 版本**：`v0.4.1`（api-contract 同步 `v0.4.1`）

---

## 0. 這是第二版裁決，v1 的實作指示全部作廢

> **v1（2026-09-22）給的指示**：Console 收 `docker save` tar → `api` multipart 串流落檔 →
> worker `LOAD` → `PUSH` → 取 manifest digest → 接既有流程。含 migration、
> 三欄 nullable 化、`artifact.origin`、`uploads` volume、新 UI 模式。
>
> **⚠️ 上面那一段全部不要做。** 使用者在 2026-09-23 裁示改變了發布路徑的形狀。
> 若你已經照 v1 開工，請停手並回報已寫的部分 —— 不要「順手留著」。

**v2 的形狀**：使用者**在跑平台的那台主機上**
`docker push localhost:5000/<repo>:<tag>` → 取得 digest →
用**本來就有的** `POST /v1/agents/{id}/versions`（JSON，填 `image_ref`）註冊 → 走既有流程。

**裁定：ZEN-009 維持 `SPEC_READY`**，且 **v1 的兩個開工閘門都解除**
（不再需要 ui-contract v0.6.0；@platform-eng 也不再是 @backend-dev 的前置）。

## 1. 使用者裁示與促成改變的事實

使用者裁示：**允許使用者直接 `docker push` 到內部 Registry，範圍限定只從跑平台那台機器推。**
原本 spec §2 寫的「不對外發布 Registry port，不讓使用者直接 `docker push`」——
**前半（不綁 `0.0.0.0`）維持，後半（不讓使用者 push）撤銷。**

促成改變的是一次事實查證：使用者原本要的是「像 AWS 或 Azure 那樣上傳 image」，
但 **AWS 與 Azure 都不收 `docker save` tar**：ECS / Fargate / App Runner / Lambda container →
**ECR**；Azure Container Apps / ACI → **ACR**。它們給使用者的「上傳感」來自**託管一個 Registry**，
不是接收檔案。使用者看到後改選了同一條路。**我們 R1 已經裁定要那個 Registry 了**，所以增量幾乎為零。

三個當事實用的技術點（前兩點由主 session 實測，第三點由 @architect 讀碼確認）：

1. **R1 的 `127.0.0.1:5000` 綁定本來就允許同機 push。** 所以 v2 是**政策改變，不是拓撲改變**。
2. **同機 push 不需要認證，也不牽動 P4。** 綁 `127.0.0.1` 時只有該主機上的行程連得到，
   而那群人本來就能碰 `docker.sock`（等同主機 root）。Docker 對 `localhost` 開頭的 registry
   預設允許明文 HTTP，**不必改 `daemon.json`、不必 TLS**。
3. **直接 push 這條路徑的後端增量是零行程式碼**：
   - `apps/api/schemas.py` 的 `parse_image_ref` 已接受 `localhost:5000/<repo>@sha256:<64hex>`
     （它要求 repository 含 `/`、全小寫、最後一段無冒號 —— `localhost:5000/x` 全部符合）。
   - `apps/sandbox/drivers/docker_driver.py` 的 `_registry_for()` 已把 `localhost` 當 registry host，
     憑證檔找不到對應 entry 時回 `None`，`_pull_exact` 因此以**不帶 auth** 的方式 pull —— 正確行為。
   - `_inspect_object` 的 `RepoDigests` 字面比對對 `localhost:5000/...` **成立**
     （Docker 只正規化預設 registry `docker.io`，不動非預設 registry 的前綴）。

→ **使用者要的功能，在 `registry` 服務起來之後就已經可用了。**
ZEN-009 要做的不是「實作 push」，而是「把它宣告為受支援的路徑，並寫進 compose、文件與 UI 說明」。

## 2. 四個裁決

### 裁決 1：直接 push 是受支援的路徑（只限同機）

受支援的四步流程（寫進 README、維運說明、Console 說明區塊）：

```bash
docker tag my-agent:dev localhost:5000/my-agent:1.0.0    # 推薦 tag 用 semver，理由見裁決 3
docker push localhost:5000/my-agent:1.0.0                 # 最後一行印出 digest: sha256:<64 hex>
docker inspect --format='{{index .RepoDigests 0}}' localhost:5000/my-agent:1.0.0
curl -X POST http://localhost:8000/v1/agents/<id>/versions \
  -H 'Content-Type: application/json' \
  -d '{"semver":"1.0.0","image_ref":"localhost:5000/my-agent@sha256:<64 hex>"}'
```

硬規則（不得放寬）：**只限同機** · **port binding 維持 `127.0.0.1:5000:5000`，不得綁 `0.0.0.0`** ·
**不加 TLS、不加認證** · **要遠端 push 需新 ADR，且該 ADR 必須先解決 TLS 與認證**
（不得以「先開 `0.0.0.0` 之後再補 TLS」的順序進行 —— 那個「之後」不會到來）。

### 裁決 2：tar 上傳 —— **撤回，不是延後**（選項 b）

直接 push 之後，上傳 tar 唯一剩下的理由是「給不熟 Docker 的人」，而**這個理由撐不住**：
要產出 `docker save` 的 tar，你必須**已經**裝了 Docker、**已經**跑過 `docker build`。
`docker save -o a.tar img` + 開瀏覽器上傳，跟 `docker tag` + `docker push` 相比，
差的是**一個指令，不是一個技能等級**。沒有一種使用者是會前者、不會後者的。

它的價格卻一分沒少：multipart 串流端點、2 GiB 上限、磁碟預檢與 `429`、`uploads` volume 與
孤兒檔清理、`LOAD`/`PUSH` 兩個 stage 與冪等與 `finally` 刪檔、三欄 nullable 化 + 兩條條件式
CHECK + migration、`artifact.origin` 與「不得依它分支」的護欄測試、Console 第二種模式
（檔案選擇 / `XMLHttpRequest` 進度 / 取消 / null digest 呈現）、約三十條對應驗收。

**撤回還順帶買到三樣東西**：

1. **資料模型變強**：`image_ref` / `image_digest` / `version_id` 維持 `NOT NULL`，
   嚴格強於 v1 的條件式 CHECK，而且 API serializer / Console / MCP / Run 路徑都不必學會處理 `null`。
2. **少一個攻擊面**：平台不接收檔案，就沒有 multipart 串流、暫存檔、磁碟耗盡、`tarfile` 解析。
   v1 那條「平台行程不得解析使用者 archive」從「要守的規則」降級成「沒有機會發生」。
3. **`docker push` 客觀上比 HTTP 上傳好**：layer 去重、並行、可重試、不必先產生 216 MB 的 tar。
   v1 自己列的「216 MB 沒有續傳」這條負面後果直接消失。

**為什麼不是「延後」**：延後不免費。留在規格裡的「之後要做」會影響現在的設計 ——
三欄得先 nullable、`origin` 得先留著、契約得先寫兩種 `Content-Type`。
那正是要拿掉的成本，而好處一分沒拿到。**要就現在做，不做就現在撤。**

**什麼情況該改選回來**（唯一站得住腳的理由）：
出現「**只有瀏覽器、沒有主機 shell**」的發布者（維運與開發分離，或要給非工程同事用）。
屆時以新 ADR 重新定價 —— 好消息是 Registry 已經在了，v1 的設計可以直接抄回來，成本比現在低。

### 裁決 3：失去單一受控入口之後，責任歸屬

平台**不在 push 路徑上**，`api` 與 `worker` 看不到任何 push。因此：

1. **容量與清理是維運者的責任。** 平台不做配額、不做磁碟預檢、不對 Registry 內容負責。
   宣稱能管理自己看不到的東西才是不誠實。
2. **磁碟滿掉的表現**：使用者的 `docker push` 在 client 端失敗，**平台不會顯示任何訊號**。
   已 `PUBLISHED` 的版本不受影響。已寫進 SPEC §10 NFR。
3. **未被任何 `agent_versions` 引用的 repository：平台不刪、不列、不管。**
   無從分辨那是垃圾、還是使用者五分鐘後要註冊的東西、還是他刻意放的 base image。
   R1 已裁定「沒有版本刪除語意就做 GC 等於發明定時炸彈」；v2 之後平台連寫入者都不是，
   **更沒有資格代替使用者刪東西**。`REGISTRY_STORAGE_DELETE_ENABLED=false` 維持寫死。
4. **回收是維運程序**：`GET /v2/_catalog` ↔ `agent_versions` 中 `PUBLISHED` 的 `image_digest` 對照
   → 只動兩邊都確認過的 → 停機 → 暫時開啟 delete → `registry garbage-collect` → 關回去。
5. ⚠️ **`registry garbage-collect --delete-untagged` 絕對不准用。**
   使用者用同一個 tag 推第二次會讓前一個 manifest 變成 untagged，
   而它可能正是某個 `PUBLISHED` 版本的 `image_digest`。這個 flag 會直接讓那個版本永久不可執行。
   **這句話要一字不漏出現在維運說明裡。** 這也是推薦「tag 用 semver」的原因。
6. **`/v2/_catalog` 等原生端點：不遮蔽。** 能連 `127.0.0.1:5000` 的人本來就有主機 root，
   對他們隱藏 catalog 是表演；要遮蔽得加反向代理（多一個元件防一個不存在的威脅），
   而且會打掉第 4 點的回收程序。
   **但有一條硬規則**：平台（`api` / `worker` / Console / MCP）**不得代理、轉發、嵌入或連結
   任何 Registry HTTP 端點**。理由很具體：`api` 同時掛在 `zentra-net` 與 `zentra-sandbox-net` 上，
   任何「幫你查 registry」的端點都會變成沙盒觸達 Registry 的跳板，**直接打穿 P4**。
   已寫成 SPEC §5 分層硬規則 4。

### 裁決 4：`artifact.origin` —— **取消**

只剩一種機制，欄位永遠是同一個值。**在 API 回應裡放一個恆定值，等於邀請客戶端對它分支**，
而 v1 自己就禁止分支。改定義成「外部 vs 內部 Registry」也不行：那是**從 `image_ref` 衍生**的值，
衍生值存進 DB 總有一天會和來源不一致。要顯示徽章，Console 自己從 `image_ref` 判斷即可 ——
那是呈現，不是契約。

連帶撤回：`agent_versions.image_origin` · 三欄 nullable 化 · 條件式 CHECK ·
`details.stage` 的 `LOAD`/`PUSH` · `QUOTA_EXCEEDED` 的磁碟語意。
**ZEN-009 不需要任何 migration。**

> **契約穩定性沒有受損**：這些都是 v0.4.0 寫進契約但**從未實作**的（`apps/` 全文檢索零命中）。
> 相對於實際跑著的 **v0.3.1，v0.4.1 是零變更**。這是在墨水乾掉之前擦掉，不是撕毀已發布的契約。

## 3. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/adr/ADR-0009-internal-registry-image-upload.md` | **加上修訂 R2**（背景 / 三個選項 / 七條決定 / 後果 / 何時重新檢討）。R1 原文保留，被推翻處標 `🔻 R2 撤回`。**檔名刻意不改** —— 已被七處連結，連結穩定性優先 |
| `specs/features/ZEN-009/spec.md` | 大幅改寫為 v2：範圍縮小、明確不做擴充、驗收改寫 |
| `specs/SPEC.md` | v0.4.0 → **v0.4.1**：§1 §1.1 §3 §5（新增硬規則 4）§6 §6.1 §7.1 §8.2 §10 §11 §13 |
| `specs/api-contract.md` | v0.4.0 → **v0.4.1**：撤回 multipart 形狀、`artifact.origin`、digest 三欄 null 語意、`LOAD`/`PUSH`、`QUOTA_EXCEEDED` 磁碟語意 |
| `specs/glossary.md` | 「平台內建 Registry」與「image tar 直傳」拆成兩列，後者標為已撤回 |
| `docs/architecture/oci-image-agent-flow.md` | §上傳路徑 → §內部 Registry，重畫資料流 |
| `docs/architecture/detailed-architecture.md` | 頁首補 v0.4.1 註記 |
| `specs/adr/ADR-0005` / `ADR-0008` | 交叉引用的一句話更新（服務清單註記、選項 B 註記） |

## 4. 下一個人要做什麼

### 4.1 @platform-eng（**這是本任務的主體**）

`compose.yaml` 新增：

```yaml
  registry:
    image: registry:2
    environment:
      REGISTRY_STORAGE_DELETE_ENABLED: "false"
    volumes: [registrydata:/var/lib/registry]
    ports: ["127.0.0.1:5000:5000"]     # 只綁 loopback。這同時就是「只限同機」的執行手段
    networks: [zentra-net]             # 絕對不要接 zentra-sandbox-net
```

- 新增 volume：`registrydata`。**不要 `uploads` volume**（v2 撤回）。
- **不要加 `depends_on: registry`**：`api` 與 `worker` 都不連 registry。
  它的 client 只有主機上的 Docker daemon 與使用者 CLI，兩者都走 loopback。
- **不要新增任何 env**：`ZENTRA_INTERNAL_REGISTRY` / `ZENTRA_UPLOAD_DIR` /
  `ZENTRA_MAX_IMAGE_UPLOAD_BYTES` 三個都取消（平台不需要知道 registry 位址，它不連它）。
  host port **固定 5000**，寫死在 compose。
- healthcheck 是可有可無的（沒有服務依賴它）。
  **若 `registry:2` 裡沒有可用的 `wget`/`curl`，不要為此加工具或換基底映像** —— 拿掉比養一個假的好。
- `docker/agent-base.Dockerfile` → `zentra/agent-base:1.0`
  （Python 3.12 + `zentra_sdk` + langgraph + `/opt/zentra/bin/zentra-agent`
  + label `io.zentra.image-contract=zentra.image/v1` + `USER 65534:65534`）。
  `examples/demo-echo/Dockerfile` 改成 `FROM zentra/agent-base:1.0` + 一行 `COPY`。
  **不推公開 Registry、不簽章、不多架構、tag 固定 `1.0`。**

**維運說明必須寫**（這是交付物的一部分，不是附帶）：
1. 裁決 1 的四步發布流程，含「**只能在跑 Zentra 的那台主機上執行**」。
2. **`registrydata` 與 `pgdata` 同屬備份範圍**；刪掉 `registrydata` = 那些 `PUBLISHED` 版本永久不可執行。
3. 手動 GC 程序（停機 → 暫時開 delete → `registry garbage-collect` → 關回去）。
4. ⚠️ **`--delete-untagged` 禁令**（裁決 3 第 5 點），一字不漏。
5. catalog ↔ DB 對照程序（裁決 3 第 4 點）。
6. 容量是維運者的責任；磁碟滿了平台沒有訊號。
7. **macOS 的 AirPlay Receiver 會佔用 port 5000**：教使用者關掉它。
   **不要**把 port 做成設定項 —— 那會需要一個端點讓 Console 知道實際值，是為環境細節新增 API 表面。

**已知的坑（會浪費你半天，先讀）**：
- pull 的執行者是**主機上的 Docker daemon**，不是 worker 行程。daemon 不在 `zentra-net` 上，
  **解析不到服務名 `registry`**。所以 image ref 必須用 `localhost:5000`，也因此必須發布 loopback port。
- 選 `localhost` 而不是容器 IP 或自訂 host name，是因為 Docker daemon 對 `localhost`／`127.0.0.0/8`
  **預設允許明文 HTTP**，不必改 `/etc/docker/daemon.json`。換名字 = 多一個部署步驟。
- 前提假設：daemon 與 compose 同一台主機。遠端 `DOCKER_HOST` 會 push 成功、pull 失敗，不在 MVP 範圍。

### 4.2 @qa-verifier（開發前）

寫 `specs/features/ZEN-009/acceptance.md`。**最低必測集合見 feature spec §7**，可加不可減。
架構師特別要求、**缺了就不算通過**的一條：

> **image 被 prune 後仍能按 digest 重新 pull 的證據。**
> push → 註冊 → `PUBLISHED` → `docker image rm` 移除該 image 的**所有**本機參照
> → 確認 `docker image inspect` 找不到 → 觸發一次 Run → **Run 成功** →
> 事後 `RepoDigests` 含有與 DB `image_digest` 完全相同的 digest。
> **要有終端輸出當證據，不接受口頭宣稱。**

v2 新增、容易被忽略的四條：
- **護欄測試（§7.3）**：送 multipart → `422`；回應沒有 `artifact.origin`；
  `apps/**` 全文檢索不得出現 `LOAD`/`PUSH` 作為 build stage、不得出現 `image_origin`、`uploads` volume。
- **`api` 沒有任何代理 Registry 的路由**：全文檢索 `apps/api/**` 與 `apps/console/**`
  不得出現對 `:5000` 或 `/v2/` 的請求；`/openapi.json` 不得出現 registry 路徑。
- **port binding 只有 `127.0.0.1`**：`docker inspect` 的 `HostIp` 必須是 `127.0.0.1`；
  從主機非 loopback IP 連 5000 必須失敗。
- **正規化否定案例**：`localhost:5000/alpine` **不得**被正規化成 `alpine` 或 `docker.io/alpine`。
- **tag 覆寫不影響舊版本**：同一個 tag push 第二個不同內容的 image → 舊 `PUBLISHED` 版本仍能 Run。

### 4.3 @ui-designer（**閘門降級：v0.6.0 → v0.5.2，patch 級**）

**v1 要求的 v0.6.0 大改寫取消。** 現行 §5.4「這是文字表單，**沒有檔案輸入、拖放區、upload progress**」
**仍然正確，不必改** —— 因為 v2 沒有上傳。

只需新增一個**靜態說明區塊**到版本註冊頁，內容要涵蓋：
1. 裁決 1 的四步指令（`tag` → `push` → 取 digest → 貼進表單）。
2. **「只能在跑 Zentra 的那台主機上執行」**，一句話講清楚。
3. `localhost:5000` 是**平台內部位址**，不要當成可以從別台機器 `docker pull` 的東西。
4. 一行說明「外部 Registry vs 內部 Registry 各適合什麼」。

**沒有新元件狀態、沒有檔案輸入、沒有進度條、沒有 `XMLHttpRequest`。**
形式（說明卡片 / details 摺疊 / 附複製按鈕）由你決定。

### 4.4 @frontend-dev（**不再被閘門擋住太久**）

把 §4.3 的說明區塊放進 `version-registration.js`（或對應的 HTML）。純靜態文字。
- **一律 `textContent`**，不要 `innerHTML`。
- 指令字串裡的 `localhost:5000` 可以寫死 —— port 由 spec 固定。
- `202` 之後 `version_id` / `artifact.digest` / `artifact.image_ref` **一定有值**，
  不會是 `null`（v1 說的 null 情況已撤回）。
- 錯誤 code 只會是 `VALIDATION_ERROR` / `VERSION_CONFLICT` / `NOT_FOUND` / `BUILD_FAILED`
  （`details.stage` ∈ `PULL|INSPECT|VALIDATE`）。**沒有 `LOAD`/`PUSH`，沒有新 code。**

### 4.5 @backend-dev（**本任務對 `apps/api/**` 與 `apps/worker/**` 零工作**）

不要寫 multipart 端點、不要寫 migration、不要加 `LOAD`/`PUSH` stage、不要加 `image_origin`。
只剩兩件與 Registry 無關、可完全平行的事（`packages/sdk/**` 是你的）：
- **Q-008**：`validate` mode 在呼叫 `build` 前用 `inspect.signature` 檢查 arity，不符 → `BUILD_FAILED`，
  message 明說「`build()` 必須接受一個 ctx 參數」（不要丟 `TypeError` 堆疊給使用者）。
- **Q-009**：新增 `packages/sdk/pyproject.toml`，宣告 `jsonschema` / `httpx` / `langgraph`
  為 runtime dependency；runtime 啟動先做 import 自檢，缺件時報出**缺哪個模組**。

## 5. 跨任務約束：`image_ref` 正規化（給 Docker Hub 缺陷的修正用）

BOARD 記錄的 ZEN-008 缺陷（`docker_driver.py` 字面比對 `RepoDigests`，Docker Hub 全部註冊不了）
**修正屬 @platform-eng 的獨立任務，不在 ZEN-009 範圍**，但它會動到 ZEN-009 依賴的不變式。
護欄先釘死（已寫進 SPEC §7.1 與 feature spec §5.6）：

1. **正規化只能是「比較時」的操作，絕不可改寫存進 DB 的 `image_ref`。**
   `agent_versions.image_ref` 必須原封不動保存呼叫端註冊的字串 —— 它是 P5 的身份證。
2. **只有預設 registry 會被正規化。** 判斷規則照抄 `docker_driver.py` 既有的 `_registry_for()`：
   repository 第一段含 `.` 或 `:`，或等於 `localhost` → 那一段是 registry host，**必須原樣保留**。
3. `docker.io/library/alpine` ≡ `docker.io/alpine` ≡ `alpine`，
   但 `localhost:5000/alpine` **不等於**上面任何一個。
   **任何把 `localhost:5000/x` 正規化成 `x` 或 `docker.io/x` 的實作都是嚴重 bug** ——
   內部 Registry 的版本會比對到錯的 image。QA 必須有一條測試釘死這個否定案例。
4. 放寬 `parse_image_ref` 時仍必須拒絕 tag-only reference 與非 64 hex 的 digest。

**好消息**：這個缺陷**不擋 ZEN-009**。`localhost:5000/...` 不會被 Docker 正規化，
所以現行字面比對對內部 Registry 是成立的。

## 6. 我沒做的事（誠實列出）

- **沒有寫任何產品程式碼**，也沒有改 `compose.yaml`（那是 @platform-eng 的）。
- **沒有實際跑過一次 `docker push localhost:5000/... → 註冊 → PUBLISHED → Run`。**
  §1 的第 3 點是**讀碼推論**（`parse_image_ref` / `_registry_for` / `_pull_exact` / `_inspect_object`
  四處），不是實測。它是 v2 立論的關鍵假設 —— **@qa-verifier 的 §7.4 就是要驗它**。
  若實測發現 `localhost:5000` 這條路徑其實被擋，請立刻回報，那會改變裁決 2 的成本比較。
- **沒有改 `coordination/BOARD.md`**（紀律）。需要主 session 整合的內容見 §8。
- **沒有改 `ui-contract.md`**（@ui-designer 的）。
- **沒有改 `CLAUDE.md` / `README.md` / `.claude/agents/platform-eng.md`** —— 它們有幾處描述
  `registry` 是「為了支援上傳 image tar」而存在，現在不準確了。見 §8。
- 沒有重畫 `detailed-architecture.md` 的各節拓撲圖，只更新了頁首註記。
- 沒有處理 `AGENT-D1`、ZEN-008 的兩個 QA 缺陷（`GET /v1/agents` 缺 `next_cursor`、
  `GET /v1/agents/{id}` 省略 null 欄位）、以及 Docker Hub 正規化缺陷 —— 都屬 ZEN-008。

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 單元測試 | **未跑** | 本任務未產生程式碼 |
| 契約測試 | **未跑** | 契約剛回退，實作尚未開始 |
| 契約自洽性 | ✅ 人工檢查 | SPEC v0.4.1 與 api-contract v0.4.1：`stage` 值域、非 null 語意、無 `origin`、服務數互相一致；全文檢索確認兩份文件沒有殘留的 multipart／`LOAD`／`PUSH`／`origin` 規範性敘述 |
| v0.4.0 新增是否已實作 | ✅ 已確認**未實作** | `grep -rn "image_origin\|multipart" apps/` 零命中 → 回退不影響任何執行中的程式碼 |
| 「同機 push 可用」的關鍵假設 | ⚠️ **未實測，僅讀碼推論** | 見 §6 第二點，交由 QA §7.4 驗證 |

## 8. 要請主 session 整合進 BOARD 與週邊文件的內容

**BOARD（`coordination/BOARD.md`）**：
1. ZEN-009 維持 `SPEC_READY`，但**標明這是第二版裁決**，範圍已大幅縮小
   （主體從 @backend-dev + @frontend-dev 移到 @platform-eng）。
2. ZEN-009 那段「由來」註記需補一句：**v1 的 tar 上傳已於 2026-09-23 撤回**（ADR-0009 R2），
   理由是使用者裁示允許同機 `docker push`，且 AWS/Azure 也不收 tar。
3. **§刻意不做 清單要改回去**：把「**image tar 直傳**」**放回** MVP 外
   （v0.4.0 曾移出，v0.4.1 撤回）；「平台內建 OCI Registry」**維持在 MVP 內**。
   新增三項到 MVP 外：遠端（非同機）push、Registry 瀏覽 UI、任何代理 Registry HTTP 端點的平台功能。
   SPEC §11 已同步處理。
4. Q-008 / Q-009(orchestrator) / Q-010 維持 RESOLVED（R2 不動它們）。

**週邊文件（我不能寫或不該順手改的）**：
5. `CLAUDE.md` 第 20 行：`registry` 的描述是「ADR-0009 新增的內部 OCI Registry」——
   準確，但可補一句「使用者在本機 `docker push` 到它」。第 23 行「範圍收斂成一條主線：
   **上傳** → 發布 → …」的「上傳」現在容易誤導，建議改成「push image」。
6. `README.md` 第 122 行提到五個服務，準確；但若有描述「上傳 image tar」之處要改。
7. `.claude/agents/platform-eng.md` 第 41 / 88 行提到 `registry`，描述準確，
   可補「它的 client 只有主機 daemon 與使用者 CLI，沒有平台服務連它；不需要 `depends_on`」。
