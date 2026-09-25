# ZEN-009：內部 Registry — 讓使用者在平台主機上 `docker push` 建立 Agent 版本

| 欄位 | 值 |
|---|---|
| 狀態 | `SPEC_READY`（**第二版裁決 v2**；原本的 ui-contract v0.6.0 閘門已解除，見 §6） |
| 擁有者 | @architect → @platform-eng（主要）+ @qa-verifier；@ui-designer / @frontend-dev 只剩一小段說明文字 |
| 對齊 Spec | `specs/SPEC.md v0.4.1`、`specs/api-contract.md v0.4.1` |
| 依據 | [ADR-0009](../../adr/ADR-0009-internal-registry-image-upload.md)，**以文末的修訂 R2 為準** |
| 依賴 | ZEN-008（digest 註冊 → pull → validate → PUBLISHED → run 已可運作） |
| 涉及分層 | L3 資料（新增內部 Registry）。**L0 / L1 / L2 零改動** |

> ## ⚠️ 這份 spec 在 2026-09-23 被大幅縮小，請不要照舊版做
>
> **v1（2026-09-22）**：Console 上傳 `docker save` tar → `api` 串流落檔 → worker `LOAD` → `PUSH`
> → 取 manifest digest → 接既有流程。跨五個角色，含 multipart 端點、migration、兩個 build stage、新 UI 模式。
>
> **v2（2026-09-23，使用者裁示）**：**撤回 tar 上傳**。使用者改為**在跑平台的那台主機上**
> 直接 `docker push localhost:6000/<repo>:<tag>`，再用**本來就有的** JSON 端點以 digest 註冊。
>
> **為什麼**：AWS 與 Azure 都不收 `docker save` tar（ECS / Fargate / App Runner / Lambda →
> **ECR**；Container Apps / ACI → **ACR**）。它們給使用者的「上傳感」來自**託管一個 Registry**，
> 不是接收檔案。使用者看到這個事實後改選了同一條路。完整裁決與被否決的選項見
> [ADR-0009 §修訂 R2](../../adr/ADR-0009-internal-registry-image-upload.md)。
>
> **後端增量是零行程式碼**（@architect 讀碼確認）：`parse_image_ref` 已接受
> `localhost:6000/<repo>@sha256:<64hex>`；`_registry_for()` 已把 `localhost` 當 registry host 處理
> 並以不帶 auth 的方式 pull；`RepoDigests` 的字面比對對非預設 registry 成立。
> **使用者要的功能，在 `registry` 服務起來之後就已經可用了。**
> ZEN-009 要做的不是「實作 push」，而是「**把它宣告為受支援的路徑，並寫進 compose、文件與 UI 說明**」。

## 1. 要解決什麼問題

使用者手上已經有 `docker build` 出來的 image，卻必須先去申請 GHCR 或自己架一個 Registry，
才能把它變成 Agent 版本。對「單機、內部工具、一個人開發」而言，這個前置條件是最大的採用阻力。
本任務讓平台**自己託管一個 Registry**，使用者在同一台主機上 `docker push` 進去就能註冊。

## 2. 範圍

### 做

- compose 新增第 5 個長駐服務：內部 OCI Registry（`registry:2`），**只掛 `zentra-net`**，
  **只發布 `127.0.0.1:6000:5000`**，`REGISTRY_STORAGE_DELETE_ENABLED=false`，volume `registrydata`。
- 維運說明：備份範圍（`registrydata` 與 `pgdata` 同等重要）、手動 GC 程序、
  **`--delete-untagged` 禁令**、catalog ↔ DB 對照程序。
- README / Console 說明：受支援的 `docker tag` → `docker push` → 取 digest → 註冊 四步流程，
  以及「**只能從跑 Zentra 的那台主機推**」這句話。
- 官方 base image `zentra/agent-base:1.0`（Q-010）與 `build()` 簽章、SDK 依賴的契約收斂（Q-008 / Q-009）。
- 契約回退：`specs/api-contract.md` v0.4.1、`specs/SPEC.md` v0.4.1（**已由 @architect 完成**）。

### **明確不做**（同等重要）

- **不做 Registry 的 TLS、認證、帳號、token。** 在只限同機的前提下它們買不到任何東西。
- **不綁 `0.0.0.0`、不做遠端 push。** 要遠端 push → **新 ADR，且該 ADR 必須先解決 TLS 與認證**。
  不得以「先開 `0.0.0.0` 之後再補 TLS」的順序進行。
