# ZEN-009 驗收條件 (Acceptance Criteria)

| 欄位 | 值 |
|---|---|
| 任務 | `ZEN-009` — 內部 Registry + 同機 `docker push` 發布路徑 |
| 擁有者 | `@qa-verifier` |
| 依據 | `SPEC.md v0.4.1`（§5 硬規則 2/3/4、§6.1、§7.1）· `api-contract.md v0.4.1`（§0 錯誤枚舉）· [ADR-0009 **修訂 R2**](../../adr/ADR-0009-internal-registry-image-upload.md) · [ADR-0005](../../adr/ADR-0005-mvp-single-host-scope.md) · feature `spec.md`（第二版）|
| 交件者 | @platform-eng（`compose.yaml`、`docker/**`、`README.md` 維運說明）· @backend-dev（**僅** `packages/sdk/**` 的 Q-008 / Q-009）· @ui-designer（`ui-contract` v0.5.2）· @frontend-dev（版本註冊頁靜態說明區塊）|
| 狀態 | **開發前門檻。本文件寫於實作開始之前，尚未執行、尚未裁定任何一條通過。** |
| 測試位置 | `tests/zen009/`，重跑方式見 §11 |

> **本文件依 ADR-0009 的 R2 撤回版本撰寫。** R1 的 tar 上傳、`LOAD`/`PUSH` stage、
> `artifact.origin`、三欄 nullable 化、`uploads` volume、磁碟版 `QUOTA_EXCEEDED`
> **全部是「必須不存在」的東西**，它們在 §7 有反向斷言，不是待驗收的功能。
>
> **判定規則**：`MUST` 任一失敗 → 不得進 `DONE`。`SHOULD` 失敗 → 記為缺陷，由裁量決定是否阻擋。
> **未執行 ≠ 通過**：跑不起來的一律標 `未驗證`，寫進報告的「未覆蓋」清單。
> **靜態檢查不得代替執行**：§5（隔離）與 §4（P5 可重現性）的 MUST 條目必須有終端輸出當證據。

---

## 0. 環境前置（**先做，否則整份驗收跑不起來**）

| # | 前置 | 為什麼寫在這裡 |
|---|---|---|
| P-0 | **macOS 的 AirPlay Receiver 必須先關掉**（System Settings → General → AirDrop & Handoff → AirPlay Receiver → Off） | **已在驗收機實測**：`ControlCenter` 正在 `LISTEN *:5000`（IPv4 + IPv6），`GET http://127.0.0.1:5000/v2/` 回 `HTTP/1.1 403 Forbidden` + `Server: AirTunes/935.7.1`。它綁的是 `*`，所以 compose 的 `127.0.0.1:5000:5000` **會 bind 失敗**。spec §5.1 已裁定「處置是教使用者關掉它，不是把 port 做成設定項」 |
| P-1 | 四個既有服務 + 新的 `registry` 服務都在跑；`ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000` | 活服務測試的前提 |
| P-2 | 主機原生 Postgres 佔用 `127.0.0.1:5432`。要對容器 DB 下 SQL 需開 `alpine/socat` 代理橋到 `15432`/`16379`，**跑完刪掉代理容器** | `information_schema` 與 `image_ref` 原值比對需要真 DB |
| P-3 | 手動起的 `zentra-qa-registry`（`:5010`）在 ZEN-009 的 registry 上線後才可清掉；清掉前先確認 §10 的 ZEN-008 重跑策略已定案 | 見 §10 |

### ⚠️ P-0 衍生的測試設計硬規則（**寫錯這裡等於整組安全斷言失效**）

1. **「registry 活著」的探測不得只看 TCP 連得上或 HTTP 有回應。**
   AirPlay 會在 `5000` 回 `403`，任何「連得上就算」的探測都會被騙過。
   唯一可接受的判定是：`GET /v2/` 回 `200` **且** 回應 header 含
   `Docker-Distribution-Api-Version: registry/2.0`。
2. **§5.3「非 loopback 位址連不到 5000」不得斷言「connection refused」。**
   在有 AirPlay 的機器上 `curl http://<LAN IP>:5000/v2/` **會成功回 403**（已實測 `192.168.0.101`）。
   正確斷言是：**從非 loopback 位址得不到 Zentra Registry**，
   即「沒有 `Docker-Distribution-Api-Version` header」且「`/v2/_catalog` 不回 registry JSON」。
   同時必須以 `docker inspect` 斷言 `HostIp == "127.0.0.1"` —— 兩條一起才成立。
3. 若 P-0 沒做，測試必須**明確 fail 並指出 AirPlay**，不准 skip 帶過。

---

## 1. 測試分層與各層要證明什麼

