# ZEN-015：常駐 Agent 容器 + 每個 Agent 自己的下游 API key

> 依 [ADR-0012](../../adr/ADR-0012-resident-agent-container.md)。
> **對外形狀完全不變** —— 使用者照樣打 `/v1/agents/{id}/runs` 與 `/mcp/agents/{id}`，
> 差別只在請求被派到一個已經開著的容器，而不是現開一個。

## 1. 要解決什麼

單機上「一 Run 一容器」的記憶體是「並發數 × 2 GB」，而主機實測只有 1.9 GB。
LLM agent 幾乎全是 I/O 等待，閒置的容器佔著記憶體卻沒在算東西。

## 2. 範圍

### 做

- **Agent 生命週期**：`PUBLISHED` 的版本可以「啟動」，得到常駐容器；可停止、可重啟
- **每容器並發上限 `N`**（`SANDBOX_RUNS_PER_CONTAINER`，預設 **30**），滿了溢出開新容器
- **派工改為送進常駐容器**，不是每次 `containers.create`
- **容器健康檢查與重啟**（Console 按鈕 + API 端點）
- **每個 Agent 可發多把下游 API key**，呼叫 `/v1` 與 `/mcp` 時帶著
- **用量歸戶到 key**：每次 LLM 呼叫記得到是哪一把 key 觸發的
- **除錯查詢**：依 key、時間範圍、狀態、model 篩選請求與錯誤

### 明確不做

- **不做跨主機調度。** 仍是單機（ADR-0005）。
- **不做自動擴縮。** 容器數由「啟動了幾個 Agent」決定，不由流量決定。
- **不改對外契約的形狀。** `/v1/agents/{id}/runs` 的 request/response 一個欄位都不動。
- **不做多輪對話。** `session_id` 欄位先留，語意等有需求再定（見 §4.2）。
- **不做計費出帳。** 只做計量（沿用 SPEC §11 的界線）。
- **不改 P1–P5 的立場**，P4 的實作方式改為「token 跟著工作項走」（ADR-0012）。

## 3. 使用者要問的三件事，先回答

### 3.1 Agent 程式要特別寫非同步嗎？

**不用刻意改寫，但有一個具體的錯不能犯。** 實測（ADR-0012）：

| 寫法 | 3 個並行 | 結論 |
|---|---|---|
| `def` 節點裡阻塞 | 1.00s，3 個執行緒 | **安全** —— LangGraph 丟到執行緒池 |
| `async def` + `await` | 0.05s，1 個執行緒 | **最好** |
| **`async def` 裡阻塞** | **3.01s，完全序列化** | **災難** —— 卡住同容器所有 Run |

規則一句話：**`async def` 的節點裡不准有阻塞呼叫**
（`requests`、`time.sleep`、同步 DB driver）。
不確定的話寫成普通 `def`，平台會幫你丟執行緒池。

驗證沙盒要**靜態檢查**這件事並在 `validate` 階段警告（§5.4）。

### 3.2 每一筆請求用什麼當唯一 ID？

**`run_id`，不需要新的識別。** 它已經是 UUIDv7 —— 時間有序，
所以「查某個時間範圍的請求」可以直接用主鍵範圍，不必額外索引時間欄位。

共用容器的關鍵實作：**`contextvars` 綁定 `run_id`**。
容器內同時有多個 Run 在跑，日誌、事件、token 都必須認得自己屬於哪一個 ——
用全域變數或參數傳遞都會在 `await` 之後串到別人身上。

`session_id` 欄位先建但不啟用：多輪對話時它用來把多個 Run 串起來，
語意等真的要做時再定。**先留欄位是因為之後補 migration 比現在多一欄貴。**

### 3.3 per-key 的用量與除錯

資料模型見 §4。查詢形狀：