- **不做 tar 上傳**：沒有 multipart 端點、沒有 `uploads` volume、沒有 `LOAD`/`PUSH` stage、
  沒有大小上限、沒有磁碟預檢、沒有上傳 UI。**這是撤回，不是延後**（ADR-0009 R2-b 否決了「延後」）。
- **不新增 `artifact.origin` / `agent_versions.image_origin`。** 不做任何 migration。
- **不把 `image_ref` / `image_digest` / `version_id` 改成 nullable。** 三欄維持 `NOT NULL`。
- **不做自動 GC、不做配額系統、不做自動備份。** 容量與清理是維運者的責任。
- **不遮蔽 `/v2/_catalog` 或任何 Registry 原生端點**（理由見 §5.4），
  但**平台不得代理、轉發、嵌入或連結它們**——這條是硬規則。
- **不做 Registry 瀏覽 UI、不做「列出 registry 裡有什麼」的端點。**
- **不把 Registry 接上 `zentra-sandbox-net`。** 一行都不准（P4 / SPEC §5 硬規則 3）。
- **不在 `api` 掛 docker.sock。** 一個字都不改現行規則。
- **不改 `version_id` 語意、不新增 Run 狀態、不新增錯誤 code。**
- **不改沙盒**：L2 的四層隔離、SandboxSpec、run token 全部零改動。
- 不把 base image 發佈到公開 Registry、不做簽章、不做多架構 build。
- 不做版本刪除、image 刪除。

## 3. 對核心立場的影響

| 立場 | 有無觸碰 | 說明 |
|---|---|---|
| P1 使用者程式碼不在平台行程執行 | **無**（v1 有，v2 沒了） | 平台不接收檔案，就沒有 tar 要解析。v1 那條「不得用 Python `tarfile` 解析使用者 archive」從「要守的規則」降級成「沒有機會發生」。**移除攻擊面永遠好過守住它。** |
| P2 框架統一 LangChain/LangGraph | 無 | 註冊的仍必須是 `zentra.image/v1` image。 |
| P3 API + MCP 雙門面 | 無 | 註冊之後零改動。 |
| P4 沙盒無長期憑證 | **有，且必須擋住** | Registry 不接沙盒網段；沙盒不注入 Registry 位址；**`api` 不得有任何代理 Registry 的端點**（`api` 同時在兩個網段上，那會是跳板）；內部 Registry 本來就沒有憑證可外洩。 |
| P5 版本不可變 | **有，且被強化** | `version_id` 仍是 manifest digest 前 12 碼。三個 digest 欄位維持 `NOT NULL`（嚴格強於 v1 的條件式 CHECK）；`INSPECT` 的 `RepoDigests` 比對確保「可重新 pull」。 |

## 4. 契約變更

- [x] `specs/api-contract.md` → **v0.4.1**（已由 @architect 更新）：
  **撤回** v0.4.0 的 multipart 形狀、`artifact.origin`、digest 三欄的 `null` 語意、
  `details.stage` 的 `LOAD`/`PUSH`、`QUOTA_EXCEEDED` 的磁碟語意。
  明訂 `repository` 可以含 registry host 與 port（`localhost:6000/my-agent` 合法）。
  **v0.4.0 的新增從未實作，所以相對於實際跑著的 v0.3.1 是零變更。**
- [x] `specs/SPEC.md` → **v0.4.1**（已由 @architect 更新）：§1 §3 §5（新增硬規則 4）§6 §6.1 §7.1 §8.2 §10 §11 §13。
- [ ] `specs/ui-contract.md` → **@ui-designer bump 到 v0.5.2（patch 級）**。
  **v1 那個 v0.6.0 閘門解除**：現行 §5.4「這是文字表單，**沒有檔案輸入、拖放區、upload progress**」
  **仍然正確，不必改**。只需新增一個**靜態說明區塊**：四步發布流程的指令、
  「只能在跑 Zentra 的那台主機上執行」、以及 `localhost:6000` 是平台內部位址的一行提醒。
  **沒有新元件狀態、沒有檔案輸入、沒有進度條、沒有 `XMLHttpRequest`。**
  @frontend-dev 因此**不再被 UI 閘門擋住**——這是純靜態文字。

## 5. 設計要點

### 5.1 拓撲