| 層級 | 本任務要證明的事 | 不可代替 |
|---|---|---|
| 靜態 | `compose.yaml` 的 registry 服務形狀；`apps/**` 與 `specs/**` 沒有撤回項殘留；維運說明含禁令 | 靜態通過**不代表**執行期成立，§5 必須另外執行期驗 |
| 單元 | `parse_image_ref` / `_registry_for` 對 `localhost:5000/...` 的正負案例 | 不碰 live 服務，快，必須全綠 |
| 契約 | `POST /v1/agents/{id}/versions` 的回應逐欄位吻合 v0.4.1；`/openapi.json` 無 registry 路徑 | 對活服務跑，不用 in-process TestClient |
| 整合 | build → tag → push `localhost:5000` → 註冊 → `PULL/INSPECT/VALIDATE` → `PUBLISHED` → REST + MCP Run | 真 Docker daemon、真 registry、真 Celery。**mock 不算證據** |
| 安全 | port binding、沙盒連不到 registry（含 IP 直連與 host gateway）、沙盒 env、`api` 無代理路由 | 必須在**真的跑著的沙盒容器內**執行探針 |

---

## 2. 範圍上限（「不做」是驗收的一部分）

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| `Z9-S1` | ZEN-009 **不含任何 Alembic migration**：`git diff` 對 base 的 `apps/**/versions/*.py`（或等價 migration 目錄）零新增檔案；`alembic heads` 與實作前相同 | MUST | `git diff --stat` + `alembic heads` |
| `Z9-S2` | `apps/api/**`、`apps/worker/**` **零產品程式碼變更**（SDK 的 Q-008/Q-009 在 `packages/sdk/**`，不算）。有變更就要在交接單舉證為什麼 | MUST | `git diff --stat apps/api apps/worker` |
| `Z9-S3` | 沒有新增任何端點：`/openapi.json` 的 path 集合與實作前**逐字相同** | MUST | 實作前後各存一份 path 清單比對 |
| `Z9-S4` | 沒有新增任何錯誤 code、Run 狀態、`details.stage` 值：`stage` 值域仍只有 `PULL` / `INSPECT` / `VALIDATE` | MUST | 全文檢索 + 契約比對 |
| `Z9-S5` | 沒有新增任何 env：`compose.yaml` 不得出現 `ZENTRA_INTERNAL_REGISTRY` / `ZENTRA_UPLOAD_DIR` / `ZENTRA_MAX_IMAGE_UPLOAD_BYTES` | MUST | 靜態檢索 compose |
| `Z9-S6` | 沒有 Registry 瀏覽 UI、沒有「列出 registry 內容」的端點、Console 沒有任何指向 registry 的連結 | MUST | 靜態檢索 `apps/console/**` + §6 |

---

## 3. A. compose 拓撲（@platform-eng 的主體交付）

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| `Z9-A1` | `compose.yaml` 有第 5 個長駐服務 `registry`，`image: registry:2` | MUST | 以 YAML parse（不是 grep）斷言 `services.registry.image` |
| `Z9-A2` | `services.registry.networks` **恰等於 `["zentra-net"]`**，不含 `zentra-sandbox-net` | MUST | YAML parse + 執行期 `docker inspect zentra-registry-1` 的 `NetworkSettings.Networks` keys 恰為 `{zentra-net}` |
| `Z9-A3` | `services.registry.ports` 恰為 `["127.0.0.1:5000:5000"]`；執行期 `docker inspect` 的 `HostConfig.PortBindings["5000/tcp"][0].HostIp == "127.0.0.1"`，且**沒有任何 binding 的 HostIp 是 `0.0.0.0` 或 `::`** | MUST | YAML parse + `docker inspect` |
| `Z9-A4` | `REGISTRY_STORAGE_DELETE_ENABLED` 設為字串 `"false"`；執行期容器 env 同值 | MUST | YAML parse + `docker inspect` 的 `Config.Env` |
| `Z9-A5` | volume `registrydata` 存在並掛在 `/var/lib/registry`；**`volumes:` 區塊沒有 `uploads`** | MUST | YAML parse |
| `Z9-A6` | `api` / `worker` **沒有** `depends_on: registry`（平台不連 registry，加了就是在宣告一個不存在的依賴） | MUST | YAML parse |
| `Z9-A7` | `registry` 服務**沒有掛 docker.sock**；`api` 仍然沒有掛 docker.sock（未退化） | MUST | YAML parse |
| `Z9-A8` | 若保留 healthcheck，它必須真的會失敗當 registry 壞掉（用 `registry:2` 裡真的存在的指令）；做不到就**不要 healthcheck**（spec §5.1 明文允許拿掉） | SHOULD | 停掉 registry 行程後觀察 health 狀態轉 `unhealthy`；或確認沒有 healthcheck |
| `Z9-A9` | `GET http://127.0.0.1:5000/v2/` 回 `200` 且 header 含 `Docker-Distribution-Api-Version: registry/2.0`（**這是唯一可接受的 registry 存活判定**，見 §0） | MUST | live HTTP |

