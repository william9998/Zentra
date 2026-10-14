# crypto-quant-agent

抓 K 棒 → 算指標與諧波型態 → 訊號成立時寄信。

它同時是 **ZEN-019 的驗收載體**：示範 per-agent 的**設定**、**機密**與
**對外能力**（HTTP 出口 + 寄信）三層怎麼一起用。

```
preflight ──▶ fetch ──▶ analyse ──▶ decide ──┬──▶ notify ──▶ END
                                             └──▶ END（沒觸發就不寄信）
```

| 檔案 | 做什麼 |
|---|---|
| `agent.py` | LangGraph 圖。唯一會 import SDK 的檔案 |
| `signals.py` | `prepare()` / `analyse(c, cfg)` / 因子評分 / `decide()` / `DEFAULTS` |
| `indicators.py` | SMA / EMA / RSI / MACD / ATR / ADX / 布林 / 擠壓 / OBV / VWAP / 量能 z-score |
| `harmonics.py` | ZigZag 樞紐點 + 七種 XABCD 諧波型態 |
| `marketdata.py` | Binance / OKX 轉接器、格式正規化、**備援**、健全性檢查 |

後四支是純運算，**不 import SDK**，也不自己發 HTTP 請求 ——
`marketdata` 要的是一個注入進去的 `fetch(url) -> JSON`，`agent.py` 用 `ctx.http` 餵它。

---

## ⚠️ 開始之前：沒有白名單，它一根 K 棒都抓不到

平台的出口是**預設拒絕**的：沙盒網段沒有對外路由，能連到哪由每個 Agent
自己的白名單決定。**一條規則都沒有的話，第一次 `ctx.http.get()` 就會回
`EGRESS_BLOCKED / NO_RULES`。**

**這是正常行為，不是故障。** 第一次用的人幾乎一定會撞到這個，撞到時去
Console 的「對外出口」加規則就好。

需要**兩條**（主源 + 備援）：

| host | methods | path_prefix |
|---|---|---|
| `api.binance.com` | `GET` | `/api/v3/` |
| `www.okx.com` | `GET` | `/api/v5/` |

> OKX 的主機是 `www.okx.com`（不是 `api.okx.com`），交易對格式是 `BTC-USDT`
> （不是 `BTCUSDT`），`limit` 上限 300。這些 `marketdata.py` 都處理好了，
> 你只要把 host 填對。

---

## 部署（四步）

### 1. Build 並 push image

```bash
docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .   # 只需一次
cd examples/crypto-quant-agent
docker build -t localhost:6000/crypto-quant-agent:1.0.0 .
docker push localhost:6000/crypto-quant-agent:1.0.0
```

平台**不接收 image 檔案上傳**（ADR-0009）：image 留在 Registry，
Console 只送 digest 去註冊。

### 2. 建 Agent、註冊版本

Console → Agents → 建立 Agent → 註冊版本，填 semver `1.0.0` 與
canonical image ref（含 digest）。等狀態變 `PUBLISHED`。

> 發布會跑一次 `--mode=validate`。那時 `ctx.config` 是 `{}`、
> `ctx.secret_names` 是 `[]`（契約 §4.1 第 4 點）——
> 所以 `build()` 裡讀設定一律 `.get()` 帶預設值，機密自檢放在節點裡。
> 這支 agent 就是這樣寫的，可以照抄。

### 3. 設定三層

**設定**（Agent 詳情 → 設定）—— 整份取代，沒有 PATCH：

```json
{
  "symbols": ["BTCUSDT", "ETHUSDT"],
  "interval": "1h",
  "limit": 300,
  "sources": ["binance", "okx"],
  "notify_to": ["me@example.com"],
  "rsi_oversold": 30,
  "rsi_overbought": 70,
  "signal_threshold": 0.35,
  "harmonic_enabled": true
}
```

