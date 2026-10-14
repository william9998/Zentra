# ADR-0014：每個 Agent 自己的設定、機密與對外能力

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-30 |
| 決策者 | @architect（四個取捨由使用者直接裁決，見 §決定的來源） |
| 相關 | 修改 [api-contract](../api-contract.md) → v0.12.0（**R1 後為 v0.12.1**）、[SPEC](../SPEC.md) → v0.5.0、[ui-contract](../ui-contract.md) → v0.9.0；落地於 ZEN-019；延伸 [ADR-0011](ADR-0011-per-agent-llm-credentials.md)；**與 P4 的關係是這份 ADR 最重要的一段（§6）**；P1／P2／P3 不受影響 |
| 修訂 | **[R1](#修訂-r12026-09-30)（2026-09-30）：validation 沙盒一律用空的 `config`／`secret_names`**，推翻 §7 原本「帶當下設定」的寫法 |

## 背景

要在 `examples/` 放一支加密貨幣量化指標 agent：抓 K 棒 → 算指標與諧波型態 →
觸發訊號時寄 email。把它攤開來，平台缺的不是一個功能，是三件互不相同的事：

| 缺的東西 | 現況 |
|---|---|
| **可變設定**（追蹤哪些交易對、RSI 門檻、K 棒週期） | `ctx.config` 是空殼。[`packages/sdk/zentra_sdk/context.py`](../../packages/sdk/zentra_sdk/context.py) 有這個 property，SPEC §8.1 也把它列為 MVP 的四個 ctx 能力之一，但**全專案沒有任何一行程式碼往裡面塞東西**，它永遠回 `{}` |
| **通用機密**（SMTP 應用程式密碼、交易所 API key） | 只有 `agent_llm_credentials` 一張專用表（ADR-0011），它只裝 LLM 那一把 |
| **對外能力**（打 Binance、寄信） | 沙盒網段 `internal: true`（`compose.yaml`），一行 `requests` 都出不去。這是 P4 的第一層，不是疏漏 |

這三件事常被混成一句「讓 agent 能設環境變數」。**它們的安全性質完全不同**，
混在一起做出來的東西一定會在某一邊過寬：設定要看得見才好維運，機密要看不見才叫機密，
對外能力則是三者中唯一會真的把資料送出機房的。所以這份 ADR 的第一個決定是**把它們拆開**。

### 決定的來源

四個取捨由使用者直接裁決，本 ADR 不重新開放討論，只負責把它們寫成契約並定價：

1. 四項全做（設定、機密、HTTP 出口、寄信），含 Console UI。
2. **對外出口做成通用白名單 proxy，不是窄的專用端點。** 架構師原本推薦窄端點
   （只能送 `symbol` / `interval` / `limit` 的 `/internal/market/klines`），使用者選了通用 proxy，
   理由是彈性。代價與緩解寫在 §4 與 §6，**不是把它當成沒代價**。
3. 寄信用 Gmail SMTP + 應用程式密碼；密碼存成該 Agent 的一個 secret。
   SMTP 不是 HTTP，**不走通用 proxy**，獨立一條 Gateway 能力。
4. 行情主源 Binance、備援 OKX。**平台不做交易所專用端點、不做 K 棒快取** ——
   agent 自己打，失敗自己換，平台只負責白名單放行與稽核。

## 決定

### 三層模型

| 層 | 裝什麼 | 到得了沙盒嗎 | 誰在用它 |
|---|---|---|---|
| **L-設定** `agent_settings` | 非機密的可變參數（交易對清單、門檻、收件人） | **會**，隨每筆 Run 送進 `ctx.config` | agent 程式碼直接讀 |
| **L-機密** `agent_secrets` | Fernet 加密的字串（SMTP 密碼、交易所 key） | **永遠不會**。沙盒只拿得到**名字**清單 | 平台在代打外部服務時解密使用 |
| **L-能力** `agent_egress_rules` + `agent_email_sender` | 「這個 Agent 准許往哪裡送東西」 | 規則本身不進沙盒；沙盒只看得到請求成不成功 | Internal Gateway 執行 |

三層都**綁 Agent 不綁版本**，鍵都是 `agent_id`（`agent_egress_rules` 是 `agent_id` +
自己的 `rule_id`）。這直接沿用 ADR-0011 §1 的結構與理由，不重新論證：
版本不在鍵裡，所以發新版自動繼承、更新時所有版本立刻一起變。

**刪 Agent 時三層全部硬刪**，FK `ON DELETE CASCADE`。這對齊
[ADR-0013](ADR-0013-hard-delete-agents-and-images.md) 的現況（`DELETE /v1/agents/{id}`
是**硬刪**，不是 ADR-0011 §5 寫的軟刪）。

### 1. 設定走 bootstrap／工作項，不走 OS 環境變數

**不放寬 [`apps/sandbox/drivers/base.py`](../../apps/sandbox/drivers/base.py) 的
`_validate_environment()`。** 那裡對每個 mode 做「多一個少一個都擋」的精確集合檢查，
是 P4 的第一道關：多注入一個值會在建立容器時就失敗，而不是等到有人發現它進了沙盒。

理由跟 api-contract §4 已經為 `default_model` 寫過的一模一樣：

- **常駐容器一個容器服務多筆 Run**（[ADR-0012](ADR-0012-resident-agent-container.md)）。
  設定寫進容器 env，就做不到「改完下一筆生效」—— 要重啟容器才會變。
- 併發時 env 是容器層級的共享狀態，**會串到別人身上**。同一個容器裡同時跑三筆 Run，
  沒有一個正確的 env 值可以同時屬於它們三個。

所以設定掛在 `ctx.config`，**每筆 Run 重新帶**。兩條派工路徑都要帶：

| 路徑 | 誰在用 | 帶法 |
|---|---|---|
| `GET /internal/runs/{run_id}/bootstrap` | 一次性沙盒（RUN mode） | 回應新增頂層 `config` 與 `secret_names` |
| Redis 工作項 → `GET /internal/agents/{id}/work` | **常駐容器** | 工作項新增 `config` 與 `secret_names` |

**只改一條就等於沒改** —— 常駐容器走的是工作項那條，而那正是預設路徑。

**`config` 是頂層欄位，不得合併進 `manifest`。** SDK 現在的 `ctx.config` 讀
`manifest["config"]`，這要改。manifest 是版本的不可變內容（validation 時抽出、
存進 `agent_versions.manifest`），把可變設定攪進去會讓「manifest 是不可變的」
這句話在程式碼裡不再成立，而那是 P5 在 runtime 這一側唯一的實體。

### 2. 機密：寫得進去，讀不出來，而且不進沙盒

`agent_secrets(agent_id, name, ciphertext, last_four, created_at, updated_at)`，
主鍵 `(agent_id, name)`。用 **Fernet 加密**，不是雜湊 —— 這裡要把明文拿去登入 SMTP，
必須解得回來。（下游 API key 是 SHA-256 雜湊，那個只需驗證身分。這兩種模式別搞混，
api-contract §2.3 第 1 點已經寫過一次。）

加密原語沿用既有的 [`apps/api/services/secrets.py`](../../apps/api/services/secrets.py)，
不新寫：沒設 `ZENTRA_SECRET_KEY` 就 `422` 拒絕寫入，不退回明文。

**沙盒拿得到的只有名字。** bootstrap 與工作項帶 `secret_names: ["SMTP_PASSWORD", …]`，
排序後的字串陣列，沒有值、沒有尾四碼、沒有長度。

為什麼連名字都要給：不給的話，「這個 Agent 忘了設 SMTP 密碼」這件事，agent 程式碼
唯一的發現方式是先跑完整段分析、到寄信那一步才收到錯誤。給了名字，它可以在
第一個節點就 fail fast 並說出缺哪一個。**名字是維運者自己取的、值沒有外流**，
成本是零。對應的 ctx 能力是 `ctx.secret_names`（唯讀的名字集合）——
**`ctx.secrets`（取值）不存在，而且這份 ADR 不打算讓它存在**。

### 3. 能力一：通用 HTTP 出口 proxy（使用者裁決 2）

`POST /internal/egress/request`，run token 認證，送 `{url, method, headers, body, timeout_seconds}`，
回一個**信封**：`{status, headers, body}`。

> **信封的關鍵性質**：只要請求真的送出去了，Gateway 一律回 `200`，上游的狀態碼放在
> `status` 裡。上游回 418／429 不是平台錯誤。**這是使用者裁決 4（Binance 主源、OKX 備援）
> 能實作的前提** —— agent 要看得到狀態碼才切得了備援。

白名單是 `agent_egress_rules`，一條規則長這樣（完整欄位見 api-contract §2.6）：

```jsonc
{ "host": "api.binance.com", "methods": ["GET"], "path_prefix": "/api/v3/",
  "auth_secret_name": null, "auth_header": null }
```

**硬性規定（這些是使用者選通用 proxy 時，架構師承諾要把外洩面壓到最小的那一組）**：

| # | 規定 | 為什麼 |
|---|---|---|
| E1 | **沒有規則就一個都不准連。** 沒有萬用字元、沒有預設條目、`*` 一律 `422` | 白名單的價值全部來自「預設拒絕」。有一個 `*` 的逃生門，等於沒有白名單 |
| E2 | **預設只允許 `GET`。** 要 `POST` 必須在**那一條**規則上明列。只支援 `GET` 與 `POST` | 讀資料與送通知有具名的用例，`PUT`/`DELETE` 沒有。多開一個 method 就多一種「把東西寫到外面去」的形狀 |
| E3 | **沙盒不得自帶認證。** request `headers` 只准 `Accept` 與 `Content-Type`，其餘（特別是 `Authorization`、`Cookie`、`X-*`）**回 `422` 並指名是哪個 header**，不是默默丟掉 | 默默丟掉會讓「我的 key 怎麼沒生效」變成查半天的謎。要帶認證，由規則上的 `auth_secret_name` + `auth_header` 指名，平台注入 —— 這樣 secret 從頭到尾沒進過沙盒 |
| E4 | **只准 `https`，不跟隨 redirect** | 跟隨 redirect 等於讓對方決定下一跳，白名單一行 `302` 就繞過去了。遇到 3xx 直接回 `EGRESS_BLOCKED`，把 `Location` 放進 `details` 讓人看得懂 |
| E5 | **不准 IP 字面值；解析結果落在 private／loopback／link-local 一律擋** | `169.254.169.254`、`127.0.0.1`、`10.0.0.0/8` 是 SSRF 的標準目標。沙盒網段本身到不了，但 proxy 是從 `app` 發出去的，**`app` 同時掛在 `zentra-net` 上** —— 這條檢查是 proxy 版本的 SPEC §5 硬規則 4 |
| E6 | **大小與次數上限**：request body ≤ 64 KiB、response ≤ 1 MiB、單次逾時 ≤ 15s、每筆 Run ≤ `EGRESS_MAX_CALLS_PER_RUN`（預設 100） | 超量不截斷，直接當錯誤 —— 截斷過的 JSON 解析失敗時，錯誤訊息會指向 agent 的解析程式碼而不是真正的原因。次數上限是為了 Binance：打太兇會封 IP，而被封的是**整台主機** |
| E7 | **每一次出口呼叫寫一列稽核**（`egress_calls`，比照 `llm_calls`），含 `run_id` / `rule_id` / method / host / path / 狀態碼 / 進出位元組 / 延遲 / 錯誤 | 沒有稽核的出口通道，等於平台不知道自己送了什麼出去。對照判斷準則「可觀測 > 聰明」：這條通道笨一點沒關係，但每一次都要看得到 |

### 4. 能力二：寄信（使用者裁決 3）

`POST /internal/notify/email`，run token 認證。SMTP 不是 HTTP，**不走 §3 的 proxy** ——
硬要走的話得先做一個 SMTP-over-HTTP 的形狀，那是憑空發明一個協定。

per-Agent 一份 `agent_email_sender`：`smtp_host` / `smtp_port` / `username` /
`from_address` / **`password_secret_name`**（指向 L-機密的一個名字，密碼**不存在這張表**）/
`allowed_recipients`。

**硬性規定**：

| # | 規定 | 為什麼 |
|---|---|---|
| M1 | `allowed_recipients` **必填且非空**，精確地址，不接受網域萬用字元 | 少了它，一支迴圈寫錯的 agent 會用使用者的 Gmail 帳號變成垃圾信來源，而 Google 封的是**那個帳號**，不是這支 agent |
| M2 | 密碼只能以 `password_secret_name` 指名，**這張表沒有密碼欄位** | 只留一條存機密的路。開第二條就會有第二種加密方式、第二種洩漏方式、第二份要寫的文件 |
| M3 | 每筆 Run ≤ `EMAIL_MAX_PER_RUN`（預設 5）、每個 Agent 每小時 ≤ `EMAIL_MAX_PER_HOUR`（預設 60） | 訊號型 agent 最典型的 bug 就是重試迴圈裡寄信 |
| M4 | 稽核與 §3 **同一張 `egress_calls`**，用 `kind` 欄位分（`HTTP` / `EMAIL`） | 「這個 Agent 往外送了什麼」是一個問題，不該有兩個要分別查的地方 |
| M5 | SMTP 認證失敗是 `EGRESS_BLOCKED`（不可重試），連線／逾時是 `EGRESS_FAILED`（可重試） | 密碼錯了重試一百次還是錯，而且 Gmail 會因此鎖帳號 |

### 5. 錯誤碼：沿用 §0 枚舉，只擴充語意，不新增第三個

- **`EGRESS_BLOCKED`**：語意從「沙盒嘗試連 Internal Gateway 以外的位址」**擴充**為
  「沙盒的出口請求被平台政策擋下」，涵蓋 HTTP proxy 與寄信。HTTP 狀態 `403`。
  具體原因放 `details.reason`（枚舉見 api-contract §4.3）。
- **`EGRESS_FAILED`**（新增，`502`，retryable）：政策放行了，但上游打不通／逾時。

為什麼不是每個原因一個 code：錯誤 code 是公開契約的一部分，**發布後要長期相容**；
`details.reason` 不是。把會成長的那一維放在 `details` 裡，是為了之後加原因時不用改枚舉。

## 6. ⚠️ 與 P4 的關係（這份 ADR 最重要的一段）

P4 的字面是「沙盒內拿不到任何長期憑證；沙盒網段無對外路由，唯一對端是 Internal Gateway」。
**這份 ADR 沒有違反字面的任何一個字**：

| P4 的條款 | 這份 ADR 之後 |
|---|---|
| 沙盒拿不到長期憑證 | 成立。secret 只有名字進沙盒，值由 Gateway 在代打時注入 |
| 沙盒網段無對外路由 | 成立。`zentra-sandbox-net` 仍是 `internal: true`，一個字都沒改 |
| 唯一對端是 Internal Gateway | 成立。多的是 Gateway 上的兩條路由，不是第二個對端 |
| 沙盒 env 只有精確那幾個變數 | 成立。`_validate_environment()` 沒有放寬 |

**但 P4 的目的是「即使沙盒被攻陷，損失也要可控」，而這份 ADR 讓損失變大了。誠實寫出來：**

> **通用 proxy 是一條真正的外洩通道。白名單擋得住「送到哪」，擋不住「送了什麼」。**

具體地說，在這份 ADR 之後，一個被攻陷（或只是寫壞）的沙盒可以：

1. **把資料送到白名單上的任何一台主機。** `GET` 也能外洩 —— query string 就是資料。
   E2 的 GET-only 與 E6 的大小上限**只是壓低頻寬，不是消除通道**。
   `https://api.binance.com/api/v3/ping?x=<偷到的東西>` 是合法請求。
2. **花掉那個 secret。** 它讀不到 SMTP 密碼，但它可以請平台用那個密碼寄一百封信
   （M3 的上限會擋在 5 封 / 小時 60 封，但不是 0）。
   **「拿不到憑證」不等於「用不了憑證」** —— 這是所有代打型設計的共同性質，
   LLM proxy（ADR-0011）早就有同一個性質，這次只是多了兩個代打對象。
3. **把上游的錯誤當成訊號通道。** 例如用不同的 host 表示不同的 bit。稽核看得到，
   但沒有人會去看那個模式。

**為什麼仍然接受**：交付條件是「內部工具 · 單租戶 · **只跑自己信得過的程式碼**」
（[ADR-0005](ADR-0005-mvp-single-host-scope.md) §1.1）。在那個前提下，沙盒隔離防的是
**寫壞的程式碼**，不是**惡意的程式碼** —— 而白名單、GET-only、稽核這一組，對「寫壞」
是有效的：打錯網址會被擋、無窮迴圈會撞到次數上限、送錯東西查得到。

**要是那條交付條件變了**（有人要在上面跑不是自己寫的 agent），這一段的定價立刻失效，
見 §何時該重新檢討 的第一條。

## 7. 與 P5 的關係

**設定、機密、能力都是可變的，而且刻意對所有版本生效。**
這與 P5 的張力**已經由 [ADR-0011 §⚠️ 與 P5 的互動](ADR-0011-per-agent-llm-credentials.md)
論證完畢，這裡不重新論證，只說明它同樣適用**：判準是「誰的責任」——
image 是開發者的交付物，改了就該是新版本；設定與憑證是維運設定，
跟資料庫密碼、TLS 憑證同一類。

代價也相同，而且比 ADR-0011 更大一點：

- **舊 Run 無法單憑 `version_id` 完整重現。** 現在不只「當時用哪把金鑰」不知道，
  「當時的 RSI 門檻是多少」也不知道。緩解是 `egress_calls` 與 `llm_calls` 留下了
  **實際發生的呼叫**，但沒有留下**當時的設定值**。要完整可重現就得保留設定版本史，
  那是還沒付的成本（同 ADR-0011，延後而非否決）。
- **改設定沒有灰度、沒有回滾。** 一次 `PUT` 之後所有版本立刻改變。

新增一個 ADR-0011 沒有的坑，要特別講：

> **不要在 `build()` 裡對 config 做硬性要求。**

版本一旦 `PUBLISHED`，設定還會繼續變 —— 今天驗證通過的版本，明天有人刪掉一個 config key，
**每一筆 Run 都會在 build 階段死掉，而版本狀態仍然是 `PUBLISHED`**。
契約層面的規則：在節點裡讀 config，或用 `.get()` 帶預設值。
平台不強制（強制不了），但這句話要寫在 SDK docstring 與 SPEC §8.1。

> **⚠️ 這一段原本還寫了「validation 沙盒也會拿到當下的 `config` 與 `secret_names`」，
> 已由 [修訂 R1](#修訂-r12026-09-30) 反轉為「validation 一律用空的」。** 原文站不住腳，
> 而且反轉之後這條規則反而變得可強制 —— 詳見 R1。

## 選項（以及為什麼否決）

### A. 設定注入沙盒的 OS 環境變數（否決）

看起來最自然 —— 使用者要的就是「環境變數」。

- **否決理由一：常駐容器做不到。** 一個容器服務多筆 Run，env 是容器層級的共享狀態。
  改設定要重啟容器才生效，而併發時三筆 Run 沒有一個共同正確的值。
- **否決理由二：要打穿 `_validate_environment()` 的精確集合檢查。** 那條檢查是 P4 的
  第一道關，它的價值來自「恰好這幾個，多一個就失敗」。一旦改成「這幾個 + 使用者的任意鍵」，
  「secret 意外進了沙盒」就不再會在建立容器時被擋下來。
- 這不是延後，是**永久否決**。設定的正確位置是 `ctx.config`。

### B. 窄的專用出口端點（架構師原本推薦，使用者否決）

`/internal/market/klines`，只吃 `symbol` / `interval` / `limit`，回正規化後的 K 棒。

- 好處是外洩面幾乎為零：能送出去的只有三個受控參數，形狀由平台決定。
- **使用者選了通用 proxy，理由是彈性，這是使用者的決定。**
  記下它的代價供日後對照：窄端點的成本是「每接一種資料源就要改平台一次」，
  而使用者要的正是不必每次都改平台。
- **要回頭選 B 的訊號**：見 §何時該重新檢討。B 與通用 proxy **可以並存** ——
  高頻或高風險的來源收窄成專用端點，其餘走 proxy。不是二選一。

### C. 機密也送進沙盒，由 agent 自己拿去用（否決）

「反正沙盒跑的是我自己的程式碼。」

- **否決理由：直接違反 P4，而且買不到東西。** 需要機密的兩個用例（帶認證打 API、寄信）
  都已經由平台代打解決了。把值送進去，換到的只有「agent 可以自己組 SMTP 連線」，
  而那還要求沙盒能連 SMTP 主機 —— 網段根本不通。
- 附帶效果是稽核會破一個洞：agent 自己發的請求，平台記不到。

### D. 機密與設定合成一張表，用一個 `is_secret` 旗標區分（否決）

- 好處：一套 CRUD、一張 Console 卡片。
- **否決理由：讀取路徑會混。** 設定是「任何人打 `GET` 都看得到」，機密是「沒有任何端點
  讀得出來」。同一張表意味著同一個 serializer 要靠一個布林值決定回不回值 ——
  ADR-0011 §1 已經說過為什麼不把密文放在 `SELECT *` 會經過的地方：
  **要讓「只有明確去查它的程式碼才碰得到」成為結構事實，而不是紀律。**

### E. 設定版本史 / 機密版本史（延後，不是否決）

同 ADR-0011：它只買到稽核級可重現性，在單租戶內部工具下還沒有人需要那個粒度。

### F. 平台做 K 棒快取或交易所 SDK（否決，使用者裁決 4）

- **否決理由：範圍。** 八週 MVP 的敵人是範圍（ADR-0005）。K 棒快取要處理失效、
  對齊、回補，那是一個獨立的產品。agent 自己打 Binance、失敗自己換 OKX，
  平台只負責白名單放行。

## 後果

**正面**：

- `ctx.config` 從空殼變成真的能用，SPEC §8.1 列的四個 ctx 能力不再有一個是假的。
- 機密有了一條**唯一**的存放路徑，而且沿用已經寫好也已經驗過的 Fernet 原語。
- 外部呼叫與寄信全部進 `egress_calls`，**只靠 `run_id` 就能還原「這次 Run 往外送了什麼」**
  —— 這是判斷準則第 4 題的答案。

**負面**（前面已經展開，這裡收在一起）：

- P4 的目的被削弱：白名單擋得住「送到哪」，擋不住「送了什麼」（§6）。
- 可重現性再退一步：舊 Run 現在連「當時的設定值」都查不到（§7）。
- 多了三張表、兩條 Gateway 路由、四組公開 CRUD、三張 Console 卡片。這是實打實的維護面積。
- **`egress_calls` 會長得很快。** 一支每分鐘抓 K 棒的 agent 一天就是上千列，
  而 MVP 沒有保留期政策。這件事記在 BOARD 待辦，不在 ZEN-019 範圍內。

## 修訂 R1（2026-09-30）：validation 沙盒一律用空的 config

**改的是 §7 的一句話，以及 api-contract §4.1 第 4 點（→ v0.12.1）。**

**原文**：validation 沙盒也會拿到**當下的** `config` 與 `secret_names`，
「否則在 `build()` 裡讀 config 的 agent 一律驗證失敗」。

**問題**（@reviewer 在寫 ZEN-019 驗收條件時發現）：**沒有路可以走到那裡。**
`validate` mode 的 `ZentraContext` 是在容器內直接建構的，沒有取 bootstrap 的動作；
而 env 是精確集合（本 ADR §1）不能加變數。要讓它拿到當下設定，只有兩條路：
發明一條契約外的 internal 路由（＝契約漂移），或漏掉不做。原文等於要求實作者二選一。

**修訂後**：**validation 一律 `config = {}`、`secret_names = []`，不新增任何路由。**

**為什麼這比原文好，而不只是比較省事**：原文自己在同一段寫了
「不要在 `build()` 裡對 config 做硬性要求」，但那條規則當時**無法強制、也無法驗證** ——
帶著一份剛好齊全的設定去驗證，正好會讓違反這條規則的 agent 通過。
改成空 config 之後，**驗證本身就是那條規則的測試**：
一個空 config 就 build 不起來的 agent 會在發布時就失敗，而不是等使用者哪天清掉設定，
才發現每一筆 Run 都死在 build 階段、版本卻還是 `PUBLISHED`。

換句話說，原文買的是「驗證時比較寬鬆」，修訂買的是「把一個只能靠自律的規則變成結構事實」。
後者正是本 ADR §2 拒絕選項 D 時用的同一個判準。

**代價，誠實列出**：

- **在 `build()` 裡讀 config 的既有寫法會在發布時就失敗**，而不是在 Run 階段。
  這是刻意的，但錯誤訊息必須看得出是 config 相關 —— 那是使用者唯一的線索，
  而且它只在驗證沙盒裡看得到（同 SPEC §8.1 對 `build()` arity 的處理）。
- **驗證涵蓋不到「這份設定能不能讓 agent build 起來」。** 設定寫壞了要到 Run 才發現。
  這本來就是可變設定的性質（設定在版本發布之後還會變），驗證擋不住，
  假裝擋得住才是問題。

**沒有改的**：Run 與常駐容器兩條路徑照舊帶**當下的** `config` 與 `secret_names`（§1）。
R1 只動 validation。

## 何時該重新檢討

- **交付條件的「只跑自己信得過的程式碼」變了** —— 這是最重要的一條。
  §6 的定價完全建立在這個前提上。一旦要跑別人的 agent，通用 proxy 必須退回選項 B
  的窄端點，或加上出口內容檢查（那比窄端點貴得多）。
- **有人要求萬用字元主機、要求跟隨 redirect、要求非 `https`、要求 `PUT`/`DELETE`，
  或要求沙盒自帶 header** —— 這四個要求各自都會把 §6 的損失上限打開一大截。
  同意之前先回來改這份 ADR，不要在 code review 裡順手放行。
- **某個來源變成高頻或高風險**（例如開始下單，而不只是讀行情）：那一條收窄成專用端點（選項 B）。
  下單尤其該收窄 —— 讀錯資料是算錯，下錯單是賠錢。
- **出現第二個租戶**：`ZENTRA_SECRET_KEY` 是全平台一把（同 ADR-0011），
  而且白名單會需要平台層的上限，不能只由 Agent 擁有者自訂。
- **需要稽核級可重現性**：做設定與機密的版本史（選項 E）。
- **寄信量變大或要寄給外部客戶**：Gmail + 應用程式密碼是給「寄給自己」用的。
  真的要對外寄信要換成有退信處理與寄件信譽管理的服務，那時 M1 的白名單也要重新定義。
- **`egress_calls` 開始吃磁碟**：做保留期與彙總。