---

## 4. B. P5 可重現性（**最重要，缺這條不算通過**）

> 主線 fixture 用 `examples/demo-echo`（build context 是 repo 根目錄）。
> repository 固定用 `zentra-zen009-accept/*`，讓事後在 `_catalog` 裡認得出是驗收留下的。

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| `Z9-B1` | **同機 push 主線**：`docker build` → `docker tag localhost:5000/zentra-zen009-accept/demo-echo:1.0.0` → `docker push` 成功，最後一行印出 `digest: sha256:<64 hex>` | MUST | 終端輸出存進報告 |
| `Z9-B2` | **不需要認證**：push 與 pull 全程沒有 `docker login`；主機 `~/.docker/config.json` 的 `auths` 沒有 `localhost:5000` 這一項（證明沒有偷偷引入憑證） | MUST | 指令輸出 + 讀 config |
| `Z9-B3` | **不需要改 `daemon.json`**：`insecure-registries` 不含 `localhost:5000`（或該檔根本不存在），push 仍成功 | MUST | 讀 daemon 設定 + push 輸出 |
| `Z9-B4` | 以該 digest 註冊 → `202` → 輪詢至 `PUBLISHED`；回應的 `version_id` = manifest digest 前 12 碼，`source_type = OCI_IMAGE`，`artifact.runtime_contract = zentra.image/v1`，`entrypoints` 非空 | MUST | live HTTP |
| `Z9-B5` | **三方 digest 一致**：`docker push` 印出的 digest ==  `HEAD /v2/zentra-zen009-accept/demo-echo/manifests/1.0.0` 的 `Docker-Content-Digest` == DB `agent_versions.image_digest` == API 回應的 `artifact.digest` | MUST | 三個獨立來源比對，逐字相同 |
| `Z9-B6` | **`image_ref` 未被改寫**：DB 的 `agent_versions.image_ref` 與 API 回應的 `artifact.image_ref` **逐字元等於**註冊時送出的字串（含 `localhost:5000/` 前綴） | MUST | SQL + live HTTP 對送出字串做 `==` |
| `Z9-B7` | **三欄 `NOT NULL`**：`information_schema.columns` 中 `agent_versions` 的 `image_ref` / `image_digest` / `version_id` 的 `is_nullable = 'NO'`；且 API 回應在 `BUILDING` / `PUBLISHED` / `FAILED` 三種狀態下這三個值都不是 `null` | MUST | SQL + live HTTP |
| `Z9-B8` | **prune 後仍能按 digest 重新 pull**（架構師點名，缺這條不算通過）：移除該 image 的**所有**本機參照（tag 與 `<repo>@sha256:` digest ref）→ `docker image inspect` 找不到 → 觸發一次 Run → **Run `SUCCEEDED`** → 事後 `docker image inspect` 的 `RepoDigests` 含有與 DB `image_digest` **完全相同**的 digest | MUST | **終端輸出當證據，不接受口頭宣稱** |
| `Z9-B9` | **tag 覆寫不影響舊版本**：對 `:1.0.0` push 第二個內容不同的 image（digest 不同）→ 舊 `PUBLISHED` 版本仍能 Run 且輸出仍是舊 image 的行為；舊 manifest 仍可用 `GET /v2/<repo>/manifests/<舊 digest>` 取得 | MUST | live HTTP + Run 結果比對 |
| `Z9-B10` | **REST 與 MCP 都能呼叫**：`POST /v1/agents/{id}/runs:invoke` 與 `POST /mcp/agents/{id}`（Streamable HTTP，`tools/call`）都成功，輸出結構相同 | MUST | live HTTP |
| `Z9-B11` | **Run 終態後沙盒不存在**：帶 `zentra.run_id=<id>` label 的容器在有界時間內 inspect 不到 | MUST | `docker ps -a --filter label=` |
| `Z9-B12` | **內外部 registry 等價**：同一份 image 一次放 `localhost:5000`、一次放外部 registry，兩個版本的 `entrypoints` schema 逐欄位相同、Run 結果相同，DB 的差異**只有 `image_ref` 字串本身**（`image_digest` 若 manifest 相同亦應相同；不同則要能解釋） | MUST | 兩版本 DB 列 diff |
| `Z9-B13` | 註冊一個 registry 裡**不存在**的 digest → 終態 `FAILED`，`error.code = BUILD_FAILED`，`details.stage = PULL`，message 可行動（指出 pull 不到該 digest，不是裸堆疊） | MUST | live HTTP |

---

## 5. C. 隔離與憑證邊界（P4 / SPEC §5 硬規則 2 · 3）

