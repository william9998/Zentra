# Feature Spec：ZEN-019 每個 Agent 自己的設定、機密與對外能力

| 欄位 | 值 |
|---|---|
| 狀態 | `SPEC_READY` |
| 擁有者 | @architect |
| 對齊 Spec | `specs/SPEC.md v0.5.0` |
| 涉及分層 | L0（公開 CRUD + Internal Gateway 兩條新路由）· L1（派工時把設定放進工作項）· L3（三張新表 + 一張稽核表）。**L2 沙盒不改隔離設定** |
| 依據 | [ADR-0014](../../adr/ADR-0014-per-agent-config-secrets-and-egress.md)（**含修訂 R1**） · api-contract **v0.12.1** §2.6–§2.10 / §4.1–§4.4 · ui-contract v0.9.0 §5.16–§5.19 |

## 1. 要解決什麼問題

使用者要在 `examples/` 放一支加密貨幣量化指標 agent：抓 K 棒 → 算指標與諧波型態 →
觸發訊號時寄 email。它需要三件平台現在做不到的事：

1. **改參數不用重發版本**（追蹤哪些交易對、RSI 門檻）。`ctx.config` 有 property 但永遠是 `{}`。
2. **存 SMTP 密碼**。平台只有 LLM 憑證那一張專用表。
3. **連得到 Binance、寄得出信**。沙盒網段沒有對外路由。

## 2. 範圍

### 做

- **L-設定**：`agent_settings` 表 + `GET`/`PUT /v1/agents/{id}/config`（api-contract §2.6）
- **L-機密**：`agent_secrets` 表（Fernet）+ `GET /secrets`、`PUT`/`DELETE /secrets/{name}`（§2.7）
- **L-能力**：`agent_egress_rules` + `agent_email_sender` 兩張表與各自的 CRUD（§2.8、§2.9）
- **稽核**：`egress_calls` 表 + `GET /v1/agents/{id}/egress-calls`（§2.10）
- **Internal Gateway**：`POST /internal/egress/request`（§4.2）、`POST /internal/notify/email`（§4.4）
- **派工兩條路都帶設定**：bootstrap 與 Redis 工作項都新增 `config` 與 `secret_names`（§4.1）
- **SDK**：`ctx.config` 改讀新欄位（**不再讀 `manifest["config"]`**）、
  新增 `ctx.secret_names`、`ctx.http`、`ctx.send_email()`
- **Console**：四個新元件（ui-contract §5.16–§5.19），都掛在 Agent 詳情頁
- **範例**：`examples/crypto-quant-agent/` —— 抓 K 棒 → 指標與諧波型態 → 寄信。
  它是這個 feature 的驗收載體，不是附錄。
  **這個目錄已經有東西**（尚未進版控）：`indicators.py` / `harmonics.py` / `signals.py`，
  純運算、不依賴 SDK。**要接上去，不要重寫** —— 缺的是 `agent.py`、`Dockerfile`、`README.md`

### 明確不做

- **不放寬 `_validate_environment()` 的精確集合檢查。** 這次**不新增任何沙盒環境變數**
  （ADR-0014 選項 A 永久否決）。任何往那個方向的實作都是這個 feature 的 bug，不是捷徑。
- **不讓機密的值以任何形式到達沙盒。** 沒有 `ctx.secrets`，沒有「只給一次」的取值端點，
  沒有 debug 模式例外。
- **不做排程器 / cron。** 這支 agent 由外部定時打 REST 觸發。平台不負責排程
  （SPEC §11 的「排程觸發（Beat）」仍在 MVP 外）。
- **不做交易所專用端點、不做 K 棒快取。** agent 自己打 Binance，失敗自己換 OKX
  （使用者裁決 4）。