```
compose（長駐 5 個）
  postgres  zentra-net
  redis     zentra-net
  registry  zentra-net                     ← 新增；ports: ["127.0.0.1:6000:5000"]
  api       zentra-net + zentra-sandbox-net
  worker    zentra-net + /var/run/docker.sock

volumes: pgdata, registrydata              ← 沒有 uploads volume（v2 撤回）
```

- `registry` **只掛 `zentra-net`**。接上 `zentra-sandbox-net` 是違反 P4 的 bug，不是設定選項。
- **沒有任何平台服務會連 registry。** 它的 client 只有主機上的 Docker daemon（pull）
  與使用者的 CLI（push），兩者都走 loopback。
  因此 `api` / `worker` **不需要** `depends_on: registry`，healthcheck 也只是給維運者看的。
  （`registry:2` 的 healthcheck 若沒有可用的 `wget`/`curl`，**不要為此加工具或改基底映像**——
  拿掉 healthcheck 比養一個假的好。）
- **為什麼一定要發布 loopback port**：pull 的執行者是主機上的 Docker daemon，
  它不在 `zentra-net` 上，解析不到服務名 `registry`。daemon 唯一能用的名字是主機 loopback。
  `localhost:6000` 另外免去改 `daemon.json` 的 `insecure-registries`（Docker 對 localhost 預設允許 HTTP）。
- **loopback 綁定同時就是「只限同機」的執行手段。** 不是文件規定，是網路事實。
- 設定項：**沒有新增任何 env**。
  v1 的 `ZENTRA_INTERNAL_REGISTRY` / `ZENTRA_UPLOAD_DIR` / `ZENTRA_MAX_IMAGE_UPLOAD_BYTES` 全部取消——
  平台不再需要知道 registry 位址（它不連它）。
  host port **固定 5000**，寫在 compose 裡；Console 說明文字可以直接寫 `localhost:6000`。
  > **主機端是 6000，不是 registry 慣用的 5000**（ADR-0009 R3）：macOS 的 AirPlay Receiver
  > LISTEN 在 `*:5000`，會蓋掉這個服務。使用者不願關閉 AirPlay 投影，因此改用 6000。
  > **容器內仍是 5000**，換的只有 compose 映射的左側；`zentra-net` 內的 `registry:5000` 不受影響。
  > port 仍然寫死，**沒有做成設定項**——做成設定項就需要一個端點讓 Console 知道實際值，
  > 那是為一個環境細節新增 API 表面。若日後證明衝突很常見，再評估一個 `GET /v1/meta`。

### 5.2 受支援的發布流程（寫進 README、維運說明、Console 說明區塊）

```bash
# 1. 在跑 Zentra 的那台主機上（不是遠端筆電）
docker tag my-agent:dev localhost:6000/my-agent:1.0.0
docker push localhost:6000/my-agent:1.0.0
#    push 最後一行： 1.0.0: digest: sha256:<64 hex> size: <n>

# 2. 事後要再查 digest
docker inspect --format='{{index .RepoDigests 0}}' localhost:6000/my-agent:1.0.0

# 3. 註冊（Console 的版本註冊頁貼同一個字串）
curl -X POST http://localhost:8000/v1/agents/<agent_id>/versions \
  -H 'Content-Type: application/json' \
  -d '{"semver":"1.0.0","image_ref":"localhost:6000/my-agent@sha256:<64 hex>"}'
```

- **推薦 tag 用 semver**（`localhost:6000/<name>:<semver>`）。不強制，平台也無法強制。
  理由是 §5.3 的 `--delete-untagged` 陷阱：讓每個被註冊過的 manifest 都留著一個 tag。
- 外部 Registry 註冊**仍是正途**，不降級、不隱藏。兩者在 API 上是同一件事。

### 5.3 失去單一受控入口之後：容量、GC、未被引用的 repository

平台**不在 push 路徑上**——`docker push` 是使用者 CLI 與主機 daemon 之間的事，
`api` 與 `worker` 完全看不到。因此：

1. **容量與清理是維運者的責任。** 平台不做配額、不做磁碟預檢、不對 Registry 內容負責。
   宣稱能管理自己看不到的東西才是不誠實。
2. **磁碟滿掉的表現**：使用者的 `docker push` 在 client 端失敗（registry 自己的錯誤），
   **平台不會顯示任何訊號**。已 `PUBLISHED` 的版本不受影響。這寫進 SPEC §10 NFR。