> 這一節**不得以靜態檢查代替執行**。探針要在真的跑著的沙盒容器裡跑。

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| `Z9-C1` | 沙盒內連 `registry:5000`（服務名）→ 失敗 | MUST | 沙盒內 `socket.create_connection`，斷言 BLOCKED |
| `Z9-C2` | **沙盒內以 registry 容器在 `zentra-net` 上的 IP 直連 `<ip>:5000` → 失敗**。（名稱解析失敗不算隔離 —— 這條才是真正的斷言，照 ZEN-008 `test_sandbox_cannot_reach_the_data_layer_by_ip_either` 的做法） | MUST | 先 `docker inspect zentra-registry-1` 取 IP，再在沙盒內連 |
| `Z9-C3` | 沙盒內連主機 loopback `127.0.0.1:5000` 與 `localhost:5000` → 失敗（這兩條是沙盒自己的 loopback，**單獨成立不算證據**，必須與 C2、C4 一起看） | MUST | 沙盒內探針 |
| `Z9-C4` | **沙盒內連 host gateway 的 `:5000` → 失敗**：取 docker bridge gateway IP 與 `host.docker.internal`，兩者都要 BLOCKED。（registry 發布在主機 port 上，這是最現實的一條逃逸路徑） | MUST | 沙盒內探針 |
| `Z9-C5` | **反向斷言**：同一個沙盒內連 `api` 的 `/internal/runs/<run>/bootstrap` → `200`。漏了這條，上面四條「都連不到」可能只是容器沒有網路 | MUST | 沙盒內 `urllib.request` |
| `Z9-C6` | 沙盒容器的 `NetworkSettings.Networks` 恰為 `{zentra-sandbox-net}`；`zentra-sandbox-net` 的 `Internal` 仍為 `true`（未退化） | MUST | `docker inspect` + `docker network inspect` |
| `Z9-C7` | 沙盒 env 的 `ZENTRA_*` **恰為契約的三個**，且整個 env blob 不含 `5000`、`registry`、`localhost:5000`、任何憑證字樣 | MUST | 沙盒內 `os.environ` + `docker inspect` 的 `Config.Env` 雙向檢查 |
| `Z9-C8` | validation 容器（`q.build` 的 VALIDATE 階段）同樣不含任何 Registry 位址或憑證 | MUST | validation 容器 inspect |
| `Z9-C9` | `api` 未掛 docker.sock；`registry` 未掛 docker.sock；只有 `worker` 掛（未退化） | MUST | YAML parse + `docker inspect` 的 `Mounts` |
| `Z9-C10` | PostgreSQL 內沒有 image blob、沒有 Registry 憑證：`agent_versions` 無 `bytea`/大型 blob 欄位，全表資料不含 `password` / `identitytoken` / `registrytoken` / `auth` 字樣 | MUST | SQL |
| `Z9-C11` | **未覆蓋要講清楚**：L2 核心隔離（gVisor）與沙盒/平台實體隔離依 ADR-0005 不在 MVP。對應測試標 `skipped` 並註明 ADR-0005，**絕不標 `passed`** | MUST | 報告用語檢查 |

---

## 6. D. 平台不得代理 / 轉發 / 嵌入 / 連結 Registry（SPEC §5 硬規則 4）

> 這條存在的原因很具體：`api` 同時掛 `zentra-net` 與 `zentra-sandbox-net`。
> `api` 上任何「幫你查 registry」的端點，都是沙盒觸達 Registry 的跳板，直接打穿 P4。

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| `Z9-D1` | 全文檢索 `apps/api/**`：不得出現對 registry 的 HTTP 呼叫。禁止樣式：`:5000`、`/v2/_catalog`、`/v2/` 作為 request path、`registry:5000`、`localhost:5000` 出現在任何 URL 組裝或 http client 呼叫裡 | MUST | 靜態檢索 + 人工判讀命中（註解與文件字串要分辨出來，**但不得因為「只是註解」就放過 URL 常數**） |
| `Z9-D2` | 全文檢索 `apps/console/**`：任何 `fetch` / `XMLHttpRequest` / `<a href>` / `<img src>` 的目標都不得是 registry。`localhost:5000` **只允許以純文字出現在說明區塊**，且必須是 `textContent` 寫入，不得出現在可點擊的屬性裡 | MUST | 靜態檢索 + 瀏覽器實跑攔截網路請求，確認 `:5000` 請求數 = 0 |
| `Z9-D3` | `GET /openapi.json`：path 集合不含任何 `/v2`、`registry`、`catalog` 字樣；整份 JSON 不含 `:5000` | MUST | live HTTP |
| `Z9-D4` | MCP `tools/list` 的 tool schema 不含任何 registry 相關 tool | MUST | live MCP |
| `Z9-D5` | `apps/worker/**` 不得有連 registry 的程式碼（pull 由主機 daemon 做，不是 worker 自己發 HTTP） | MUST | 靜態檢索 |
| `Z9-D6` | **反向健全性**：確認 `registry` 服務確實沒有被任何平台服務連過 —— 驗收期間 registry 的 access log 中，來源只有主機 daemon / CLI，沒有來自 `api` / `worker` 容器 IP 的請求 | SHOULD | `docker logs zentra-registry-1` 比對容器 IP |