- **不做出口內容檢查 / DLP。** 白名單擋「送到哪」，不擋「送了什麼」（ADR-0014 §6）。
- **不做設定或機密的版本史。** 舊 Run 查不到當時的設定值，這是已知代價（ADR-0014 §7）。
- **不做 `egress_calls` 的保留期與彙總。** 已記在 BOARD 待辦。
- **不改 Runs / SSE / MCP / 版本發布的任何形狀。**
- **不加新的錯誤 code**，除了契約已裁決的 `EGRESS_FAILED`。

## 3. 對核心立場的影響

| 立場 | 有無觸碰 | 說明 |
|---|---|---|
| P1 使用者程式碼不在平台行程執行 | ☐ 無 | 新路由都在 `app`，不載入使用者模組 |
| P2 框架統一 LangChain/LangGraph | ☐ 無 | |
| P3 API + MCP 雙門面 | ☐ 無 | 新端點都是管理面，不是 entrypoint，不進 OpenAPI-for-agent 也不進 MCP tool schema |
| P4 沙盒無長期憑證 | ☑ **要讀 ADR-0014 §6** | **字面一條沒鬆**：網段仍 `internal: true`、env 仍是精確集合、機密仍不進沙盒。**但目的被削弱**：通用 proxy 是真正的外洩通道。使用者已裁決，緩解措施 E1–E7／M1–M5 是硬性規定 |
| P5 版本不可變 | ☑ 同 ADR-0011 | 三層都綁 Agent 不綁版本，刻意對所有版本生效。判準「誰的責任」沿用 ADR-0011，不重新論證 |

## 4. 契約變更

- [x] `specs/api-contract.md` → **v0.12.1**（v0.12.0 新增三層，v0.12.1 收斂
      @reviewer 挖出的九處矛盾 U1–U9，逐條見該檔 §5 變更紀錄。
      **其中 U2 是行為反轉，寫程式碼前務必看過**）。新增 §2.6 設定、§2.7 機密、§2.8 出口白名單、
      §2.9 寄件者、§2.10 稽核查詢、§4.1 設定怎麼進沙盒、§4.2 出口 proxy、
      §4.3 `details.reason` 枚舉、§4.4 寄信。§0 的 `EGRESS_BLOCKED` 語意擴充 + HTTP `403`，
      新增 `EGRESS_FAILED`（502, retryable）。§2 端點表新增 14 列。
- [x] `specs/ui-contract.md` → **v0.9.0**。新增 §5.16–§5.19 四個元件，§6.1 檔案清單同步。**無新 token。**
- [x] `specs/SPEC.md` → **v0.5.0**。新增 §7.4；§5 新增分層硬規則 5；§8.1 ctx 能力表改寫；
      §10 新增三列 NFR 並修正「沙盒沒有其他出口」這個已不成立的前提；§11 兩張清單更新。

## 5. 設計要點

### 5.1 資料模型

```
agent_settings
  agent_id    UUID PK  → agents.id ON DELETE CASCADE
  config      JSONB NOT NULL DEFAULT '{}'
  created_at / updated_at

agent_secrets
  agent_id    UUID     → agents.id ON DELETE CASCADE
  name        varchar(64)
  ciphertext  text NOT NULL          -- Fernet token
  last_four   varchar(8) NOT NULL
  created_at / updated_at
  PRIMARY KEY (agent_id, name)

agent_egress_rules
  rule_id           UUID PK
  agent_id          UUID → agents.id ON DELETE CASCADE
  host              varchar(253) NOT NULL
  methods           text[] NOT NULL DEFAULT '{GET}'
  path_prefix       text NULL
  auth_secret_name  varchar(64) NULL
  auth_header       varchar(64) NULL
  description       varchar(200) NULL
  created_at / updated_at
  UNIQUE (agent_id, host)

agent_email_sender
  agent_id             UUID PK → agents.id ON DELETE CASCADE
  smtp_host            varchar(253) NOT NULL
  smtp_port            int NOT NULL
  username             text NOT NULL
  from_address         text NOT NULL
  password_secret_name varchar(64) NOT NULL
  allowed_recipients   text[] NOT NULL          -- CHECK: 長度 1–10
  created_at / updated_at

egress_calls
  call_id         UUID PK
  run_id          UUID → runs.id ON DELETE CASCADE
  agent_id        UUID → agents.id ON DELETE CASCADE
  kind            varchar(8) NOT NULL          -- HTTP | EMAIL
  rule_id         UUID NULL                    -- ON DELETE SET NULL
  method          varchar(8) NULL
  host            varchar(253) NOT NULL
  path            text NOT NULL                -- **不含 query string**
  status_code     int NULL
  request_bytes   int NOT NULL DEFAULT 0
  response_bytes  int NOT NULL DEFAULT 0
  latency_ms      int NULL
  error           text NULL
  created_at      timestamptz NOT NULL
  INDEX (agent_id, created_at DESC), INDEX (run_id)
```