3. **未被任何 `agent_versions` 引用的 repository：平台不刪、不列、不管。**
   平台無從分辨那是垃圾、還是使用者五分鐘後要註冊的東西、還是他刻意放的 base image。
   `REGISTRY_STORAGE_DELETE_ENABLED=false` 維持寫死：連誤刪都做不到。
4. **回收是維運程序**（@platform-eng 寫進維運說明，不是產品功能）：
   `GET /v2/_catalog` 列出 repository → SQL 取出 `agent_versions` 中 `status='PUBLISHED'`
   的 `image_digest` → 比對 → 只動兩邊都確認過的 → 停機 → 暫時開啟 delete →
   `registry garbage-collect` → 關回去。
5. ⚠️ **`registry garbage-collect --delete-untagged` 絕對不准用。**
   使用者用同一個 tag 推第二次，前一個 manifest 會變成 untagged，
   但它可能正是某個 `PUBLISHED` 版本的 `image_digest`。這個 flag 會把它刪掉，
   那個版本立刻永久不可執行。**這句話要一字不漏地出現在維運說明裡。**

> **tag 被覆寫對 P5 沒有影響**：註冊的是 digest，舊 manifest 仍以 digest 可定址
> （`delete.enabled=false` 保證它不會被移除）。這正是 ADR-0008 堅持 digest-only 身份的價值兌現。
> 唯一的危險就是上面第 5 點那個 flag。

### 5.4 Registry 原生端點（`/v2/_catalog` 等）：不遮蔽，但平台不得代理

**裁決：不做任何遮蔽。** 理由：

- 能連到 `127.0.0.1:6000` 的就是能在該主機執行程式的人，而在單機單租戶下，
  那群人**同時也是能碰 `docker.sock`（等同主機 root）的人**。對主機 root 隱藏 catalog 是表演。
- 要遮蔽就得在 Registry 前面加反向代理或 auth middleware ——**多一個要跑、要設定、要測的元件**，
  防禦一個不存在的威脅。這正是 ADR-0005 要砍掉的那種東西。
- 遮蔽還會打掉 §5.3 第 4 點的回收程序，它需要 `_catalog`。

**真正的硬規則**（QA 要驗）：

> **平台（`api` / `worker` / Console / MCP）不得代理、轉發、嵌入或連結任何 Registry HTTP 端點。**
> Registry 的 HTTP API 是**維運介面**，不是產品介面。它不出現在 `api` 的任何路由、
> Console 的任何 `fetch`、OpenAPI 或 MCP tool schema 裡。

原因很具體：`api` 同時掛在 `zentra-net` 與 `zentra-sandbox-net` 上。
`api` 上任何一個「幫你查 registry」的端點，都會變成沙盒觸達 Registry 的跳板，直接打穿 P4。
**Registry 對沙盒不可達，要靠「沒有任何一條路可以到」保證，不是靠沙盒不知道位址。**

### 5.5 `artifact.origin`：取消

v1 用它區分「註冊」與「上傳」。v2 只剩一種機制，欄位會永遠是同一個值 ——
**在 API 回應裡放一個恆定值，等於邀請客戶端對它分支**，而 v1 自己就禁止分支。
改定義成「外部 vs 內部 Registry」也不行：那是**從 `image_ref` 衍生**的值（看第一段是不是
`localhost:6000`），衍生值存進 DB 總有一天會和來源不一致。
要顯示徽章，Console 自己從 `image_ref` 判斷即可 —— 那是呈現，不是契約。

連帶：`agent_versions` **不需要任何 migration**；`image_ref` / `image_digest` / `version_id`
維持 `NOT NULL`；v1 那條 `status <> 'PUBLISHED' OR (… IS NOT NULL)` 的條件式 CHECK 不需要
（`NOT NULL` 嚴格更強）。

### 5.6 `image_ref` 正規化的護欄（跨任務約束，**ZEN-009 依賴它成立**）

BOARD 記錄了一個 ZEN-008 既有缺陷：`apps/sandbox/drivers/docker_driver.py` 以**字面字串**比對
`image_ref` 與 `RepoDigests`，而 Docker 會把 `docker.io/` 前綴正規化掉，導致 Docker Hub 的
image 一個都註冊不了。**修正本身屬 @platform-eng 的獨立任務，不在 ZEN-009 範圍內**，
但它會動到 ZEN-009 依賴的不變式，所以護欄先釘死：

