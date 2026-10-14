# ZEN-019 開發前驗收條件

| 欄位 | 值 |
|---|---|
| 依據 | `SPEC.md v0.5.0` §5 硬規則 5／§7.4／§8.1／§10、`api-contract.md v0.12.0` §0 · §2.5–§2.10 · §4.1–§4.4、`ui-contract.md v0.9.0` §5.16–§5.19、[ADR-0014](../../adr/ADR-0014-per-agent-config-secrets-and-egress.md) E1–E7／M1–M5／§6、ZEN-019 feature spec |
| 範圍 | 三層（設定／機密／能力）+ 兩條 Gateway 出口路由 + **三條**派工路徑帶 `config`/`secret_names` + SDK 四個新能力 + Console 四個元件 + `examples/crypto-quant-agent/` |
| 狀態 | **開發前門檻；尚未執行、尚未裁定通過** |
| 撰寫時點 | 實作進行中，本文件在實作完成前定稿。它是判準，不是事後追認 |

本文件定義「做完了」的可執行證據。規則：

- **`skipped` 不是 `passed`。** 環境缺件導致跳過的斷言一律記在 §6.2「未覆蓋」，不得計入通過數。
- **測試不得依賴公網或真實 LLM。** 唯一的例外是 §1.3 的出口成功路徑，而那個例外要先裁決。
- **錯誤只准用 `api-contract.md` §0 的 code 枚舉**（本 feature 只新增 `EGRESS_FAILED`），
  `details.reason` 只准用 §4.3 的枚舉。測試裡出現自創字串就是測試本身不合格。
- **反向斷言不可省。** 「擋下來了」的每一條旁邊都要有一條「該通的真的通」，
  否則一個「全部拒絕」的實作也會全綠。
- 本 feature **不改** Runs／SSE／MCP／版本發布的形狀，也**不放寬**沙盒 env 的精確集合檢查；
  這兩件事各有一條護欄（`Z19-45`、`Z19-40`），它們要留在 repo 裡長期跑，不是驗一次就算。

---

## 1. 測試環境、fixture 與分層

### 1.1 分層與最低證據

| 層級 | 測什麼 | 最低證據 |
|---|---|---|
| 單元 | 鍵名／名字 regex、大小上限、host 合法性、`methods` 白名單、位址分類（private/loopback/link-local/metadata、IPv6 與 IPv4-mapped）、檢查順序、`last_four` 遮罩、`errors_only` 條件 | pytest，可用 fake resolver / fake transport |
| 契約 | 14 條公開端點 + 2 條 Gateway 路由逐欄位對 v0.12.0；bootstrap／工作項的新欄位 | 對 live FastAPI 打真 HTTP，不以函式直呼代替 |
| 整合 | 三條派工路徑、Fernet 加解密、稽核寫入、SMTP、限額、cascade 刪除 | 真 PostgreSQL + Redis + Celery + `api` |
| Docker 整合／安全 | 沙盒 env 精確集合、沙盒網段仍不通、反向斷言 Gateway 可達、token scope | 真容器內探針，不以設定檔靜態檢查代替 |
| E2E | Console 四個元件、範例 agent 端到端 | 沿用 repo 既有的 CDP 腳本慣例（`tests/zen019/cdp_*.mjs`），對真後端操作 |

測試碼放 `tests/zen019/`，整合層沿用既有開關：`ZENTRA_TEST_BASE_URL`、`ZENTRA_TEST_DB_URL`、
`ZENTRA_TEST_REDIS_URL`。缺變數而 `skipped` 不算通過。

### 1.2 必要 fixture（沒有這些，下面一半的條件驗不了）

1. **HTTPS 出口 stub**：可設定回 200／418／429／500／302（含 `Location`）／慢回應／超大回應；
   會**記錄收到的 method、path、query、每一個 header**（用來驗平台注入的認證與 `User-Agent`）；
   可回 `Set-Cookie`、`Authorization`、`X-Custom` 等不該被回傳的 header。
2. **SMTP stub**（如 `aiosmtpd`）四種型態：正常 STARTTLS(587)、隱式 TLS(465)、
   **不支援 STARTTLS**、**認證固定失敗**。要能斷言「有沒有收到 `DATA`」，
   這是「全有全無」與 `TLS_REQUIRED` 唯一站得住的證據。
3. **可控 DNS / hosts**：能讓某個白名單主機名解析到指定 IP（`127.0.0.1`、`10.x`、
   `169.254.169.254`、`postgres` 的容器 IP、`::1`），以及一個**兩次解析回不同答案**的名字（rebinding）。
4. **哨兵字串**：機密值、config 值、query string 各用一個一眼認得出的 sentinel
   （例：`ZQA-SECRET-8f3a…`），供全域外洩掃描（`Z19-12`、`Z19-36`）使用。
5. **範例 agent image**：`examples/crypto-quant-agent/` build 出來的 image，加一個
   可注入「Binance 失敗」的開關（由 stub 回 500 達成，**不要真的去打 Binance**）。

### 1.3 「不依賴外網」與出口成功路徑的衝突（**先裁決，再寫測試**）

出口 proxy 只准 `https`，而且**解析結果落在 private 位址一律擋**。
本機或 compose 網段上的任何 stub 都在 `172.16/12` 這類私有網段 —— 也就是說，
**照契約寫出來的正確實作，會擋掉所有本機 stub，成功路徑在不打外網的前提下打不通。**

驗收採兩段式，兩段都要有：

- **必做（不打外網）**：在 `app` 行程內以 fake resolver 把 stub 的私有 IP 偽裝成公網 IP，
  並注入可觀測的 HTTP transport。斷言 proxy **連到的就是解析出來的那個 IP**、
  帶了 `Host` header 與對應 SNI、不跟隨 redirect、注入了規則指定的認證 header。
  這一段同時是 `Z19-21` 的 rebinding 證據。