```sql
-- 某把 key 這個月花了多少、用了哪些模型
SELECT c.model, count(*), sum(c.input_tokens), sum(c.output_tokens), sum(c.cost_usd)
FROM llm_calls c JOIN runs r ON r.id = c.run_id
WHERE r.api_key_id = :key_id AND r.created_at >= :from
GROUP BY c.model;

-- 某把 key 哪幾筆出錯了
SELECT r.id, r.created_at, r.status, c.status_code, c.error
FROM runs r LEFT JOIN llm_calls c ON c.run_id = r.id
WHERE r.api_key_id = :key_id AND (r.status = 'FAILED' OR c.error IS NOT NULL)
  AND r.created_at BETWEEN :from AND :to;
```

可篩的欄位：`api_key_id`、`created_at` 範圍、`status`、`model`、`credential_source`、
`status_code`、`error IS NOT NULL`。

## 4. 資料模型

### 4.1 新表

```
agent_containers                       -- 一個 Agent 目前的常駐容器
  id                uuid PK
  agent_id          uuid FK → agents.id ON DELETE CASCADE
  agent_version_id  uuid FK → agent_versions.id    -- 容器綁定單一版本（P5）
  container_id      text                           -- Docker container id
  status            text  -- STARTING|READY|UNHEALTHY|STOPPED|FAILED
  started_at        timestamptz
  last_health_at    timestamptz
  restart_count     int default 0
  error             text
  inflight_runs     int default 0       -- 目前正在跑幾個 Run（上限 N）
  -- 一個 Agent 可以有多個容器（N 滿了溢出），所以不是 UNIQUE(agent_id)
  INDEX (agent_id, status)

agent_api_keys                         -- 下游應用拿來呼叫這個 Agent 的 key
  id            uuid PK
  agent_id      uuid FK → agents.id ON DELETE CASCADE
  name          text        -- 給人看的用途標記（「行銷站」「內部工具」）
  key_hash      text        -- **只存雜湊**。與 ZEN-012 不同：那個要還原後轉發，這個只驗
  key_prefix    text        -- 顯示用，例如 zk_live_a1b2…
  created_at    timestamptz
  last_used_at  timestamptz
  revoked_at    timestamptz -- 軟撤銷，保留歷史用量的歸屬
```

> **`agent_api_keys` 存雜湊，`agent_llm_credentials` 存密文 —— 這個差別是刻意的。**
> 前者只需要「驗證來的人是誰」，雜湊就夠且更安全（外洩也還原不出來）。
> 後者要把明文轉發給供應商，只能加密不能雜湊。**不要把兩者做成同一套。**

### 4.2 既有表的新欄位

```
runs
  + api_key_id   uuid NULL FK → agent_api_keys.id   -- 哪把 key 觸發的；Console 發起為 NULL
  + session_id   uuid NULL                          -- 多輪對話用，本期不啟用
  + container_id uuid NULL FK → agent_containers.id -- 跑在哪個容器，除錯用
```

`llm_calls` **不動** —— 它已經有 `run_id`，join 就拿得到 key 的歸屬。
在 `llm_calls` 再存一次 `api_key_id` 是冗餘，而且會有兩份可能不一致的真相。

## 4.3 派工方向：容器拉，平台不推

常駐容器**不開任何 port**。它 long-poll Internal Gateway 取工作：

```
容器 ──▶ GET /internal/agents/{agent_id}/work    （long-poll，等到有工作或逾時）
     ◀── {run_id, entrypoint, input, run_token, manifest}
容器 ──▶ POST /internal/runs/{run_id}/events     （既有路徑）
容器 ──▶ POST /internal/runs/{run_id}/result     （既有路徑）
```

**這樣做的理由是隔離，不是效能。** 讓平台推的話容器得監聽連線，
SPEC §8.1「容器內不開對外 HTTP port」與 L1 的「沙盒只有一個對外方向」就失效了 ——
而那是 P4 最容易驗證的一條性質。拉的做法讓網路拓撲**一個字都不用改**。

`run_token` **跟著工作項一起送**，不在容器啟動時給（ADR-0012 的 P4 實作）。
容器內用 `contextvars` 綁定，確保 Run A 的 token 不會洩漏到 Run B 的 context。