1. **正規化只能是「比較時」的操作，絕不可改寫存進 DB 的 `image_ref`。**
   `agent_versions.image_ref` 必須原封不動保存呼叫端註冊的字串 —— 它是 P5 的身份證。
2. **只有預設 registry 會被正規化。** 判斷規則照抄 `docker_driver.py` 既有的 `_registry_for()`：
   repository 第一段含 `.` 或 `:`，或等於 `localhost` → 那一段是 **registry host，必須原樣保留**。
3. 因此 `docker.io/library/alpine` ≡ `docker.io/alpine` ≡ `alpine`，
   但 `localhost:6000/alpine` **不等於**上面任何一個。
   **任何把 `localhost:6000/x` 正規化成 `x` 或 `docker.io/x` 的實作都是嚴重 bug**：
   內部 Registry 的版本會比對到錯的 image。QA 必須有一條測試釘死這個否定案例。
4. 若修正順帶放寬 `parse_image_ref`（目前要求 repository 含 `/`），放寬後仍必須拒絕
   tag-only reference 與非 64 hex 的 digest。

### 5.7 image 契約收斂（Q-008 / Q-009 / Q-010 的落地，**v1 原文不變**）

這三則在 v2 之後**更重要**：使用者現在必然是手工做 image 的人。

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

### 順序

1. **@platform-eng**：compose 加 `registry` + `registrydata`、base image、維運說明。**這是主體。**
2. **@qa-verifier**：`specs/features/ZEN-009/acceptance.md`（§7 是必須涵蓋的最低集合）。**開發前完成。**
3. **@ui-designer**：`ui-contract.md` → v0.5.2，§5.4 加一個靜態說明區塊（§4 列的內容）。
   **這不再擋住 @frontend-dev 太久 —— 它是純文字，沒有新元件狀態。**
4. **@frontend-dev**：把該說明區塊放進版本註冊頁。一律 `textContent`。
5. **@backend-dev**：**本任務對 `apps/api/**` 與 `apps/worker/**` 沒有任何工作。**
   只剩 `packages/sdk/**` 的 Q-008 / Q-009（那兩件與 Registry 無關，可完全平行）。
6. 整合 → @qa-verifier 驗收 → @spec-keeper drift 稽核。**只有 QA 能標 `DONE`。**

> **v1 的兩個閘門都解除**：不再需要 ui-contract v0.6.0，@platform-eng 也不再是 @backend-dev 的前置。

### 可寫範圍

| 角色 | 工作 | 可寫範圍 |
|---|---|---|
| @platform-eng | `compose.yaml` 加 `registry` + `registrydata`；base image Dockerfile；維運說明（備份範圍、手動 GC、`--delete-untagged` 禁令、catalog↔DB 對照、macOS port 5000 衝突）；README 的四步流程 | `compose.yaml`、`docker/**`、`apps/sandbox/**`、`README.md` |
| @backend-dev | **僅** SDK `pyproject.toml` 與 `build()` 簽章 arity 檢查 | `packages/sdk/**` |
| @ui-designer | `ui-contract` v0.5.2 §5.4 新增靜態說明區塊 | `specs/ui-contract.md` |
| @frontend-dev | 版本註冊頁加上該說明區塊（靜態文字 + 可選的複製按鈕） | `apps/console/**` |
| @qa-verifier | `acceptance.md` + `tests/zen009/` | `tests/**`、`specs/features/*/acceptance.md` |

## 7. 驗收條件（@qa-verifier 在開發前寫進 `acceptance.md`）

> 本節是**最低集合**，不是完整清單。@qa-verifier 可以加，不可以減。

### 7.1 P5 可重現性（**最重要，缺這條不算通過**）

- **prune 後仍能按 digest 重新 pull**：`docker push localhost:6000/<repo>:<semver>` → 以 digest 註冊
  → `PUBLISHED` → 以 `docker image rm` 移除該 image 的**所有**本機參照（tag 與 digest ref）
  → 確認 `docker image inspect` 找不到 → 觸發一次 Run → **Run 成功**，且事後
  `docker image inspect` 的 `RepoDigests` 含有與 DB `image_digest` **完全相同**的 digest。
  **這條要有終端輸出當證據，不接受口頭宣稱。**
- 以 Registry HTTP API 獨立核對：`HEAD /v2/<repo>/manifests/<tag>` 回的
  `Docker-Content-Digest` == `docker push` 印出的 digest == DB `image_digest`。
