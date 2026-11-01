# Agent 與版本註冊（含下游 API key）

> Agent 怎麼進平台、版本怎麼驗證和發布、同一個版號怎麼取代、怎麼刪除，以及發給下游應用的 key。
> 端點長什麼樣看 [api.md](api.md)。**要寫 Agent 的程式碼，看 [agent-development.md](agent-development.md)。**

## 先搞懂三個東西

```
Agent（只有名字和說明）
 └─ 版本：agent_id + semver（例如 1.2.0）→ 對應「一個」image digest
        狀態：BUILDING（驗證中）→ PUBLISHED（可用）或 FAILED
              PUBLISHED 被同版號新版本取代 → SUPERSEDED
```

- `POST /v1/agents` 只建「名字和說明」，**這時還沒有版本，不能呼叫**。`name` 全平台唯一。
- 平台**不接收 image 檔案**。image 放在 Registry，平台只存 digest，以及驗證時抽出的資訊。
- 註冊版本**不會**啟動容器；第一筆 Run 進來才會（看 [container-orchestration.md](container-orchestration.md)）。

## 使用者要交出什麼：image 契約

| 項目 | 要求 |
|---|---|
| label | `io.zentra.image-contract=zentra.image/v1` |
| 執行檔 | `/opt/zentra/bin/zentra-agent`，有 `validate` / `run` / `serve` 三種模式 |
| 執行身分 | non-root `65534:65534`。平台用唯讀 rootfs 啟動，只給 `/workspace` 一塊 tmpfs |
| port | 容器內**不開**任何 HTTP port（REST / MCP 是 `app` 的事） |
| 程式檔 | `agent.py`，放在 `/opt/zentra/app/agent.py` |

**不用自己滿足這些。** 官方 base image `zentra/agent-base:1.0`（`docker/agent-base.Dockerfile`，本機 build、不發佈）已經備好以上全部，加上 Python 3.12、`zentra_sdk`、LangGraph。
你的 Dockerfile 只要兩行：

```dockerfile
FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
```

完整範例在 `examples/my-agent/`。`agent.py` 長這樣：

```python
from zentra_sdk import ZentraContext, agent
from langgraph.graph import StateGraph

@agent(name="my-agent", version="1.0.0")
def build(ctx: ZentraContext) -> StateGraph:   # 只接受一個參數，回傳「還沒 compile」的 StateGraph
    ...
```

`ctx` 提供的東西：`ctx.llm()` · `ctx.logger` · `ctx.emit()` · `ctx.config` · `ctx.secret_names` · `ctx.http` · `ctx.send_email()`（行為看 [gateway.md](gateway.md)）。
**沒有** `ctx.secrets`（直接取機密的值，永久否決）、`ctx.tools`、`ctx.knowledge`。

兩個注意：
- **不要在 `build()` 裡強制要求某個 `config` 鍵或機密。** 驗證時 `config = {}`、`secret_names = []`，你的 Agent 會在發布時就失敗。用 `.get()` 給預設值，或等到節點執行時再檢查。
- SDK 自己的依賴由 `packages/sdk/pyproject.toml` 宣告，image 契約不逐一列出。

## image 放哪、怎麼註冊

| 放哪 | 怎麼做 | `image_ref` 長相 |
|---|---|---|
| 外部 Registry（GHCR、自建） | 自己 push，再用 `POST /v1/agents/{id}/versions` 以 digest 註冊 | `ghcr.io/acme/agent@sha256:…` |
| 平台內部 Registry | **在跑平台的那台主機上** `docker push localhost:6000/<agent 名>:<版號>`，**push 就是註冊**（見下一節） | `localhost:6000/<agent 名>@sha256:…` |

取得 digest：`docker inspect --format='{{index .RepoDigests 0}}' <image>`。

## push 即註冊（內部 Registry）

```
docker push localhost:6000/<agent 名>:<版號>   ==   註冊「<agent 名>」的「<版號>」
```

**範例：**