## 5. 設計要點

### 5.1 容器生命週期

```
發布版本 ──▶ PUBLISHED ──(啟動)──▶ STARTING ──▶ READY ──▶ (停止) ──▶ STOPPED
                                       │            │
                                       └──(失敗)────┴──▶ UNHEALTHY ──(重啟)──▶ STARTING
```

- **啟動是明確動作**，不是註冊的副作用 —— 使用者要能只註冊不啟動（省資源）
- 容器綁定**單一 `agent_version_id`**。發新版要換容器（P5），切換期間兩個並存
- 健康檢查：平台定期打容器的 `/healthz`；連續失敗 N 次標 `UNHEALTHY`
- **重啟保留 `restart_count`** —— 一直重啟的容器是壞掉了，不是需要再重啟一次

### 5.2 隔離的變化（誠實列出）

| 層 | 現在 | 之後 |
|---|---|---|
| L1 網路 | 不變 | 不變 |
| L2 核心 | 不變 | 不變 |
| L3 資源 | per-Run | **per-Agent** —— 一個失控的 Run 會吃掉同容器兄弟的額度 |
| L4 檔案 | per-Run 的 tmpfs | **per-Agent** —— `/workspace` 跨 Run 共用 |
| L5 應用 | 未實作 | 未實作 |

**這是這個功能真正的代價**，要寫進 SPEC §6.2 而不是藏在實作裡。

### 5.3 15 分鐘上限怎麼強制

現在靠銷毀容器。之後：

1. 容器內對每個 Run 用 `asyncio.wait_for` 包住（涵蓋 async 路徑）
2. 平台側仍記逾時並把 Run 標 `FAILED`
3. **對阻塞的 `async def` 無效** —— 那種情況只能重啟整個容器，
   並在 `restart_count` 留下痕跡。這條在文件裡要講明，不要假裝有解。

### 5.4 validate 階段的靜態檢查

驗證沙盒現在只檢查 `build()` 簽章與回傳型別。再加一條：
掃 `async def` 節點裡有沒有已知的阻塞呼叫（`requests.`、`time.sleep`、
`urllib.request`、同步 DB driver），有就在 `validate` 回**警告**（不擋發布）。

擋不住所有情況（動態呼叫抓不到），但擋得住最常見的那幾個 ——
而且在發布時講比在半夜卡住所有 Run 時講便宜得多。

## 6. 契約變更

- `api-contract.md` → **v0.6.0**：容器生命週期端點（§2.2）；**v0.7.0**：API key 管理端點（§2.3）；**v0.8.0**：用量與除錯查詢（§2.4）
- `ui-contract.md` → **v0.6.0**：§5.13 `api-keys-card.js`、§5.14 `usage-panel.js`
- **既有端點的 request/response 形狀零變更**（P3 的門面對使用者無感）
- 新增錯誤 code：`AGENT_NOT_RUNNING`（呼叫了沒啟動容器的 Agent）

## 7. 交付順序

1. ~~ADR-0012 裁決~~ ✅ 使用者裁決**選項 C，N 預設 30**
2. ~~migration + 資料模型~~ ✅
3. ~~容器生命週期（啟動 / 停止 / 重啟 / 健康檢查）+ Console 按鈕~~ ✅
4. ~~派工改走常駐容器~~ ✅ serve 模式 + long-poll 派工，單容器實測 10 併發
5. ~~per-agent API key + 用量歸戶~~ ✅ api-contract v0.7.0 §2.3，REST 與 MCP 同一套規則
6. ~~除錯查詢與畫面~~ ✅ api-contract v0.8.0 §2.4、ui-contract v0.6.0 §5.13–5.14
7. N > 1 的並發與溢出（ADR-0012 選項 C 的後半）← **下一步**

**4 與 5 可以平行**，但 3 必須先完成。

> **6 的畫面在 Agent 詳情頁**，兩張卡：發 / 撤銷 key，以及用量與逐筆除錯。
> key 卡片把清單推給用量面板，兩邊不各抓一次。