- **`image_ref` 未被改寫**：註冊什麼字串，DB 裡就是什麼字串，API 回應也是什麼字串。
- `image_ref` / `image_digest` / `version_id` 三欄在 schema 上是 `NOT NULL`
  （直接查 `information_schema`），且 API 回應在任何狀態下都不是 `null`。
- **tag 覆寫不影響舊版本**：對同一個 tag push 第二個不同內容的 image → 舊的
  `PUBLISHED` 版本仍能 Run（舊 manifest 仍以 digest 可定址）。

### 7.2 隔離與憑證邊界

- `registry` 容器的 networks **不含** `zentra-sandbox-net`（靜態檢查 compose + 執行期 `docker inspect`）。
- 沙盒內解析／連線 `registry`、`registry:5000`、`localhost:6000`、`127.0.0.1:6000` **全部失敗**。
- `registry` 的 port binding 只有 `127.0.0.1:6000`，**沒有 `0.0.0.0`**
  （`docker inspect` 的 `HostIp` 必須是 `127.0.0.1`；從另一台機器或主機非 loopback IP 連 5000 必須失敗）。
- **`api` 沒有任何代理 Registry 的路由**：全文檢索 `apps/api/**` 與 `apps/console/**`，
  不得出現對 `:5000` 或 `/v2/` 的請求；`GET /openapi.json` 不得出現任何 registry 路徑。
- 沙盒與 validation 容器的 env 不含任何 Registry 位址或憑證；`api` 未掛 docker.sock。
- PostgreSQL 內沒有 image blob、沒有 Registry 憑證。

### 7.3 「不做」的事真的沒有做（護欄測試）

- `POST /v1/agents/{id}/versions` 送 `multipart/form-data` → **`422 VALIDATION_ERROR`**，不得建立版本列。
- 回應中**不存在** `artifact.origin`；送 `origin` 欄位 → `422`（`StrictRequest` 拒絕未定義欄位）。
- `details.stage` 只可能是 `PULL` / `INSPECT` / `VALIDATE`；
  全文檢索 `apps/**` **不得出現** `LOAD` / `PUSH` 作為 build stage 的字面值。
- 全文檢索 `apps/**` 不得出現 `image_origin`、`uploads` volume、`ZENTRA_MAX_IMAGE_UPLOAD_BYTES`。
- `QUOTA_EXCEEDED` 只由併發 Run 上限產生，不由磁碟產生。
- compose 沒有 `uploads` volume。

### 7.4 同機 push 路徑本身

- 正常流程：`examples/demo-echo` build → tag → push `localhost:6000` → 註冊 digest
  → `PUBLISHED` → REST 與 MCP 都能呼叫。
- **不帶認證**即可 push 與 pull（確認我們沒有偷偷引入憑證）。
- **不需要改 `daemon.json`**：在乾淨環境下不加 `insecure-registries` 直接 push 成功。
- 註冊一個 registry 裡**不存在**的 digest → `BUILD_FAILED / stage=PULL`，訊息可行動。
- 兩種存放地點等價：同一個 image 一次放外部 registry、一次放 `localhost:6000`，
  兩者 entrypoints schema 相同、Run 行為相同，DB 的差異**只有 `image_ref` 字串本身**。
- ZEN-008 既有測試全數重跑，**不得退化**。
  內部 Registry 就位後，那 137 個 registry-dependent skipped 應有機會解封 —— 請評估併驗。

### 7.5 `image_ref` 正規化護欄（§5.6）

- **否定案例（最重要）**：`localhost:6000/alpine` **不得**被正規化成 `alpine` 或 `docker.io/alpine`。
- 正面案例：`docker.io/library/alpine` ≡ `docker.io/alpine` ≡ `alpine`
  （這條在 @platform-eng 修好 Docker Hub 缺陷之後才會通過；在那之前標 **`skipped` 並註明原因，
  不得標 `passed`**）。
- tag-only reference 與非 64 hex digest 一律 `422`。

### 7.6 Q-008 / Q-009 / Q-010

- `def build()`（零參數）的 image → `BUILD_FAILED`，message 明確指出簽章要求（**不是 TypeError 堆疊**）。
- 缺 `jsonschema` 的 image → `BUILD_FAILED`，message 指出缺哪個模組。
- 以 `zentra/agent-base:1.0` + 三行 Dockerfile 做出來的 image，push + 註冊後**一次通過**驗證。