```bash
docker tag my-agent:build localhost:6000/stock-agent:1.0.1
docker push localhost:6000/stock-agent:1.0.1
# → 平台自動：找到（或建立）stock-agent，註冊版本 1.0.1，開始驗證
```

| 部分 | 規則 |
|---|---|
| repository（`stock-agent`） | **就是 Agent 名稱**，只能一層，要符合 Agent 名稱規則。Agent 不存在就**自動建立** |
| tag（`1.0.1`） | **就是版號**（規則同 `POST /versions` 的 semver。Docker tag 不能有 `+`，所以沒有 build metadata） |
| 不符合約定的 | 略過並記 warning log：`latest`、`dev`、多層 repo（`team/x`）、layer / config blob 事件 |

背後怎麼運作：

```
docker push ──▶ registry ──通知──▶ app 的 POST /internal/registry/events
                                     1. 挑出「manifest 的 push、有 tag、repo 是 Agent 名、tag 是版號」的事件
                                     2. 回頭問 registry：HEAD /v2/<repo>/manifests/<tag>，
                                        這個 tag 現在真的指向通知裡的那個 digest 嗎？不是就略過
                                     3. 同一個交易：找 Agent（沒有就建）→ create_version（和 POST /versions 同一個函式）
                                     4. 需要驗證 → 送 zentra.build.execute → 之後和一般註冊完全一樣
```

- push 之後的行為和 API 註冊**完全一樣**：驗證、發布、同版號 = 取代、成為最新版時舊容器被收掉。
- **通知端點沒有 token**（registry 帶不了我們的憑證）。但 `/internal/*` 容器是連得到的，所以**通知內容不被信任**：第 2 步會回頭向 registry 確認。偽造的通知最多只能讓平台註冊「registry 裡本來就存在」的東西，和真的 push 一樣。
- **永遠回 200**：回別的狀態，registry 會一直重送同一則。處理結果寫在 `app` 的 log。
- 同一個版號上一次 push 還在驗證 → `409`，這次的 push 不會註冊（log 有記）。等它結束再 push。
- 通知送不到（例如 `app` 沒起來）**不會讓 push 失敗**。registry 會在記憶體排隊重試，但 registry 重啟就沒了——那時請重 push 一次。
- 設定在 `docker/registry-config.yml`（compose 掛進 registry）。

典型用法（平台那台 VM 上的 CD 流程）：從公司 registry 拉下要上線的版本 → `docker tag` 成 `localhost:6000/<agent 名>:<版號>` → `docker push`。

**內部 Registry 的安全設計：** 只掛在 `zentra-net`、只對外發布 `127.0.0.1:6000`、沒有 TLS、沒有認證。**綁在 loopback 本身就是存取控制**，所以只能同一台機器 push。
要綁 `0.0.0.0` 或讓遠端 push，得先解決 TLS 和認證，需要重新設計。

## 註冊與驗證流程（用 API 註冊的路徑）

```
POST /v1/agents/{id}/versions {"semver", "image_ref"}
  app：解析 image_ref（只接受 repository@sha256:<64 個 hex>）
       → INSERT agent_versions（BUILDING）→ 送 zentra.build.execute → 回 202

worker：zentra.build.execute（q.build）
  1. 取得建置租約（10 分鐘）；這筆已經是終態就跳過              ← 同一任務被投遞兩次時的保護
  2. PULL     叫 Docker daemon 拉「那個精確的 digest」
              （外部 Registry 用平台這側的 credential；localhost:6000 不帶）
  3. INSPECT  檢查 platform / label / runtime contract；
              RepoDigests 必須包含註冊的那個 reference
  4. VALIDATE 開一個一次性驗證容器（env 只有 ZENTRA_GATEWAY_URL / ZENTRA_AGENT_VERSION_ID / ZENTRA_VALIDATION_TOKEN），
              跑 `zentra-agent validate`：import 使用者模組、檢查 build() 簽章、
              build 出還沒 compile 的 StateGraph、抽出 entrypoints schema
              → 用 validation token POST /internal/versions/{id}/validation
  5. 驗證容器以 0 結束 + payload 存在 + 再 inspect 一次 digest 沒變 → 同一個交易：
       舊的同版號 PUBLISHED → SUPERSEDED（如果有）；這一筆 → PUBLISHED（寫入 manifest、entrypoints、platform、size）

  任何一步失敗 → FAILED + BUILD_FAILED
      （details.stage = PULL | INSPECT | VALIDATE，message 帶驗證容器的輸出）
  不論成功失敗：都會撤銷 validation token、銷毀驗證容器
```