三點要注意：

1. **`agent_egress_rules.rule_id` 在 `egress_calls` 裡是 `ON DELETE SET NULL`**，
   不是 CASCADE。刪一條白名單規則不該連帶抹掉它過去的稽核紀錄 ——
   那正是「我改了什麼、然後發生了什麼」最需要對照的時候。
2. **`agent_id` 的 FK 全部 `ON DELETE CASCADE`**，對齊
   [ADR-0013](../../adr/ADR-0013-hard-delete-agents-and-images.md) 的**硬刪**現況
   （不是 ADR-0011 寫的軟刪）。`DELETE /v1/agents/{id}` 的 §2.5 清單要補上這五張表。
3. **`auth_secret_name` / `password_secret_name` 是名字字串，不是 FK。**
   FK 到 `(agent_id, name)` 會讓「刪機密」在 DB 層直接爆 constraint error，
   而契約要的是一個能說出「被誰引用」的 `409`（§2.7 第 6 點）。引用檢查在服務層做。

### 5.2 設定怎麼走到沙盒（最容易做錯的一段）

```
                       ┌─ 一次性沙盒 ──▶ GET /internal/runs/{id}/bootstrap
agent_settings.config ─┤                    └─ 回應加 config / secret_names
                       └─ 常駐容器 ────▶ worker push 工作項（apps/worker/tasks/run.py）
                                            └─ 工作項 dict 加 config / secret_names
                                               → GET /internal/agents/{id}/work 原樣回傳
```

**兩條都要改。只改 bootstrap 那條，常駐容器（預設路徑）永遠拿不到設定，
而且畫面上看起來跟「設定沒生效」一模一樣** —— ZEN-012 的 `default_model` 踩過一次
（api-contract §4），ZEN-017 的 `ctx.llm()` 踩過第二次。這是第三次，請直接寫一個
涵蓋兩條路徑的測試。

**validation 沙盒是第三條路，但它不取設定**（v0.12.1 定案，U2）：
`config = {}`、`secret_names = []`，**不新增任何 internal 路由、不改 `apps/sandbox/**`**。
`validate` mode 的 `ZentraContext` 在容器內直接建構，沒有 bootstrap 這個動作，
而 env 是精確集合不能加變數 —— 硬要帶當下設定只能發明契約外的路由。

**空 config 驗證是刻意的，而且比帶設定更嚴格**：一個空 config 就 build 不起來的 agent
**應該**發布失敗，因為等使用者哪天清掉設定，它的每一筆 Run 都會死在 build 階段
而版本狀態仍是 `PUBLISHED`。細節見 [ADR-0014 修訂 R1](../../adr/ADR-0014-per-agent-config-secrets-and-egress.md)。

**SDK 的 `ctx.config` 現在讀 `manifest.get("config", {})`（`packages/sdk/zentra_sdk/context.py`），
要改成讀新的頂層欄位。** 不要「順手」讓平台把 config 塞進 manifest ——
manifest 是版本的不可變內容，那樣做會讓 P5 在 runtime 這一側失去實體。

### 5.3 出口 proxy 的檢查順序

順序有意義：**便宜且不需要網路的檢查先做，最貴的（DNS 解析）最後做。**