## 8. 風險與未決問題

| 風險 | 影響 | 對策 |
|---|---|---|
| **發布需要主機 shell** | 只有瀏覽器、沒有主機存取權的人**無法發布版本** | MVP 交付條件下成立（單租戶、內部工具、開發者即維運者）。這是**唯一**能讓 tar 上傳回來的理由，寫進 ADR-0009 R2「何時該重新檢討」 |
| `registrydata` volume 被刪或未納入備份 | 放在內部 Registry 的 `PUBLISHED` 版本**永久不可執行** | 寫進 SPEC §6.1、維運說明與 README；MVP 不做自動備份，誠實標示 |
| **`--delete-untagged` 誤用** | 直接刪掉某個 `PUBLISHED` 版本的 manifest | 維運說明明文禁止；推薦 tag 用 semver 讓 manifest 保持 tagged |
| 無 GC、無配額、平台看不到 push | 磁碟緩慢成長，滿了時**平台沒有任何訊號** | `delete.enabled=false` 防誤刪 + 手動 GC 程序；責任歸屬明寫在 SPEC §6.1 與 §10 |
| 平台不知道 Registry 裡有什麼 | 出現平台未知的 repository | 接受。回收靠 catalog↔DB 對照的維運程序，不做成產品功能 |
| 有人為了方便改綁 `0.0.0.0` | 內部 Registry 無 TLS 無認證直接上網 | compose 寫死 `127.0.0.1`；QA §7.2 驗 binding；**改綁需新 ADR 且必須先解決 TLS 與認證** |
| 遠端 `DOCKER_HOST` 部署 | `localhost:6000` 指向錯的機器，push 成功但 pull 失敗 | 文件明示「daemon 與 compose 同機」是前提；QA 不需覆蓋遠端 daemon |
| 主機端 port 被別的服務佔用 | 起不來，且該服務可能回 200 騙過寬鬆探測 | 已因 macOS AirPlay 由 5000 改為 6000（R3）。判活只認 `Docker-Distribution-Api-Version` header；**仍不把 port 做成設定項**（見 §5.1） |
| 日後又要換 port | 已註冊的 `image_ref` 含舊 port，那些版本拉不到 | 換之前先確認 DB 裡沒有該 port 的 `PUBLISHED` 版本；有的話是一次資料遷移 |
| `localhost:6000` 出現在使用者看得到的 `image_ref` | 誤以為可以從別台機器 `docker pull` | Console 說明區塊一行提醒；@ui-designer 在 v0.5.2 處理 |
| Docker Hub image 目前完全無法註冊（ZEN-008 既有缺陷） | 最常見的使用情境是壞的 | **不在 ZEN-009 範圍**，屬 @platform-eng 的獨立任務；ZEN-009 只釘死修正必須遵守的護欄（§5.6） |

**未決 SPEC-QUESTION：無。** Q-008 / Q-009 / Q-010 已於 ADR-0009 §7 裁決（R2 維持）。
Q-002～Q-007 與 Q-009(UI) 屬 ui-contract，歸 @ui-designer，不阻擋本任務。

## 9. 完成的定義

一個從未接觸過任何外部 Registry 的環境裡：

1. 使用者在**平台主機上** `docker tag` + `docker push localhost:6000/<repo>:<semver>`，
   不需要認證、不需要改 `daemon.json`，push 成功並拿到 digest。
2. 用既有的 JSON 端點以該 digest 註冊 → `PULL → INSPECT → VALIDATE` → `PUBLISHED`。
3. REST 與 MCP 都能呼叫該版本，Run 正常完成、事件可查、終態後容器不存在。
4. **把該 image 的所有本機參照 prune 掉之後，Run 仍然成功**（daemon 依 digest 重新 pull）。
5. 同一個 image 放在外部 Registry 所得的版本，除了 `image_ref` 字串本身之外**行為完全相同**。
6. 沙盒連不到 Registry；`api` 上沒有任何代理 Registry 的路由；
   `registry` 的 port binding 只有 `127.0.0.1`。
7. 回應中沒有 `artifact.origin`，multipart 被 `422` 擋下，`apps/**` 沒有 `LOAD`/`PUSH` stage。
8. 維運說明含備份範圍、手動 GC 程序與 `--delete-untagged` 禁令。
9. ZEN-008 的既有測試沒有退化。