- **選做（預設 skip）**：`ZENTRA_TEST_ALLOW_PUBLIC_EGRESS=1` 時，對一個真實公開 HTTPS 主機
  跑一次成功路徑。**預設不跑**，跑了也只作為補充證據。

**若實作改用「測試專用旁路」（環境變數放行私有位址）來讓成功路徑可測，
那個旁路本身要有一條驗收：預設關閉，且關閉時 `Z19-21` 全部仍為 `PRIVATE_ADDRESS`。**
沒有這條護欄的旁路等於把 SSRF 防護做成可選的。

### 1.4 測試需要能調整的旋鈕

`EGRESS_MAX_CALLS_PER_RUN`、`EMAIL_MAX_PER_RUN`、`EMAIL_MAX_PER_HOUR`、`ZENTRA_SECRET_KEY`
必須可由環境變數覆寫（SPEC §10「全部可由環境變數調整」）。
測試把次數上限調到 1–2 來驗，**不得**用預設值 100／5／60 跑滿。
旋鈕讀不到就是 `Z19-25`、`Z19-32` 不通過。

---

## 2. 可執行驗收條件

### A. 設定（§2.6）與**三條**派工路徑（§4.1）

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z19-01` config 契約 | 契約 | 未設定過的 Agent `GET /v1/agents/{id}/config` 回 `200` 且**恰好**是 `{"config": {}, "updated_at": null}` —— `config` 這個 key 一定在、不是 `null`、不被省略。`PUT {"config":{…}}` 回 `200` 且與 `GET` 同形狀，`updated_at` 為 RFC 3339 UTC。第二次 `PUT` 一份**不含某個鍵**的文件後，`GET` 不再有那個鍵（整份取代，不是合併）。 |
| `Z19-02` 沒有 PATCH | 契約 | `PATCH /v1/agents/{id}/config` 不存在（`405` 或 `404`，不得 `200`），且不出現在 `GET /openapi.json`。 |
| `Z19-03` config 邊界 | 單元／契約 | **通過**：`{}`、值為 `null`／布林／數字／字串／陣列／三層巢狀物件都 `200`，且 `GET` 回來與送出去**深度相等**（特別是值為 `null` 的鍵不得被丟掉）；100 個頂層鍵 `200`。**擋下**：裸物件（沒有 `config` 包裝）、`config` 為 `null`／陣列／字串、鍵名 `1abc`／`a-b`／空字串／65 字元、101 個頂層鍵、序列化後 > 64 KiB，全部 `422 VALIDATION_ERROR` 且 DB 未變更。 |
| `Z19-04` **路徑一：一次性沙盒 bootstrap** | 整合 | 設定 config 與兩個機密後跑一筆 RUN mode 的 Run，`GET /internal/runs/{run_id}/bootstrap`（帶該 Run 的 run token）回應**頂層**有 `config`（與 `PUT` 的文件深度相等）與 `secret_names`（**已排序**的名字字串陣列）。沙盒內 `ctx.config` 讀到同一份值，寫進 Run output 後由公開 `GET /v1/runs/{id}` 讀回比對。 |
| `Z19-05` **路徑二：常駐容器工作項** | 整合 | 走預設路徑（常駐容器）建立 Run，容器以 container token 打 `GET /internal/agents/{agent_id}/work` 取得的工作項**頂層**含 `config` 與 `secret_names`，值與 `Z19-04` 相同；worker push 進 Redis 的工作項原文（直接讀 Redis key）也含這兩個欄位。沙盒內 `ctx.config` 拿到值，output 比對通過。**這條與 `Z19-04` 必須是兩個獨立的測試函式，不得共用同一條路徑作證。**（ZEN-012 `default_model`、ZEN-017 `ctx.llm()` 各踩過一次，這是第三次。） |
| `Z19-06` **路徑三：validation 沙盒** | 整合 | 一個在 `build()` 裡 `ctx.config.get("interval", "1h")` 並把值寫進 manifest 以外任何可觀測處的 fixture image，在 Agent 已有 config 的情況下註冊新版本 → 版本 `PUBLISHED`（不是 `FAILED`），且 validation 沙盒取得的 `config`／`secret_names` 與當下設定一致。反向：Agent 沒有任何 config 時同一 image 仍 `PUBLISHED`（`config` 為 `{}`，不是缺欄位）。**實作用哪條路把資料送進 validate 容器，必須在交付時寫出來**（見 §4 未決 U2）。 |
| `Z19-07` 改設定不重啟即生效 | 整合 | 常駐容器啟動後不重啟、不重發版本：`PUT` 新 config → 下一筆 Run 的 `ctx.config` 是新值（斷言容器 ID 與改設定前**相同**，證明沒有靠重啟達成）。 |
| `Z19-08` 進行中的 Run 不受影響 | 整合 | 一筆會跑 ≥ 3 秒的 Run 進入 `RUNNING` 後改 config，該 Run 的 output 仍是**派工當下**的舊值；同時送出的下一筆 Run 是新值。 |
| `Z19-09` **config 不得污染 manifest** | 整合／靜態 | `PUT` config 前後，`agent_versions.manifest` 與 `entrypoints` 在 DB 中 byte-identical；bootstrap 回應與工作項的 `manifest` 物件**沒有 `config` 鍵**；`GET /v1/agents/{id}/versions/{semver}` 的 `manifest` 也沒有。靜態：`grep -rn 'manifest\[.config.\]\|manifest.get("config"' packages/sdk apps` 無結果。 |
| `Z19-10` SDK `ctx.config` 讀新欄位 | 單元 | 建構 `ZentraContext` 時 manifest 裡**故意**塞 `{"config": {"poisoned": true}}`、頂層 config 給 `{"real": 1}` → `ctx.config == {"real": 1}`。`ctx.config` 回的是副本（改動不影響下一次讀取）。頂層缺 `config` 時是 `{}`，不是 `None`、不丟例外。 |
| `Z19-11` `ctx.secret_names` 形狀 | 單元 | `ctx.secret_names` 是名字字串的唯讀序列／集合，排序穩定；對它做 mutation 不影響內部狀態。`hasattr(ctx, "secrets")` 為 `False`，`dir(ctx)` 內沒有任何名稱含 `secret` 且會回傳值的 callable（`ctx.secret_names` 除外）。 |

### B. 機密（§2.7）：寫得進去，讀不出來

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z19-12` secrets 契約 | 契約 | `PUT /v1/agents/{id}/secrets/SMTP_PASSWORD {"value":"…"}` 回 `200`（或 `201`，擇一且與實作一致地寫進契約測試）且 body **恰好**四個欄位 `name`／`last_four`／`created_at`／`updated_at`。`GET /secrets` 回 `{"items":[…]}`，每列同形狀。重複 `PUT`：`created_at` 不變、`updated_at` 變。`DELETE` 回 `204`；再 `DELETE` 一次仍 `204`（冪等）；不存在的名字 `DELETE` 也 `204`。 |
| `Z19-13` `last_four` 遮罩 | 單元／契約 | 值 `"abcdefghmnop"` → `last_four == "mnop"`。值 `"ab"` → `last_four` **不等於** `"ab"`、長度為 2（等長遮罩）。值 `""` → `422`（1–4096 字元）。 |
| `Z19-14` 機密邊界 | 契約 | 名字 `1abc`／`a-b`／65 字元／空 → `422`；第 51 個機密 → `422`，第 50 個 `200`；值 4096 字元 `200`、4097 字元 `422`。 |
| `Z19-15` 沒有金鑰就拒絕 | 整合 | `ZENTRA_SECRET_KEY` 未設定時 `PUT` 回 `422 VALIDATION_ERROR`，且 `agent_secrets` **沒有新增任何列**（絕不退回明文儲存）。訊息要說得出是平台缺金鑰，不是使用者輸入錯。 |
| `Z19-16` 刪除被引用 → `409` | 整合 | 機密 `S` 被 §2.8 規則的 `auth_secret_name` 與 §2.9 的 `password_secret_name` 引用時，`DELETE /secrets/S` 回 **`409 VERSION_CONFLICT`**，`details.referenced_by` 列出**兩個**引用者（可定位到 `rule_id` / email-sender），且 `S` **仍在** `GET /secrets` 裡。解除兩個引用後 `DELETE` 回 `204`。**DB 層不得以 FK constraint error 代替**（回應必須是契約形狀，不是 `500`）。 |
| `Z19-17` **值永遠不出現在沙盒可見面** | 整合／安全 | 以 sentinel 當機密值，跑一筆會用到出口與寄信的 Run，然後在下列**每一處**掃描 sentinel 明文**與** DB 裡的 Fernet 密文字串，全部必須為 0 命中：bootstrap 回應全文、Redis 工作項原文、`GET /internal/agents/{id}/work` 回應、SSE 事件全文、`GET /v1/runs/{id}`、`GET /v1/agents/{id}`、`GET /secrets`、`GET /egress-rules`、`GET /email-sender`、`GET /egress-calls`、容器 `docker inspect` 與 `/proc/1/environ`、`/workspace` 內容、api 與 worker 的結構化日誌。另斷言 `secret_names` 的每個元素都是**純字串名字**（不是物件、沒有長度、沒有尾四碼）。 |
| `Z19-18` 取值的函式不存在 | 靜態＋契約 | 沒有任何公開路由回傳機密的值或密文（掃 `/openapi.json` 全部 response schema，不得有 `value`／`ciphertext`／`plaintext` 欄位）。機密服務只暴露 metadata 查詢與給 Gateway 用的解密路徑，**API router 不 import 解密函式**（`grep` 證明），沿用 §2.1／ADR-0011 §3 既有形狀。 |