- validation 端點**只負責保存 payload**，不能自己把版本轉成 PUBLISHED。決定權在 build worker。相同 payload 重送沒關係；內容不同 → `422`。
- validation token 綁一個 `agent_version_id`，最長 10 分鐘，**不能**打 `/internal/runs/*`、`/llm/*`、`/egress/*`、`/notify/*`。
- **沒有可重新 pull 的 digest，就不可能成為 PUBLISHED**（P5 的保證）：`image_ref` / `image_digest` / `version_id` 三個欄位任何狀態都不能是 NULL，再加上 INSPECT 的 RepoDigests 比對。
- `version_id` = digest 十六進位的前 12 碼，只用來短顯示；真正的身分是完整 digest。

### `image_ref` 怎麼存

**呼叫端送什麼字串就原封不動存什麼。** registry 前綴的正規化（`docker.io/library/x` ≡ `docker.io/x` ≡ `x`）只在**比較時**做，不會改寫已存的值。

要特別注意：repository 第一段含 `.` 或 `:`，或等於 `localhost`，那就是 registry 主機，**必須原樣保留**。
例：`localhost:6000/alpine` **不等於** `alpine`（前者是你自己的 registry，後者是 Docker Hub 的）。

## 同版號重新註冊 = 取代

版號由開發者決定。用同一個版號再送一次，意思是「這個版號改成指向這個新 image」。

| 情況 | 結果 |
|---|---|
| 版號不存在，或只有 FAILED 的紀錄 | `202`，新增一筆 BUILDING |
| 版號有現役 PUBLISHED，送來**不同** image | `202`，新增一筆 BUILDING；**驗證通過才取代**，失敗的話舊的照常服務 |
| 送來的 `image_ref` 和現役那筆**完全相同** | `202`，直接回現役那筆（`PUBLISHED`），不重新驗證 |
| 這個版號已經有一筆 BUILDING | `409 VERSION_CONFLICT` |

**例子：**

```
1.0.0  PUBLISHED  image=A     ← 正在服務
再送 1.0.0 image=B
1.0.0  PUBLISHED  image=A     ← 還在服務（B 驗證中）
1.0.0  BUILDING   image=B
   ├ B 驗證通過 → A 變 SUPERSEDED，B 變 PUBLISHED，之後的 Run 用 B
   └ B 驗證失敗 → B 變 FAILED，A 繼續服務
```

- 取代 = **新增一筆 + 切換指向**，不是改寫。舊那筆改成 `SUPERSEDED`、記下 `superseded_at`，它的 digest **一個字都不動**；舊 Run 仍查得到當時跑的是哪個 image。`SUPERSEDED` 不會出現在任何版本端點，也不能被呼叫。
- 已經建立（包含排隊中）的 Run 綁定建立當下那一筆，**照舊 image 跑完**。
- `GET /versions/{semver}` 回的是**最近一次註冊**（輪詢靠它）。所以取代失敗後它會回 `FAILED`，但實際呼叫這個版號跑的仍是舊的 PUBLISHED。
- 資料庫保證：一個版號最多一筆 PUBLISHED、最多一筆 BUILDING（partial unique index）。

### 舊版本的回收：`zentra.version.retire`

取代發生的那個交易 commit 之後才派送。