---

## 7. E. 撤回項的反向斷言（R2 撤回的東西**必須不存在**）

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| `Z9-E1` | `POST /v1/agents/{id}/versions` 送 `multipart/form-data` → **`422` 且 `error.code = VALIDATION_ERROR`**（不是 `415`、不是 `500`），且 DB **不新增版本列**、`q.build` **不多一則訊息** | MUST | live HTTP + SQL 前後計數 |
| `Z9-E2` | 版本回應中**不存在** `artifact.origin`（key 層級不存在，不是值為 null） | MUST | live HTTP：`"origin" not in body["artifact"]` |
| `Z9-E3` | 註冊請求多送 `origin` 欄位 → `422 VALIDATION_ERROR`（`StrictRequest` 拒絕未定義欄位） | MUST | live HTTP |
| `Z9-E4` | 全文檢索 `apps/**`：不得出現 `image_origin`、`ZENTRA_MAX_IMAGE_UPLOAD_BYTES`、`ZENTRA_UPLOAD_DIR`、`ZENTRA_INTERNAL_REGISTRY` | MUST | 靜態檢索，零命中 |
| `Z9-E5` | 全文檢索 `apps/**`：`LOAD` / `PUSH` **不得作為 build stage 的字面值**出現（`details.stage` 的值域只有三個） | MUST | 靜態檢索 + 人工判讀（避免誤殺無關的 `push`／`load` 用詞） |
| `Z9-E6` | `compose.yaml` 沒有 `uploads` volume、沒有任何 upload 相關 mount | MUST | YAML parse |
| `Z9-E7` | `QUOTA_EXCEEDED` 只由併發 Run 上限產生：以磁碟壓力無法觸發它；`apps/**` 中 `QUOTA_EXCEEDED` 的產生點不含任何磁碟／`statvfs`／`disk` 判斷 | MUST | 靜態檢索產生點 + 契約比對 |
| `Z9-E8` | `specs/api-contract.md` 與 `specs/SPEC.md` 中不得殘留 multipart 形狀、`artifact.origin`、`LOAD`/`PUSH` 的**規範性**敘述（變更紀錄裡的歷史說明不算） | SHOULD | 靜態檢索 + 人工判讀 |

---

## 8. F. `image_ref` 正規化護欄（spec §5.6 / SPEC §7.1 / ADR-0009 R2-6）

> 這一節與 @platform-eng 的 Docker Hub 修正是**平行任務**。ZEN-009 依賴它成立，所以先釘死。

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| `Z9-F1` | **否定案例（最重要）**：`localhost:5000/alpine` **不得**被正規化成 `alpine` 或 `docker.io/alpine` 或 `docker.io/library/alpine`。任何實作只要讓這三者比對成功，就是嚴重 bug | MUST | 單元：對正規化/比對函式直接斷言不相等 |
| `Z9-F2` | `_registry_for("localhost:5000/x")` 必須回 `localhost:5000`（第一段含 `:` → 是 registry host，原樣保留）；`_registry_for("localhost/x")` 回 `localhost` | MUST | 單元 |
| `Z9-F3` | **正規化只能是「比較時」的操作**：註冊 `localhost:5000/<repo>@sha256:…` 後，DB 的 `image_ref` 與 API 回應逐字元等於送出值（與 `Z9-B6` 同一條不變式，從兩個角度釘） | MUST | SQL + live HTTP |
| `Z9-F4` | 正面案例：`docker.io/library/alpine` ≡ `docker.io/alpine` ≡ `alpine`。**在 @platform-eng 修好 Docker Hub 缺陷之前標 `skipped` 並註明原因，不得標 `passed`** | MUST（可 skip，需註明） | 單元 + 整合 |
| `Z9-F5` | 放寬後仍必須拒絕：tag-only reference（`localhost:5000/x:1.0.0`）、非 64 hex digest、非 `sha256:` 前綴、同時帶 tag 與 digest → 一律 `422 VALIDATION_ERROR`，DB 無版本列 | MUST | 單元 + live HTTP |
| `Z9-F6` | **正規化必須是獨立於憑證查找的函式**：憑證 key（`_registry_for`，回 Docker 的 auth key）與比較用正規化（`_normalized_repository` / `_comparison_key`）是兩件事，不得混用。`_registry_for("alpine")` 回 `https://index.docker.io/v1/` 而 `_registry_for("docker.io/library/alpine")` 回 `docker.io` 是**憑證 key 的既有慣例**，不是比較用的值 | SHOULD | 單元 |