```
1. 有沒有 run token                → 401（**這一步失敗不寫稽核**，沒有可信的 run_id）
── 以下每一條 return 路徑都要寫 egress_calls（U3）──
2. body / timeout_seconds 格式     → 422（timeout > 15 也是 422，不夾值；U4）
3. headers 有沒有帶不該帶的         → 422（details.header 指名；U1，**不是 403**）
4. 這個 Agent 有沒有規則           → EGRESS_BLOCKED / NO_RULES
5. scheme 是不是 https             → SCHEME_NOT_ALLOWED
6. host 命中哪一條規則              → HOST_NOT_ALLOWED
7. method 在那條規則裡嗎            → METHOD_NOT_ALLOWED
8. path_prefix 對得上嗎             → PATH_NOT_ALLOWED
9. 這筆 Run 的次數上限              → RUN_CALL_LIMIT（**計的是嘗試，前面被擋的也算**；U5）
10. DNS 解析 → 位址是不是內網        → PRIVATE_ADDRESS
11. 送出（不跟隨 redirect、有逾時、有大小上限）
12. 寫一列 egress_calls（成功與失敗都寫，欄位組合見 api-contract §2.10 第 4 點）
```

**格式檢查（2、3）排在政策檢查（4 之後）前面是刻意的**：`422` 說的是「這個請求寫錯了」，
`403` 說的是「這個請求沒錯，但政策不准」。先判政策的話，一個同時寫錯又不被允許的請求
會回 `403`，使用者跑去改白名單，改完還是失敗。

**次數上限（9）排在 DNS（10）之前**：DNS 是最貴的一步，而超過上限的請求無論如何都不會送出。

第 9 步有一個實作陷阱要講清楚：**解析完再連，中間有 TOCTOU 的空窗**
（DNS rebinding）。MVP 的處理是「解析一次、**直接連那個 IP** 並帶上 `Host` header 與 SNI」，
不是「解析一次然後再讓 http client 自己解析第二次」。後者等於沒檢查。
做不到的話就在交付時明說做不到，**不要當成做到了** —— 這是判斷準則第 3 題。

### 5.3.1 怎麼在「不准連內網」的前提下測到成功路徑（U8 定案）

@reviewer 指出的矛盾是真的：proxy 只准 `https` 且解析到私有位址一律擋，
而 compose 網段上任何測試 stub 都在 `172.16/12` —— **一個正確的實作會把自己的測試 stub 擋掉**。
這不是要放寬契約，是要把測試切在對的縫上。

**必做：把可測性做進實作，而不是在實作上開後門。**

1. **私有位址判斷是一個純函式**，輸入是「已解析的 IP 清單」，輸出是擋或不擋。
   不碰網路，所以 `169.254.169.254`、`10.x`、`127.0.0.1`、IPv6 ULA 全部可以用表格測試窮舉。
2. **解析器與 transport 是兩個可替換的相依。**
   ⚠️ **不要照抄 `apps/api/services/llm.py`** —— 它在 `proxy()` 裡就地
   `async with httpx.AsyncClient(...)`，**沒有任何可替換的縫**，
   所以它的成功路徑今天也測不到。這裡刻意不一樣，不是不一致。
   具體做法：`httpx.AsyncClient(transport=...)`，正式環境用預設 transport，
   測試傳 `httpx.MockTransport`；resolver 則是一個可覆寫的 module 層函式。
   測試注入：假 resolver 回一個公網 IP、假 transport 記下「實際發出去的請求長什麼樣」。
   **成功路徑驗的是「平台組出來的請求對不對」** —— method、注入的 header、
   `User-Agent`、有沒有跟隨 redirect、大小上限在哪裡截斷 —— 這些全部不需要真的連出去。
3. 所以**成功路徑不依賴外網，也不需要任何旗標**。

**明確禁止**：`apps/**` 裡不得存在「允許連內網」「跳過解析檢查」之類的開關，
**連預設關閉的都不行**（api-contract §4.2 第 9 點）。理由是它打開時沒有任何症狀。
請加一條靜態護欄測試：`apps/**` 對這些名字**零命中**。