1. 還有沒結束的 Run 綁著舊那筆 → 每 30 秒重試，最多 120 次（一小時）。還沒跑完就**放棄、保留 image**，寧可不刪。
2. 停掉綁著舊版本的常駐容器（撤銷 container token → 銷毀 → 標 STOPPED）。
3. 如果**任何 Agent** 還有未被取代的版本用同一個 digest → 不刪 image。
4. 否則：刪內部 Registry 的 manifest、刪主機上的 image（不加 force：還有容器在用就跳過）。

只刪平台自己用的那個 digest 參照；開發者在同一台主機上自己打的 tag 不動。
**刪 manifest 不等於釋放空間**，blob 要停機跑 `registry garbage-collect` 才會真的消失。畫面上不可以說「已釋放空間」。

## 刪除 Agent（硬刪）

`DELETE /v1/agents/{id}` → `204`，**不可復原**。

同一個交易會刪掉：Agent、所有版本、容器紀錄、Run、事件、LLM 呼叫、LLM 憑證、API key、設定、機密、出口規則、寄件者、出口稽核，以及 MCP key 對這個 Agent 的勾選（`mcp_key_agents`）。
因為 `runs` 和 `agent_containers` 對版本**沒有 CASCADE**，所以要由下往上刪（`services/agents.py::delete_agent`）。

**刪資料庫之前**，先收集要在平台外面清的東西（容器 id、所有 `image_ref`）；commit 之後送 `zentra.agent.purge`：
停容器、刪主機上的 image、刪內部 Registry 的 manifest（外部 Registry 的不碰）。**清理失敗不會讓刪除失敗**，結果寫在 worker log。

代價：稽核紀錄（`llm_calls`、`egress_calls`）會一起消失，要留請先匯出。確認視窗必須把這件事講出來。
沒有批次刪除的端點，Console 的批次刪除是一個一個呼叫。

## 下游 API key

一個 Agent 可以發很多把 key，給不同的下游應用。

**這種 key 買到的是「這筆用量是誰打的」，不是「擋住沒授權的人」。**（要擋人看下面 `ZENTRA_REQUIRE_AGENT_API_KEY`。）
MCP 不用這種 key，看 [mcp.md](mcp.md) §MCP key。

### 發放與保存

- 明文格式 `zk_live_<隨機字串>`，**只在建立的那次回應出現**。沒有「再看一次」的端點——遺失只能重發。
- 資料庫只存 **SHA-256 雜湊**（`agent_api_keys.key_hash`，全表唯一）和前綴 `key_prefix`（例如 `zk_live_a1b2`，在列表裡認得出是哪一把）。
  這刻意和 LLM 憑證不同：LLM 憑證要把明文轉給供應商，只能加密保存；這個只需要驗證身分，存雜湊更安全。
- **軟撤銷**：`DELETE` 回 `200` 並帶 `revoked_at`，那一列保留——硬刪會讓它過去的 Run 變成沒人認領的帳。重複撤銷沒關係。列表會包含已撤銷的。

### 驗證規則（REST 建 Run）

| 請求 | 結果 |
|---|---|
| 沒帶 `Authorization` | 放行，`runs.api_key_id = NULL`（Console 發起的都是這種） |
| 帶了有效的 key | 放行，記下 `api_key_id`、更新 `last_used_at` |
| 帶了無效或已撤銷的 key | `401 UNAUTHENTICATED` |
| 帶了**別的 Agent 的** key | `403 PERMISSION_DENIED` |
| 設了 `ZENTRA_REQUIRE_AGENT_API_KEY=true` 且沒帶 | `401`。**設之前先確認 Console 有辦法帶認證**，否則 Console 整個不能用 |

**為什麼「帶了但打錯」要擋、不能當作沒帶？** 因為這兩者意圖不同。默默當匿名放行，呼叫端會以為自己的用量有被記到。

### 用量歸戶

`runs.api_key_id` 記錄是哪把 key 觸發的這筆 Run；`llm_calls` 透過 `run_id` join 回來，**不另外存一份**（存兩份會不一致）。
查詢端點看 [api.md](api.md) §用量與除錯。`by_key` 一定有 `key_id: null` 那一列（沒帶 key 的呼叫）。