> **本輪發現（2026-09-23，寫驗收條件時看到的）**：工作區裡有一份**尚未 commit** 的
> `apps/sandbox/drivers/docker_driver.py` 變更，已經加了 `_split_registry` /
> `_normalized_repository` / `_comparison_key`，並把 `_inspect_object` 的 `RepoDigests`
> 比對改成正規化 key 比對。那是 @platform-eng 的 Docker Hub 平行任務，**不屬 ZEN-009**。
> `tests/zen009/test_image_ref_normalization.py` 的三條護欄已對它生效並通過 ——
> 但那是**平行任務的成果，不得計入 ZEN-009 的通過項**。
> 若該變更被回退，`Z9-F4` 會自動回到 `skipped`（測試已寫成這樣），
> `Z9-F1` 的否定案例則在兩種狀態下都必須綠。

---

## 9. G. GC 禁令與維運說明（文件是交付物，不是附帶）

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| `Z9-G1` | 維運說明含**一字不漏**的 `--delete-untagged` 禁令，且說明了「為什麼」：tag 覆寫會讓某個 `PUBLISHED` 版本的 manifest 變成 untagged，該 flag 會把它刪掉，版本立刻永久不可執行 | MUST | 文件檢索：同時命中 `--delete-untagged`、「不准/禁止」語氣、以及 `PUBLISHED` 這個後果 |
| `Z9-G2` | 維運說明含備份範圍：`registrydata` 與 `pgdata` **同等重要**，刪掉 `registrydata` = 那些 `PUBLISHED` 版本永久不可執行 | MUST | 文件檢索 |
| `Z9-G3` | 維運說明含手動 GC 程序（停機 → 暫時開 delete → `registry garbage-collect` → 關回去）與 catalog ↔ DB 對照程序 | MUST | 文件檢索 |
| `Z9-G4` | 維運說明含「容量是維運者的責任；磁碟滿了平台沒有任何訊號」 | MUST | 文件檢索 |
| `Z9-G5` | 維運說明含四步發布流程與 **「只能在跑 Zentra 的那台主機上執行」** | MUST | 文件檢索 |
| `Z9-G6` | 維運說明含 macOS AirPlay Receiver 佔用 5000 的處置（§0 P-0 已實測會擋住啟動） | MUST | 文件檢索 |
| `Z9-G7` | **腳本護欄**：repo 內不得存在任何會執行 `garbage-collect --delete-untagged` 的腳本／Makefile／compose command | MUST | 全 repo 檢索，零命中 |
| `Z9-G8` | Console 說明區塊：四步指令、「只能在平台主機上執行」、「`localhost:5000` 是平台內部位址，不是別台機器 pull 得到的位址」、外部 vs 內部 registry 一行說明；**全部以 `textContent` 寫入**（無 `innerHTML`） | MUST | 靜態檢索 + 瀏覽器 DOM 檢查 |

---

## 10. H. Q-008 / Q-009 / Q-010

| # | 條件 | 等級 | 驗證方式 |
|---|---|---|---|
| `Z9-H1` | `def build()`（零參數）的 image → 終態 `FAILED`，`BUILD_FAILED`，`details.stage = VALIDATE`，message **明說「`build()` 必須接受一個 ctx 參數」**，不是 `TypeError` 堆疊 | MUST | 整合：專用 fixture image |
| `Z9-H2` | `def build(a, b)`（兩個必填參數）同樣被 arity 預檢擋下，訊息同上 | MUST | 整合 |
| `Z9-H3` | 缺 `jsonschema` 的 image → `BUILD_FAILED`，message **指出缺哪個模組名** | MUST | 整合：專用 fixture image |
| `Z9-H4` | `packages/sdk/pyproject.toml` 存在且宣告 `jsonschema` / `httpx` / `langgraph` 為 runtime dependency | MUST | 靜態 |
| `Z9-H5` | `docker/agent-base.Dockerfile` 產出的 `zentra/agent-base:1.0` 帶 label `io.zentra.image-contract=zentra.image/v1`、`USER 65534:65534`、有 `/opt/zentra/bin/zentra-agent` | MUST | `docker inspect` |
| `Z9-H6` | 以 `FROM zentra/agent-base:1.0` + 一行 `COPY` 做出的 image，push + 註冊後**一次通過**驗證（`PUBLISHED`，不需要任何額外步驟） | MUST | 整合 |
| `Z9-H7` | `examples/demo-echo/Dockerfile` 已改為以 base image 為基底，且仍可 build 成功 | MUST | build 輸出 |
| `Z9-H8` | base image **沒有**被推到任何公開 Registry、沒有簽章、沒有多架構 build | MUST | Dockerfile / CI 檢索 |

---

## 11. ZEN-008 的 137 個 `skipped`：實測結論與併驗建議

### 11.1 實測數字（2026-09-23，本輪量到的，不是引用）

```
$ PYTHONPATH=. ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 pytest tests/zen008 -q -rs
2 failed, 66 passed, 137 skipped in 2.08s
```