**選做（預設關閉）**：要真的打公網驗證交易所回得來，用 `ZENTRA_TEST_ALLOW_PUBLIC_EGRESS=1`
開啟那幾條測試，**預設 skip**。這個旗標的關鍵性質是
**它只被 `tests/**` 讀，`apps/**` 永遠不讀它** —— 產品程式碼讀不到的東西不可能是產品旁路。
同一條靜態護欄一起檢查這個名字在 `apps/**` 的命中數為零。
而且它**不改變平台行為**：打公網本來就是這個 proxy 的正常工作，
它只是決定那幾條測試跑不跑。

> **skipped 不等於通過**（ZEN-008 的 137 個 skip 是前車之鑑）。
> 交付時要說清楚哪幾條是 skip 的、以及沒驗到什麼。

### 5.4 機密解密的位置

**只有 Gateway 的 egress / email 兩條路徑會呼叫 `secrets.decrypt()`。**
沿用 ADR-0011 §3 的做法：服務層**不提供**回傳明文給 API 層的函式。
`describe()` 回 metadata，`resolve()` 只給 Gateway。這是靠「沒有那個函式」保證的，
不是靠紀律 —— 新寫的 `agent_secrets` 服務要維持同一個形狀。

### 5.5 範例 agent（`examples/crypto-quant-agent/`）

它同時是這個 feature 的**驗收載體**。

**現況**：目錄裡已經有**四支**模組、都**尚未進版控**：

| 檔案 | 內容 |
|---|---|
| `indicators.py` | SMA/EMA/RSI/MACD/ATR/ADX/布林／擠壓／OBV／VWAP… |
| `harmonics.py` | 樞紐點偵測與諧波型態比對 |
| `signals.py` | `prepare()` / `analyse(c, cfg)` / 因子評分 |
| `marketdata.py` | **Binance／OKX 轉接器、格式正規化、備援、健全性檢查** |

**要接上去，不要重寫。** 兩個現成的接點：

- `analyse(c, cfg)` 已經吃一個 `cfg: dict` —— **那就是 `ctx.config` 的接點**，
  不要另外發明一套設定格式。
- `marketdata.py` 已經做了主源／備援與正規化 —— **它需要的只是一個 HTTP 呼叫函式**，
  把 `ctx.http` 接進去即可。**不要在 agent 裡再寫一層備援邏輯。**

缺的是 `agent.py`（LangGraph 圖）、`Dockerfile`、`README.md`。

要示範的是全部三層：

- `ctx.config` 讀交易對清單、K 棒週期、指標門檻，原樣餵給 `signals.analyse(c, cfg)`
  （全部 `.get()` 帶預設值）
- `ctx.http` 接進 `marketdata.py`，由它負責「Binance 非 2xx 或例外時改打 OKX」（使用者裁決 4）
- `ctx.send_email()` 在訊號成立時寄信
- **一個節點**用 `ctx.secret_names` 做啟動自檢，缺 `SMTP_PASSWORD` 時直接說出來。
  **要在節點裡，不能在 `build()` 裡** —— validation 期間 `secret_names` 是空的（§5.2），
  寫在 `build()` 裡會讓這支範例自己發布失敗

白名單要設兩條（`api.binance.com`、`www.okx.com`），README 要把這件事寫出來：
**沒設白名單它一個字都抓不到**，而那是預設拒絕的正常行為，不是故障。

README 要寫清楚：**由外部定時打 REST 觸發**（`curl` + cron 或任何排程器），
平台不負責排程。

## 6. 分工