### C. 出口白名單與 proxy（§2.8、§4.2、§4.3）

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z19-19` 規則 CRUD 契約 | 契約 | `POST` 回 `201` 且欄位恰為 §2.8 的九個（`rule_id`/`agent_id`/`host`/`methods`/`path_prefix`/`auth_secret_name`/`auth_header`/`description`/`created_at`/`updated_at`）。`GET` 清單每列同形狀，`PUT` 整條取代（未給的選填欄位回到預設／`null`，不是保留舊值），`DELETE` 回 `204`。 |
| `Z19-20` E1 host 合法性 | 單元／契約 | `422`：`*.binance.com`、`*`、`https://api.binance.com`、`api.binance.com:443`、`api.binance.com/api`、`API.Binance.com`（大寫）、`1.1.1.1`、`2606:4700::1111`、空字串、254 字元。`200/201`：`api.binance.com`、`api.okx.com`、`smtp.gmail.com`。 |
| `Z19-21` E2 methods | 契約 | 不給 `methods` → 預設 `["GET"]`；`[]` → `422`；`["PUT"]`／`["DELETE"]`／`["PATCH"]`／`["get"]` → `422`；`["GET","POST"]` → `201`。 |
| `Z19-22` E3 認證成對 | 契約 | 只給 `auth_secret_name`、只給 `auth_header` → 各 `422`；`auth_secret_name` 指向不存在的機密 → `422`；`auth_header` 為 `Accept`／`Content-Type`／`Host`／`Content-Length`（大小寫不敏感）→ `422`；`X-MBX-APIKEY` + 存在的機密 → `201`。 |
| `Z19-23` 唯一與上限 | 契約 | 同一 Agent 重複 `host` → `409 VERSION_CONFLICT`；不同 Agent 同 `host` → `201`。第 21 條規則 → `422`，第 20 條 `201`。 |
| `Z19-24` **預設拒絕** | 整合 | 一條規則都沒有 → 任何 `POST /internal/egress/request` 回 `403 EGRESS_BLOCKED`、`details.reason == "NO_RULES"`、`retryable == false`。加一條 `api.binance.com` 後：打 `api.okx.com` → `HOST_NOT_ALLOWED`；`POST` 到只開 `GET` 的規則 → `METHOD_NOT_ALLOWED`；`path_prefix="/api/v3/"` 時打 `/api/v1/x` → `PATH_NOT_ALLOWED`。四種 reason 逐一比對 §4.3 枚舉。 |
| `Z19-25` 跨 Agent 隔離 | 安全／整合 | Agent A 的 run token 打一個**只在 Agent B 白名單上**的 host → `EGRESS_BLOCKED / HOST_NOT_ALLOWED`；Agent A 的規則若引用名字相同但屬於 B 的機密，注入的仍是 A 自己的值（或 `422`），不得跨 Agent 取用。 |
| `Z19-26` E4 scheme 與 redirect | 整合 | `http://…` → `EGRESS_BLOCKED / SCHEME_NOT_ALLOWED`（`ftp://`、無 scheme、`HTTPS://` 大寫也各驗一次）。上游回 `302` → `EGRESS_BLOCKED / REDIRECT`，`details.location` 等於上游的 `Location`，**且 stub 只收到一個請求**（證明沒有跟隨）。 |
| `Z19-27` E3 沙盒不得自帶 header | 契約 | `headers` 帶 `Authorization`／`Cookie`／`X-Api-Key`／`User-Agent` 任一 → 被擋且 **`details` 指名是哪個 header**，絕不是默默丟掉（斷言 stub 沒收到該 header，且回應中出現該 header 名）。`Accept`、`Content-Type` → 放行。（狀態碼採 §4.2 第 2 點的 `422 VALIDATION_ERROR`；與 §4.3 的 `HEADER_NOT_ALLOWED` 衝突，見 §4 未決 U1 —— **裁決前，測試對「有沒有指名 header」的斷言不受影響**。） |
| `Z19-28` **E5 SSRF：解析後檢查** | 安全（最高風險） | 白名單放一個測試控制的主機名，讓它分別解析到 `127.0.0.1`、`10.0.0.5`、`172.17.0.2`（`postgres` 的實際容器 IP）、`192.168.1.1`、`169.254.169.254`、`::1`、`fd00::1`、`::ffff:127.0.0.1` —— **八種各一條**，全部 `403 EGRESS_BLOCKED / PRIVATE_ADDRESS`。指到 `postgres` 那條要額外證明**沒有任何連線真的送達**（`postgres` 連線數／socat 監聽器無新連線）。`Z19-20` 已擋 IP 字面值，這條擋的是**名字解析之後**才看得到的位址。 |
| `Z19-29` **DNS rebinding** | 安全 | 兩次解析回不同答案（第一次公網、第二次 `127.0.0.1`）的主機名：proxy 必須**只解析一次，並直接連那個 IP**，帶 `Host` header 與該名稱的 SNI。以可觀測 transport 斷言「連線目的 IP == 檢查過的 IP」。**做不到就在交付時明說做不到，標 `未覆蓋`，不得標 `passed`** —— 這是 SPEC §5 硬規則 5，漏了等於把 `postgres`／`redis`／`registry` 開給沙盒。 |
| `Z19-30` 檢查順序 | 單元／整合 | 依 spec §5.3：沒有規則時，即使 url 是 `http://` 也回 `NO_RULES`（便宜的先做）；host 不在白名單時 **resolver 未被呼叫**（斷言 fake resolver 呼叫次數為 0）。這條擋的具體錯：先解析再檢查，等於讓沙盒用任意網址驅動 `app` 發 DNS 查詢，那本身就是一條外洩通道。 |
| `Z19-31` 平台注入認證 | 整合 | 規則設 `auth_secret_name=BINANCE_KEY`、`auth_header=X-MBX-APIKEY`：stub 收到的請求含該 header 且值等於機密明文；沙盒送出的 request body／headers 完全沒有它；平台固定送 `User-Agent: Zentra-Egress/1`。 |
| `Z19-32` 信封語意 | 整合 | 上游回 `418`／`429`／`500` → Gateway 一律 HTTP **`200`**，body 為 `{status, headers, body}`，`status` 等於上游碼，**不是** §0 錯誤格式。這是 `Z19-53`（Binance→OKX 備援）能成立的前提。 |
| `Z19-33` 回應 header 白名單 | 整合 | stub 回 `set-cookie`、`authorization`、`x-internal`、`content-type`、`retry-after`、`x-ratelimit-remaining` → 回應 `headers` 只含 `content-type`／`content-length`／`date`／`retry-after`／`x-ratelimit-*`；`set-cookie` 與 `authorization` 0 命中（大小寫不敏感比對）。 |
| `Z19-34` E6 上限 | 整合 | request body 64 KiB `200`、64 KiB + 1 B → `REQUEST_TOO_LARGE`；response 1 MiB `200`、超過 → `RESPONSE_TOO_LARGE` 且**回應 body 不是被截斷的內容**；`timeout_seconds` 16 → `422`（見 §4 未決 U4）；`EGRESS_MAX_CALLS_PER_RUN=2` 時第 3 次 → `RUN_CALL_LIMIT`，且**換一筆 Run 後計數歸零**。 |
| `Z19-35` `EGRESS_FAILED` 的分界 | 整合 | 政策放行後：DNS 解析失敗、連線被拒、上游逾時 → `502 EGRESS_FAILED`、`retryable == true`。政策擋下 → `403 EGRESS_BLOCKED`、`retryable == false`。兩者 body 都符合 §0 錯誤形狀（`code`/`message`/`run_id`/`retryable`/`details` 五個 key 都在，`run_id` 是發起這次呼叫的 Run）。 |
| `Z19-36` `ctx.http` 形狀 | 單元／整合 | SDK 的 `ctx.http` 回信封（有 `status`／`headers`／`body`），上游非 2xx **不丟例外**；沙盒內 `import requests` 或直連外網仍然不通（`Z19-42`）。docstring 寫明「狀態碼在 `status` 裡」。 |