`137` 這個數字是對的，但**它們不是卡在同一個前置條件**。逐項拆開：

| 卡在什麼 | 條數 | ZEN-009 的 registry 進 compose 後會自然解封嗎 |
|---|---|---|
| 需要本機測試 Registry（`require_registry()`） | **112** | **不會自動解封** —— 見 §11.2 |
| 需要 `ZENTRA_TEST_DB_URL`（socat 橋接容器 Postgres） | 21 | 不會。與 registry 無關，要另外開 socat |
| 需要 `ZENTRA_TEST_REDIS_URL` | 1 | 不會。同上 |
| **永久不會解封**（ZEN-004 `cancel`、`Idempotency-Key`、ADR-0005 gVisor） | 3 | **永遠不會**。這 3 條不准被算進「可解封」 |

另外那 `2 failed` 是 BOARD 已登記的兩個 ZEN-008 契約缺陷，與 registry 無關：
`GET /v1/agents` 最後一頁少 `next_cursor`（`keys=['items']`）、
`GET /v1/agents/{id}` 少 `description` 與 `latest_published_version`
（`keys=['agent_id','created_at','endpoints','name','updated_at']`）。

### 11.2 為什麼「registry 進 compose」不會自動解封那 112 條

`tests/zen008/conftest.py` 的 `require_registry()` 是用**容器名稱**判定的：

```python
["docker", "inspect", "-f", "{{.State.Running}}", images.REGISTRY_CONTAINER]  # "zentra-qa-registry"
```

compose 起來的容器叫 `zentra-registry-1`，名字對不上 → **112 條照樣 skip**。
要解封需要兩處改動（都在 `tests/**`，是 QA 的可寫範圍）：

1. `require_registry()` 改成 **HTTP 探測**：`GET $ZENTRA_TEST_REGISTRY/v2/` 回 `200`
   **且** header 含 `Docker-Distribution-Api-Version: registry/2.0`。
   **不可以只看連得上** —— §0 已實測 macOS AirPlay 在 5000 回 403，會騙過任何寬鬆探測。
2. 執行時設 `ZENTRA_TEST_REGISTRY`（`images.REGISTRY` 已支援這個 env，預設 `localhost:5010`）。

### 11.3 建議（**不要把 ZEN-008 的 fixture 推進產品 registry**）

**結論：併「跑」，不併「判」；而且 ZEN-008 繼續用可丟棄的測試 registry，不要指向 `localhost:5000`。**

不指向產品 registry 的三個理由：

1. **產品 registry 是 `delete.enabled=false` 且沒有 GC。** ZEN-008 的 8 個 fixture repository
   一旦推進去就是**永久、刪不掉**的垃圾，躺在 `registrydata` 裡 —— 而 ADR-0009 R2 才剛把
   `registrydata` 提升成與 `pgdata` 同等的備份範圍。要清掉它們必須走
   「停機 → 暫時開 delete → `garbage-collect` → 關回去」的完整維運程序。
   **拿一次測試環境的方便，換一個生產資料卷的永久污染，不划算。**
2. **ZEN-008 自己的驗收條件禁止這件事。** `specs/features/ZEN-008/acceptance.md` §1 寫明
   「OCI 測試使用測試期間啟動的本機 Registry fixture，**它不是產品 compose 的第五個長駐服務**」。
   改指向產品 registry 等於偷偷改掉一份已簽核的驗收條件 —— 那是規格變更，不是 QA 的方便。
3. **ZEN-008 有一條測試是「故意用同一個 tag 推第二個不同內容的 image」**（`valid_v2` 與
   `valid_v1` 共用 `zentra-qa/agent:stable`）。那正好會在產品 registry 裡**刻意製造出
   untagged manifest** —— 也就是 §9 `Z9-G1` 那條禁令在講的危險狀態。
   在生產儲存裡故意製造它，是把測試的副作用留給維運者收。

**實際建議的做法**：

- ZEN-008 繼續用一個**可丟棄**的 registry 容器（現有的 `zentra-qa-registry:5010` 就可以，
  或 compose registry 上線後另起一個 `:5010`），只把 `require_registry()` 換成 §11.2 的 HTTP 探測。
  這樣 112 條立刻可跑，且零污染。
- ZEN-009 自己的 §4 主線**必須**打產品 registry（`localhost:5000`），
  但只用 `zentra-zen009-accept/*` 這一組 repository 名，並在交接單明列
  「驗收在 `registrydata` 留下了哪幾個 repository」，交給維運者決定要不要走 GC 程序。