| 角色 | 工作 | 可寫範圍 |
|---|---|---|
| @builder（後端） | 五張表的 migration、四組公開 CRUD、兩條 Gateway 路由、派工三條路徑帶 `config`/`secret_names`、`DELETE /v1/agents/{id}` 的清單補上新表 | `apps/api/**`、`apps/worker/**`、`migrations/**` |
| @builder（SDK） | `ctx.config` 改讀頂層欄位、新增 `ctx.secret_names` / `ctx.http` / `ctx.send_email()`、`image_runtime` 兩條執行路徑一起改 | `packages/sdk/**` |
| @builder（前端） | ui-contract §5.16–§5.19 四個元件 + `api.js` 補對應函式 | `apps/console/**` |
| @builder（範例） | `examples/crypto-quant-agent/` 的 `agent.py` + `Dockerfile` + `README.md`（三支運算模組已存在，接上去就好） | `examples/**` |
| @reviewer | 驗收條件寫進 `acceptance.md`，**開發前寫** | `specs/features/ZEN-019/acceptance.md` |

**`apps/sandbox/**` 這次不需要改。** 如果實作過程中發現「非改不可」，那幾乎一定是
走上了「把設定塞進 env」那條被否決的路 —— 停下來回報，不要自己放寬。

## 7. 驗收條件

> 由 @reviewer 在**開發前**寫進 `acceptance.md`。開發者不能自己定義「做完了」。

架構師指定**必須包含**的六條（不是全部，是不得省略的）：

1. **兩條派工路徑都驗。** 一次性沙盒與常駐容器各跑一次，`ctx.config` 都拿得到值。
   改設定後**不重啟容器**，下一筆 Run 就是新值。
2. **機密拿不到值。** 掃過所有公開回應與沙盒可見的一切（bootstrap、工作項、
   `ctx` 的每個 property），確認明文與密文都不存在。
   `secret_names` 只有名字。
3. **沙盒 env 沒變。** 斷言 RUN mode 的 env 恰好是那三個、SERVE mode 恰好是那四個。
   **這條測試要留在 repo 裡當護欄**，不是驗一次就算。
4. **白名單是預設拒絕。** 沒有規則時任何 `ctx.http` 都被擋；
   加一條 `api.binance.com` 之後打 `api.okx.com` 仍被擋。
   redirect、`http://`、IP 字面值、`169.254.169.254`、自帶 `Authorization` 各一條。
5. **稽核完整。** 成功與被擋下的呼叫都寫了一列，只用 `run_id` 查得回來，
   `path` 不含 query string。
6. **範例 agent 端到端跑通**，包含 Binance 失敗時真的切到 OKX（用假的失敗注入，
   不要靠真的把 Binance 打壞）。

## 8. 風險與未決問題

| 風險 | 影響 | 對策 |
|---|---|---|
| **只改 bootstrap、忘了工作項** | 常駐容器（預設路徑）的設定永遠是空的，而且症狀看起來像「設定沒存進去」 | 驗收條件 1；ZEN-012 與 ZEN-017 都踩過同一個坑 |
| **實作時為了方便把設定塞進 env** | 直接打穿 P4 的第一道關 | 驗收條件 3 的護欄測試；§6 的「sandbox 不需要改」 |
| **DNS rebinding 繞過內網檢查** | 沙盒可經 `app` 觸達 postgres / redis / registry | §5.3 的「解析一次直接連 IP」；做不到要明說做不到 |
| **使用者把 API key 貼進 `config`** | 明文顯示在 Console、明文進沙盒，而且沒有任何警告 | ui-contract §5.16 第 1 點的文案是唯一防線。**平台不掃描**（猜不準的警告比沒有警告更糟） |
| **迴圈寄信打爆 Gmail 帳號** | Google 鎖的是使用者的帳號，不是這支 agent | M3 的每 Run / 每小時上限；M1 的收件人白名單 |
| **打太兇被交易所封 IP** | 被封的是整台主機，其他 agent 一起遭殃 | `EGRESS_MAX_CALLS_PER_RUN`（預設 100） |
| **`egress_calls` 長太快** | 磁碟 | 已知，記在 BOARD 待辦，不在本範圍 |

未決 → 直接問使用者（CLAUDE.md 開發鐵則 2），不要寫 `TODO: 待確認`。