### D. 寄信（§2.9、§4.4）

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z19-37` 寄件者契約 | 契約 | 未設定時 `GET` 回 `configured: false` 且其餘欄位 `null`（key 都在）。`PUT` 回 `configured: true` + 七個欄位。`DELETE` `204`。 |
| `Z19-38` M2 沒有密碼欄位 | 契約 | body 出現 `password`／`smtp_password`／`secret` → `422`，**訊息要指出該去 `/secrets` 存**。`password_secret_name` 指向不存在的機密 → `422`。回應在任何情況下都沒有密碼欄位。 |
| `Z19-39` 寄件者邊界 | 契約 | `smtp_port` 2525／0／`null` → `422`，`587`／`465`／`25` → `200`。`allowed_recipients` 缺／`[]`／11 個／`*@example.com`／`example.com` → `422`；1 個與 10 個精確地址 → `200`。 |
| `Z19-40` **M1 全有全無** | 整合 | `allowed_recipients=["a@x.com"]`，寄給 `["a@x.com","b@x.com"]` → `403 EGRESS_BLOCKED / RECIPIENT_NOT_ALLOWED`，`details.recipient` 指名 `b@x.com`，**SMTP stub 收到 0 封信**（連 `a@x.com` 那封也沒寄）。反向：只寄 `["a@x.com"]` → `202` 且 stub 收到 1 封。 |
| `Z19-41` 未設定寄件者不靜默 | 整合 | 沒有 email-sender（或已 `DELETE`）時寄信 → `403 EGRESS_BLOCKED / SENDER_NOT_CONFIGURED`，**不是 `202`、不是靜默丟棄**，且寫一列 `egress_calls`。 |
| `Z19-42` M3 上限 | 整合 | `EMAIL_MAX_PER_RUN=1`：同一 Run 第 2 封 → `RUN_EMAIL_LIMIT`。`EMAIL_MAX_PER_HOUR=2`：**跨 Run 累計**到第 3 封 → `HOURLY_EMAIL_LIMIT`。`text`+`html` 合計 256 KiB `202`、超過 → `BODY_TOO_LARGE`。`subject` 201 字元、`to` 為 `[]` 或 11 個、`text` 與 `html` 皆缺 → `422`。 |
| `Z19-43` M5 TLS 與認證 | 整合 | 不支援 STARTTLS 的 stub → `EGRESS_BLOCKED / TLS_REQUIRED`，且 **stub 沒有收到 `DATA`**（信件內容沒有以明文送出）。認證失敗 → `EGRESS_BLOCKED / SMTP_AUTH`、`retryable == false`。SMTP 連不上／逾時 → `502 EGRESS_FAILED`、`retryable == true`。`465` 與 `587` 各跑一次成功路徑。 |
| `Z19-44` 請求不能奪權 | 契約 | request 帶 `from`／`smtp_host`／`smtp_port`／`password` → `422`。成功回 `202 {"accepted": true, "message_id": "<…>"}`。不冪等：完全相同的 request 送兩次 → stub 收到 **2** 封、`egress_calls` **2** 列。 |

### E. 稽核（§2.10、E7）

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z19-45` 成功與被擋都寫 | 整合 | 同一筆 Run 依序做：成功 HTTP、被擋 HTTP（`HOST_NOT_ALLOWED`）、成功 EMAIL、被擋 EMAIL（`RECIPIENT_NOT_ALLOWED`）→ `GET /v1/agents/{id}/egress-calls?run_id=<id>` 回**恰 4 列**，欄位逐欄位符合 §2.10。被擋的兩列：`status_code == null`、`error` 同時說得出 `EGRESS_BLOCKED` 與 `reason`。 |
| `Z19-46` `path` 不含 query | 整合 | 以 `…/api/v3/klines?symbol=BTCUSDT&token=<sentinel>` 呼叫後，DB `egress_calls.path` 與 API 回應的 `path` 都是 `/api/v3/klines`；**全表掃描 sentinel 0 命中**（稽核表不得變成第二個機密外洩點）。 |
| `Z19-47` EMAIL 列形狀 | 整合 | `kind == "EMAIL"` 時 `host` 是 SMTP 主機、`path` 是收件人以 `", "` 串接（多人時順序穩定）、`method`／`rule_id`／`status_code` 為 `null`、`request_bytes > 0`。 |
| `Z19-48` 篩選與分頁 | 契約／整合 | `kind`／`run_id`／`since`／`until`／`host`／`status_code`／`errors_only`／`limit`／`cursor` 各驗一次且可疊加。`errors_only=true` 等於 `error IS NOT NULL OR status_code >= 400`（造一列 `status_code=404 error=null` 驗它有被收進來）。`next_cursor` key 一定在、無下一頁為 `null`；用 `limit=2` 翻完 5 列不重不漏。 |
| `Z19-49` 稽核 100% 覆蓋 | 整合 | 一筆 Run 做 N 次出口（含被擋、含逾時、含 `EGRESS_FAILED`）→ `egress_calls` 恰 N 列。SPEC §10 要求 100% 留存，少一列就是不通過。`latency_ms`／`request_bytes`／`response_bytes` 非 `null` 時必須是非負整數。 |
| `Z19-50` 刪除語意 | 整合／migration | 刪一條 egress 規則 → 它產生過的 `egress_calls` 列**還在**且 `rule_id` 變 `null`（`ON DELETE SET NULL`，不是 CASCADE）。`DELETE /v1/agents/{id}` 回 `204` 後，`agent_settings`／`agent_secrets`／`agent_egress_rules`／`agent_email_sender`／`egress_calls` 五張表對該 `agent_id` 各 0 列（§2.5 清單）。全新 DB `alembic upgrade head` 後五張表的欄位、型別、PK、FK、`CHECK`（`allowed_recipients` 1–10）、兩個索引都存在。 |