- **同一輪跑、分開裁定**：環境（Docker、socat、base_url、registry）搭一次兩邊共用，
  省掉重複成本；但 ZEN-008 的 `DONE` 與 ZEN-009 的 `DONE` 各自獨立。
  理由：ZEN-008 還有 21 條 DB-gated + 2 個已知缺陷，那些與 ZEN-009 毫無關係。
  **綁在一起會有兩種壞結果**：ZEN-009 被 ZEN-008 的舊缺陷擋住，
  或 ZEN-008 的缺陷搭 ZEN-009 的順風車矇混過關。
- 那 3 條永久 skip（ZEN-004 cancel、Idempotency-Key、gVisor）在任何報告裡都要
  **維持 `skipped` 並註明理由**，不准因為「數字比較好看」被算成解封。

---

## 12. 明確不覆蓋（誠實列出，不要在報告裡假裝有測）

| 未覆蓋 | 為什麼 |
|---|---|
| L2 核心隔離 / gVisor / 沙盒與平台的實體隔離 | ADR-0005 明定不在 MVP。**這不是缺陷，是已知取捨。** 四層 Docker 隔離通過 ≠ 防得住核心層逃逸 |
| 遠端 `DOCKER_HOST` 部署下的行為 | spec §8 明列不在 MVP 範圍（會 push 成功、pull 失敗） |
| 從**第二台實體機器**連 `127.0.0.1:5000` | 驗收機只有一台。以「本機非 loopback IP + `docker inspect` HostIp」兩條合併替代，並在報告註明是替代證據 |
| Registry 磁碟滿掉的行為 | 需要把卷灌滿，代價高於價值；ADR-0009 已裁定「平台不會有任何訊號」是**設計**而非缺陷 |
| Registry 的 TLS / 認證 | 明確不做（ADR-0009 R2-1 硬規則 3）。**若有人提議開 `0.0.0.0` 或加遠端 push，QA 要擋下來並開 `SPEC-QUESTION`** |
| 內部 Registry 的併發 push / 負載 | 單機單使用者，`docker push` 是 CLI 行為，不在平台路徑上 |

---

## 13. 重跑方式

```bash
# 0) 前置：關掉 macOS AirPlay Receiver（否則 registry 起不來，見 §0 P-0）
lsof -nP -iTCP:5000 -sTCP:LISTEN          # 必須是空的，或只有 com.docker

# 1) 起服務
docker compose up -d
curl -sI http://127.0.0.1:5000/v2/ | grep -i docker-distribution-api-version   # 必須有這行

# 2) DB 斷言需要 socat 代理（主機原生 Postgres 佔住 5432），跑完刪掉
docker run -d --name zentra-qa-pg-proxy --network zentra-net -p 127.0.0.1:15432:15432 \
  alpine/socat tcp-listen:15432,fork,reuseaddr tcp-connect:postgres:5432

# 3) 跑 ZEN-009
PYTHONPATH=. \
ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 \
ZENTRA_TEST_DB_URL=postgresql+asyncpg://zentra:zentra@127.0.0.1:15432/zentra \
ZENTRA_TEST_REGISTRY=localhost:5000 \
  /private/tmp/zentra-backend-venv/bin/pytest tests/zen009 -v -rs

# 4) ZEN-008 回歸（不得退化）——注意指向**可丟棄**的 registry，理由見 §11.3
ZENTRA_TEST_REGISTRY=localhost:5010 ... pytest tests/zen008 -q -rs

# 5) 收工：刪掉 socat 代理容器
docker rm -f zentra-qa-pg-proxy
```

---

## 14. 本輪已寫好的測試骨架與它現在的顏色（**紅的是閘門，不是缺陷**）

```
$ PYTHONPATH=. pytest tests/zen009 -q
6 failed, 32 passed
```

| 檔案 | 覆蓋 | 現在的狀態 |
|---|---|---|
| `tests/zen009/conftest.py` | AirPlay-proof 的 registry 探測、compose YAML 解析、原始碼檢索 | — |
| `test_compose_topology.py` | §3 A1–A7、S5 | **6 紅** —— `compose.yaml` 還沒有 `registry` 服務。**這正是開工閘門**，@platform-eng 交付後應全綠 |
| `test_withdrawn_and_no_proxy.py` | §7 E4/E5 + §6 D1/D2/D5 + §9 G7 的靜態半邊 | **全綠** —— 護欄現在綠，是為了在有人把撤回項寫回來的那一刻變紅 |
| `test_image_ref_normalization.py` | §8 F1/F2/F4/F5 | **綠 + F4 依平行任務**（見 §8 的「本輪發現」） |

**尚未寫、驗收時才寫得出來的**（需要 `registry` 上線）：
§4 全部（B1–B13，含 prune 後重 pull 的證據）、§5 執行期探針（C1–C10）、
§6 的 live 半邊（D3/D4/D6）、§7 的 live 半邊（E1/E2/E3/E7）、§9 文件檢查（G1–G6/G8）、§10（H1–H8）。
**這些一條都還沒跑過，不得以「骨架已寫」當成已驗證。**