| 鍵 | 預設 | 意思 |
|---|---|---|
| `symbols` | `["BTCUSDT","ETHUSDT"]` | 追哪些交易對 |
| `interval` | `"1h"` | K 棒週期 |
| `limit` | `300` | 抓幾根（OKX 上限 300） |
| `sources` | `["binance","okx"]` | 主源與備援的順序 |
| `notify_to` | 無 | 寄給誰。一個地址或一串地址都收（`"a@b.com"` 與 `["a@b.com"]` 等價）。**每一個都必須也在寄件者的收件人白名單裡** —— 白名單是全有全無，有一個不在清單上，整封信都不寄 |
| `notify_on_flat` | `false` | 方向明確但未觸發時也寄信 |
| 其餘 | 見 `signals.DEFAULTS` | RSI / MACD / ATR / ADX / 布林 / 諧波 / 權重 / 門檻… |

> **打錯的鍵會被回報，不會被安靜吃掉。** `signals.merge_config()` 把不認得的
> 鍵收集起來，`preflight` 節點會 emit 出來並寫進 output 的 `warnings` ——
> 把 `rsi_period` 打成 `rsi_perio` 而沒有任何提示的話，你會以為設定生效了。
>
> **設定不是機密。** 它明文顯示在 Console，也完整進沙盒。API key 放機密。

**機密**（Agent 詳情 → 機密）：存一個 `SMTP_PASSWORD`。
Gmail 要用**應用程式密碼**，不是登入密碼。
**存進去就讀不回來**，agent 拿得到的只有名字。

**對外出口與寄信**：加上面那兩條白名單規則，再設寄件者：

| 欄位 | 值 |
|---|---|
| SMTP 主機 | `smtp.gmail.com` |
| Port | `587`（STARTTLS） |
| 帳號 / 寄件地址 | 你的 Gmail |
| 密碼 | 下拉選 `SMTP_PASSWORD`（**這張表沒有密碼欄位**） |
| 收件人白名單 | `me@example.com`（1–10 個精確地址） |

### 4. 觸發

```bash
curl -s -X POST http://localhost:8000/v1/agents/<agent_id>/runs \
  -H 'content-type: application/json' \
  -d '{"entrypoint":"default","input":{}}'
```

`input` 可以覆寫這一次的 `symbols` / `interval`，或帶 `{"dry_run": true}`。

---

## 排程：**平台不負責**

**這支 agent 由外部定時打 REST 觸發。** 平台刻意不做排程器
（SPEC §11 把「排程觸發（Beat）」列在 MVP 之外）。

cron 每小時第 1 分跑一次：

```cron
1 * * * * curl -s -X POST http://localhost:8000/v1/agents/<agent_id>/runs \
  -H 'content-type: application/json' -d '{"entrypoint":"default","input":{}}' >/dev/null
```

`systemd` timer、Jenkins、GitHub Actions、手動按按鈕都可以 —— 平台這一側完全一樣。

---

## 出了事看哪裡

| 症狀 | 看哪裡 |
|---|---|
| 抓不到 K 棒、`EGRESS_BLOCKED` | Console → **出口稽核**。被擋下的每一次都有一列，`reason` 會說是哪一條規則沒開 |
| 主源掛了 | 同上，Binance 那列的 `status_code` 不是 200，而 OKX 那列是。`warnings` 也會有一句「binance 失敗，改用 okx」 |
| 信沒寄出去 | 出口稽核裡 `kind=EMAIL` 的列。`SMTP_AUTH` = 密碼錯（Gmail 要應用程式密碼）；`RECIPIENT_NOT_ALLOWED` = 收件人不在白名單，**整封信都沒寄** |
| 設定好像沒生效 | Run 的 output 有 `warnings`；`preflight` 事件裡有這一次實際用的 `symbols` / `interval` / 不認得的鍵 |

---

## 這支 agent 刻意不做的事

- **不自己重試換源。** 備援在 `marketdata.fetch_klines()` 裡，agent 只注入 `fetch`。
  兩層重試會互相打架，而且看不出到底打了幾次。
- **不用形成中的 K 棒。** `signals.prepare()` 會砍掉未收盤的那一根。
  用它算訊號會在盤中閃爍 —— 那是「回測漂亮實盤失效」的頭號原因。
- **不自建 HTTP client、不裝交易所 SDK。** 沙盒連不出去，那些一定失敗。
- **不記狀態、不做去重。** 寄信不冪等，同一個訊號每小時觸發就會再寄一封。
  要去重請自己在 `ctx.config` 裡記，或把觸發頻率調低。

訊號是研究輸出，**不是投資建議**。