### F. 核心立場護欄（P1／P4／P5）與契約一致性

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z19-51` **沙盒 env 精確集合（長期護欄）** | 單元＋Docker 安全 | 單元：`SandboxSpec` 在 RUN mode 多帶／少帶任一變數都丟錯；斷言期望集合**恰為** RUN `{ZENTRA_GATEWAY_URL, ZENTRA_RUN_ID, ZENTRA_RUN_TOKEN}`、SERVE `{ZENTRA_GATEWAY_URL, ZENTRA_AGENT_ID, ZENTRA_CONTAINER_TOKEN, ZENTRA_MAX_CONCURRENT_RUNS}`、VALIDATE `{ZENTRA_GATEWAY_URL, ZENTRA_AGENT_VERSION_ID, ZENTRA_VALIDATION_TOKEN}`。真容器：`/proc/1/environ` 的 `ZENTRA_*` 恰為上述集合，且整份 env 不含機密名字對應的值、不含 `OPENAI`／`SMTP`／`POSTGRES`／`REDIS`／`DOCKER` 任何 key。另斷言 `git diff main -- apps/sandbox/drivers/base.py` 中 `_validate_environment()` 的內容**一個字都沒改**。 |
| `Z19-52` 沙盒網段沒有變寬 | Docker 安全 | 沙盒內連 `postgres:5432`、`redis:6379`、`registry:5000`、`1.1.1.1`、`169.254.169.254` 全部失敗；**反向斷言：連 `api:8000/internal/runs/{id}/bootstrap`、`/internal/egress/request`、`/internal/notify/email` 帶 run token 全部成功**（漏了反向斷言等於沒測）。`docker inspect` 顯示容器只接 `internal: true` 的 `zentra-sandbox-net`；`compose.yaml` 中 `postgres`／`redis`／`registry`／`worker` 仍未接 sandbox 網段，只有 `api` 兩邊都接。 |
| `Z19-53` token scope | 安全／契約 | container token 與 validation token 打 `/internal/egress/request`、`/internal/notify/email` → `403 PERMISSION_DENIED`（兩條路由 × 兩種 token = 四個斷言）。缺／無效 token → `401`。Run 進終態後，同一 run token 再打這兩條 → `401`（已撤銷）。稽核列的 `run_id` 一律取自 token，request body 內若夾帶 `run_id`／`agent_id` 不得被採用（`422` 或忽略，二擇一但要一致）。 |
| `Z19-54` P1 | 靜態 | 新增的 API／worker 程式碼不 `exec`／`eval`／`importlib`／`__import__`／`subprocess` 使用者模組（`grep -rn` 在本 feature 新增檔案上為 0 命中）。出口 proxy 只轉傳位元組，不解析、不執行上游回應。 |
| `Z19-55` P5 | 整合／schema | 五張新表**沒有** `agent_version_id` 欄位（三層綁 Agent 不綁版本）。改 config／機密／規則後 `agent_versions` 任一列的 `version_id`／`manifest`／`entrypoints`／`published_at` 均未變。 |
| `Z19-56` 契約一致性與已移除的值 | 契約／靜態 | 全部新回應逐欄位對 v0.12.0（欄位名、型別、必填、`null` 語意：**`null` 欄位要出現且為 `null`，不得被省略**）。錯誤 code 只出現 §0 枚舉值；本 feature 只新增 `EGRESS_FAILED`。`grep -rn "WAITING_INPUT\|tool\.call\|tool\.result\|interrupt\|tenant_id\|source_type\|alias"` 在新增程式碼上 0 命中。`/openapi.json` 不含任何 `/internal/*` 路由，也不含新端點以外的東西。Run 狀態與錯誤 code 一律引用枚舉常數，不是散落的字面值。 |

### G. Console（ui-contract §5.16–§5.19）

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z19-57` `agent-config-card` | E2E | 卡片**第一句**是「這裡不是放密碼的地方」語意的文案且指向機密卡（斷言 DOM 文字與連結 target）。編輯器是 `<textarea>`；輸入非法 JSON 時**前端就擋**（沒有發出 `PUT` 請求）並設 `aria-invalid`、指出行號。CTA 文案含「取代整份設定」，確認視窗含「完整覆蓋」語意。未設定時顯示 `{}` 而非空白。卡片上寫明「立刻對所有版本生效，下一筆 Run 就是新值；已經在跑的不受影響」。屬性前綴 `data-config-*`。五態齊全，成功以 `aria-live` 宣告。 |
| `Z19-58` `agent-secrets-card` | E2E | 每列只有名字／尾四碼／更新時間；**沒有任何「顯示」按鈕**；攔截網路證明回應不含值。值輸入框 `type="password"`，送出成功後 DOM 中該欄位為空。卡片寫明「這些值不會進到沙盒」與「以名字引用」。刪除被引用的機密（後端 `409`）時，畫面**列出 `details.referenced_by`** 並提供跳到對應卡片的連結。未設 `ZENTRA_SECRET_KEY` 的 `422` 有專屬文案。前綴 `data-secret-*`。 |
| `Z19-59` `egress-card` | E2E | 空狀態文案說出後果（「連不到任何外部網站」），不是「沒有資料」。規則列顯示 host／methods／`path_prefix`／有無認證，`POST` 標記使用 `--warning` token（斷言 class／computed color 來自 token 而非硬編碼色碼）；帶認證時顯示 secret **名字**。新增表單 `methods` 預設只勾 `GET`，勾 `POST` 就地顯示警語。`auth_secret_name` 是下拉且選項來自 `GET /secrets`，清單空時 `disabled` 並指向機密卡。寄件者區塊**沒有密碼欄位**。`allowed_recipients` 為空時 CTA `disabled` 且說明理由。底部有連到稽核的連結。前綴 `data-egress-*`。 |
| `Z19-60` `egress-calls-panel` | E2E | 一張表同時列出 `HTTP` 與 `EMAIL`（沒有分頁籤）。被擋下的列顯眼，`reason` 譯成人話（至少驗 `HOST_NOT_ALLOWED` → 「主機不在白名單」）並附「去加一條規則」連結。每列連到該筆 Run。`path` 不以完整網址樣式呈現。預設載入最近 50 筆且**不自動輪詢**（觀察 10 秒內無額外請求）。 |
| `Z19-61` 前端鐵則 | 靜態／E2E | 新增的 `apps/console/**` 檔案：`innerHTML` 0 命中（API 字串一律 `textContent`；用含 `<img onerror>` 的 config 值與機密名字做 XSS 實測，不得執行）。無 npm／bundler／framework／TypeScript 引入。無硬編碼色碼與間距（只用 ui-contract token）。`api.js` 新增函式的欄位名與 v0.12.0 逐一吻合。 |

### H. 範例 agent（`examples/crypto-quant-agent/`）—— 驗收載體

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z19-62` 端到端跑通 | E2E | build image → `docker push` → 註冊版本 → `PUBLISHED`；設好 config（`symbols`／`interval`／`rsi_oversold`）、機密 `SMTP_PASSWORD`、兩條 egress 規則（Binance／OKX）、email-sender → 打 REST 觸發一筆 Run → `SUCCEEDED`。把 `symbols` 改成另一組後再跑一筆，output 隨之改變（證明 `ctx.config` 真的被吃進 `signals.analyse(c, cfg)`）。 |
| `Z19-63` 備援切換 | E2E | 以 stub 讓 Binance 那條回 `500`（**不得真的去打 Binance**）→ agent 改打 OKX，Run 仍 `SUCCEEDED`；`egress_calls` 至少兩列：Binance `status_code=500`、OKX `status_code=200`。這條同時是 `Z19-32` 信封語意的端到端證據。 |
| `Z19-64` fail fast | E2E | 刪掉 `SMTP_PASSWORD` 後跑一筆 → agent 在**第一個節點**就說出缺 `SMTP_PASSWORD`（事件或 error message 含該名字），不是跑完整段分析才在寄信那步失敗。 |
| `Z19-65` `build()` 不硬性依賴 config | 整合 | 把 config `PUT` 成 `{}` 之後，既有 `PUBLISHED` 版本**仍能建立並完成 Run**（不得在 build 階段死掉）。這條擋的是 ADR-0014 §7 點名的坑：版本狀態還是 `PUBLISHED`，但每一筆 Run 都掛。 |
| `Z19-66` 既有運算模組未被重寫 | 靜態 | `indicators.py`／`harmonics.py`／`signals.py` 的公開簽章維持不變（特別是 `analyse(c, cfg)`），`agent.py` 把 `ctx.config` **原樣**餵進去、全部以 `.get()` 帶預設值，沒有另外發明一套設定格式。三支模組仍不 import SDK。 |
| `Z19-67` README | 靜態 | README 寫明「由外部定時打 REST 觸發（cron / 任何排程器），平台不負責排程」，並列出這支 agent 需要哪些 config 鍵、哪些機密名字、哪些 egress 規則。 |

---

## 3. 品質閘門

| Gate | ZEN-019 必要證據 |
|---|---|
| G1 立場 | `Z19-51`～`Z19-55` 全綠且有**真容器**證據。報告要明講：「Docker 四層隔離與沙盒網段未放寬；**通用出口 proxy 是一條真正的外洩通道**（ADR-0014 §6），白名單擋得住送到哪、擋不住送了什麼；核心層逃逸未覆蓋，本架構不防這一類。」**不可只寫「安全測試通過」。** |
| G2 契約 | 14 條公開端點 + 2 條 Gateway 路由逐欄位對 v0.12.0 全綠；`details.reason` 只用 §4.3 枚舉；`/openapi.json` 無 `/internal/*`。 |
| G3 三條派工路徑 | `Z19-04`／`Z19-05`／`Z19-06` 是**三個獨立的測試函式**且全綠。只有 bootstrap 綠 = G3 不通過（這是第三次踩同一個坑）。 |
| G4 機密邊界 | `Z19-17` 的全域 sentinel 掃描 0 命中；`Z19-18` 的「取值函式不存在」成立。任一命中即 G4 不通過，且應在 `coordination/DRIFT.md` 開 🔴 P4。 |
| G5 SSRF | `Z19-28` 八條全綠；`Z19-29` 綠或**明確標未覆蓋並寫進交付說明**。`Z19-29` 若未覆蓋，交付文字必須是「DNS rebinding 未防護」，不得省略。 |
| G6 稽核 | `Z19-45`～`Z19-49` 全綠，含被擋下的呼叫。 |
| G7 收尾 | `make clean-test-data` 已執行，Console 上只剩內建 fixture；CHANGELOG 有 ZEN-019；BOARD 無未解 `SPEC-QUESTION`；DRIFT 無本任務未解漂移。 |

判定規則：任何斷言失敗、未執行、或因環境缺件 `skipped`，都不得標為通過。
真 Docker／PostgreSQL／SMTP stub 不可用時，可保留單元與契約結果，但 G1／G3／G5／G6 不通過。

---

## 4. 契約裡講不清楚、會影響「驗不驗得了」的地方（待裁決）

> 這幾條不是挑語病 —— 每一條都會讓對應的測試寫不出唯一正確答案。

| # | 問題 | 影響 |
|---|---|---|
| **U1** | **自帶 header 到底回什麼？** §4.2 第 2 點與 spec §5.3 第 4 步說 `422 VALIDATION_ERROR`；§4.3 的 reason 表又有 `HEADER_NOT_ALLOWED`（那是 `EGRESS_BLOCKED` 403 專用）。兩者只能成立一個 | `Z19-27` 的狀態碼斷言。目前暫定 `422`，並要求 `details` 指名 header |
| **U2** | **validation 沙盒怎麼拿到 `config` / `secret_names`？** §4.1 第 4 點要求它拿得到，但 §4 的路由表沒有對應端點（只有 `POST /internal/versions/{id}/validation`），而 env 是精確集合不能加變數 | `Z19-06` 驗不了「怎麼送」，只能驗「結果對不對」。若實作新增了一條路由，那條路由**不在 v0.12.0 契約裡**，屬契約漂移，要補契約 |
| **U3** | **被擋下的呼叫要寫稽核列 —— 那 `422` 的呼叫算不算？** `REQUEST_TOO_LARGE` 有 reason（是 `EGRESS_BLOCKED`），但 header 不合法是 `422`。`422` 要不要寫 `egress_calls` 沒寫 | `Z19-45`／`Z19-49` 的列數斷言 |
| **U4** | **`timeout_seconds` 超過 15 是 `422` 還是夾到 15？** 契約只寫「上限 15」 | `Z19-34`。目前暫定 `422`（與 `timeout_seconds=901` 回 `422` 的既有慣例一致） |
| **U5** | **限額的計數基準**：`EGRESS_MAX_CALLS_PER_RUN`／`EMAIL_MAX_PER_RUN` 是算「送出去的」還是「包含被擋下的」？ | `Z19-34`／`Z19-42`。差別很實際：100 次被擋的呼叫算不算用完配額 |
| **U6** | **`EMAIL_MAX_PER_HOUR` 的時窗**：滾動 60 分鐘還是整點桶？ | `Z19-42` 的跨 Run 斷言寫法 |
| **U7** | **`last_four` 的遮罩字元未定義**（「不足四碼時是等長遮罩」） | `Z19-13` 只能斷言「不等於明文且等長」；Console 顯示也少一個固定值 |
| **U8** | **成功路徑在「不打外網」前提下無法驗**（§1.3）：所有本機 stub 都在私有網段，會被 `PRIVATE_ADDRESS` 正確地擋掉 | 影響 `Z19-31`～`Z19-34`、`Z19-62`、`Z19-63`。需要裁決用 fake resolver（建議）還是測試專用旁路（要另加「預設關閉」的護欄） |
| **U9** | **`PUT /secrets/{name}` 的成功狀態碼**：§2.7 只給了 body，沒寫 `200` 還是 `201` | `Z19-12`。擇一並與契約一致即可 |

---

## 5. 明確不驗（範圍外，寫進來是為了不被「順手」補上）

- **排程器 / cron。** 平台刻意不做，範例 agent 由外部定時觸發。不驗「平台有沒有排程」。
- 設定與機密的**版本史**、舊 Run 重現當時設定值（ADR-0014 §7 已知代價）。
- `egress_calls` 的保留期與彙總（BOARD 待辦）。
- 出口**內容**檢查 / DLP —— 白名單只擋「送到哪」（ADR-0014 §6）。
- 交易所專用端點、K 棒快取（使用者裁決 4）。
- 核心層逃逸、gVisor、沙盒與主機的實體隔離 —— ADR-0005 的已知取捨，固定 `skipped`，
  **不是缺陷，也不是通過**，不要為它開 bug。

---

## 6. 開發前狀態

### 6.1 目前

- 上述 **67 條**全部**未跑**。本文件不宣稱任何功能通過。
- `tests/zen019/` 由 @builder 先寫；@reviewer 在驗收時依本文件補強缺口，
  特別是 `Z19-05`（工作項）、`Z19-28`／`Z19-29`（SSRF）、`Z19-40`（全有全無）、`Z19-17`（外洩掃描）。
- §4 的 U1–U9 在裁決前，對應斷言以本文件的「暫定」值執行；裁決後以契約為準並更新此節。

### 6.2 驗收後要填的兩張表（留白）

| 判定不通過的斷言 | 證據（指令 + 輸出） |
|---|---|
| （驗收時填） | |

| 未覆蓋（不得計入通過） | 原因 |
|---|---|
| 核心層逃逸 / gVisor / 實體隔離 | ADR-0005 已知取捨 |
| （其餘驗收時填） | |
