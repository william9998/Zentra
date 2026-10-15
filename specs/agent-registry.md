# Agent 註冊系統設計（含下游 API key）

> Agent 怎麼進平台、版本怎麼驗證與發布、同版號怎麼取代、怎麼刪，以及發給下游應用的 key。
> 端點形狀見 [api.md](api.md)。

## 概念

```
Agent（metadata：name、description）
 └─ 版本 agent_id + semver → 一個 OCI image digest
     狀態：BUILDING → PUBLISHED / FAILED；PUBLISHED 被同版號取代 → SUPERSEDED
```

- `POST /v1/agents` 只建 metadata，**還沒有版本、不能呼叫**。`name` 全表唯一。
- 平台**不接收 image 檔案**。image 在 Registry，平台只存 digest 與驗證時抽出的 metadata。
- 註冊版本**不會**啟動容器；第一筆 Run 才會（見 [container-orchestration.md](container-orchestration.md)）。

## image 契約（使用者要交出什麼）

| 要求 | 值 |
|---|---|
| label | `io.zentra.image-contract=zentra.image/v1` |
| 執行檔 | `/opt/zentra/bin/zentra-agent`，模式 `validate` / `run` / `serve` |
| 使用者 | non-root `65534:65534`；平台以唯讀 rootfs 啟動，只給 `/workspace` tmpfs |
| port | 容器內**不開** HTTP port（REST / MCP 是 `app` 的門面） |
| 模組 | `agent.py`，放在 `/opt/zentra/app/agent.py` |

官方 base image `zentra/agent-base:1.0`（`docker/agent-base.Dockerfile`，本機 build、不發佈）已備好以上全部 + Python 3.12 + `zentra_sdk` + LangGraph。
使用者的 Dockerfile 只要兩行：`FROM zentra/agent-base:1.0` + `COPY agent.py /opt/zentra/app/agent.py`。範例在 `examples/my-agent/`。

```python
from zentra_sdk import ZentraContext, agent
from langgraph.graph import StateGraph

@agent(name="my-agent", version="1.0.0")
def build(ctx: ZentraContext) -> StateGraph:   # 只接受一個位置參數，回傳「未 compile」的 StateGraph
    ...
```

`ctx` 提供：`ctx.llm()` · `ctx.logger` · `ctx.emit()` · `ctx.config` · `ctx.secret_names` · `ctx.http` · `ctx.send_email()`（行為見 [gateway.md](gateway.md)）。
**沒有** `ctx.secrets`（取值，永久否決）、`ctx.tools`、`ctx.knowledge`。

- **不要在 `build()` 裡對 `config` 的鍵或機密做硬性要求**：驗證時 `config = {}`、`secret_names = []`，會在發布時就失敗。用 `.get()` 帶預設值，或在節點裡檢查。
- SDK 的依賴由 `packages/sdk/pyproject.toml` 自己宣告，image 契約不列舉。

## 推 image 到哪裡

| 放哪 | 怎麼放 | `image_ref` |
|---|---|---|
| 外部 Registry（GHCR、自建） | 自己 push | `ghcr.io/acme/agent@sha256:…` |
| 平台內部 Registry | **在跑平台的那台主機上** `docker push localhost:6000/<agent 名>:<版號>` —— **push 就是註冊**（見下） | `localhost:6000/<agent 名>@sha256:…` |

外部 Registry 的 image 用 `POST /v1/agents/{id}/versions` 以 digest 註冊（digest 用 `docker inspect --format='{{index .RepoDigests 0}}' <image>` 取）。

## push 即註冊（內部 Registry）

```
docker push localhost:6000/<agent 名>:<版號>   ==   註冊 <agent 名> 的 <版號>
```

| 部分 | 規則 |
|---|---|
| repository | **就是 Agent 名稱**，只能一層、符合 Agent 名稱規則。不存在就**自動建立** Agent |
| tag | **就是版號**（與 `POST /versions` 的 semver 規則相同；Docker tag 不能有 `+`，所以沒有 build metadata） |
| 不符合約定 | 略過並記 warning log：`latest`、`dev`、多層 repo（`team/x`）、layer / config blob 事件 |

```
docker push ──▶ registry ──通知──▶ app POST /internal/registry/events
                                     1. 挑出「manifest 的 push、有 tag、repo 是 Agent 名、tag 是版號」的事件
                                     2. HEAD registry /v2/<repo>/manifests/<tag>：tag 現在真的指向那個 digest 嗎？不是就略過
                                     3. 同一個交易：找 Agent（沒有就建）→ create_version（與 POST /versions 同一個函式）
                                     4. 需要驗證 → 送 zentra.build.execute → 之後與一般註冊完全相同
```

- 之後的行為與 API 註冊**完全一樣**：驗證、發布、同版號 = 取代、成為最新版時舊容器收掉。
- **通知端點沒有 token**（registry 帶不了我們的憑證）。但 `/internal/*` 是沙盒連得到的，所以**通知內容不被相信**：
  第 2 步向 registry 確認之後才註冊。偽造的通知最多只能讓平台註冊「registry 裡本來就是那樣」的東西，跟真的 push 一樣。
- **永遠回 200**：回別的 registry 會一直重送同一則。處理結果寫 `app` 的 log。
- 同一個版號上一次 push 還在驗證中 → `409`，這次的 push 不會註冊（log 有記），等它結束再 push。
- 通知送不到（`app` 沒起來）不會讓 push 失敗；registry 會在記憶體裡排隊重試，但 registry 重啟就丟了 —— 那時重 push 一次。
- 設定在 `docker/registry-config.yml`（compose 掛進 registry）。

典型用法（平台那台 VM 上的 CD）：從公司 registry 拉下要上線的版本 → `docker tag` 成 `localhost:6000/<agent 名>:<版號>` → `docker push`。

內部 Registry：只掛 `zentra-net`、只發布 `127.0.0.1:6000`、無 TLS、無認證。**loopback 綁定本身就是存取控制**，所以只能同機 push。
綁 `0.0.0.0` 或遠端 push 要先解決 TLS 與認證，需要重新設計。

## 註冊與驗證流程

```
POST /v1/agents/{id}/versions {"semver", "image_ref"}
  app：解析 image_ref（只接受 repository@sha256:<64 hex>）→ INSERT agent_versions (BUILDING) → 送 zentra.build.execute → 202

worker：zentra.build.execute（q.build）
  1. 取得建置租約（10 分鐘），已終態就跳過                                  ← 重複投遞的冪等
  2. PULL     令 daemon pull exact digest（外部 Registry 用平台側 credential；localhost:6000 不帶）
  3. INSPECT  檢查 platform / label / runtime contract；RepoDigests 必須含註冊的那個 reference
  4. VALIDATE 起一次性驗證容器（env 只有 ZENTRA_GATEWAY_URL / ZENTRA_AGENT_VERSION_ID / ZENTRA_VALIDATION_TOKEN）
              跑 `zentra-agent validate`：import 使用者模組、檢查 build() 簽章、build 出未 compile 的 StateGraph、
              抽出 entrypoints schema → 用 validation token POST /internal/versions/{id}/validation
  5. 容器以 0 結束 + payload 存在 + 再 inspect 一次 digest 沒變 → 同一個交易：
       舊的同版號 PUBLISHED → SUPERSEDED（若有）；這一筆 → PUBLISHED（寫 manifest、entrypoints、platform、size）
  任何一步失敗 → FAILED + BUILD_FAILED（details.stage = PULL | INSPECT | VALIDATE，message 帶驗證容器的輸出）
  不論結果：撤銷 validation token、銷毀驗證容器
```

- validation 端點**只保存 payload**，不能自己把版本轉 PUBLISHED；裁決權在 build worker。相同 payload 重送冪等，不同內容 → `422`。
- validation token 綁一個 `agent_version_id`、最長 10 分鐘，**不能**打 `/internal/runs/*`、`/llm/*`、`/egress/*`、`/notify/*`。
- **沒有可重新 pull 的 canonical digest，不得成為 PUBLISHED**（P5 不變式）：`image_ref` / `image_digest` / `version_id` 三欄任何狀態都 `NOT NULL`，加上 INSPECT 的 RepoDigests 比對。
- `version_id` = digest hex 前 12 碼，只供短顯示；權威身份是完整 digest。

### `image_ref` 的保存規則

**原封不動保存呼叫端送來的字串。** registry 前綴正規化（`docker.io/library/x` ≡ `docker.io/x` ≡ `x`）只發生在**比較時**，不改寫已保存的值。
repository 第一段含 `.` 或 `:`、或等於 `localhost` → 那是 registry host，**必須原樣保留**：`localhost:6000/alpine` **不等於** `alpine`。

## 同版號重新註冊 = 取代

版號由開發者決定。用同一個版號再送一次，意思就是「這個版號換成這個 image」。

| 情況 | 結果 |
|---|---|
| 版號不存在，或只有 FAILED 的紀錄 | `202`，新的 BUILDING |
| 版號有現役 PUBLISHED，送來不同 image | `202`，新的 BUILDING；**驗證通過才取代**，失敗則舊的照常服務 |
| 送來的 `image_ref` 與現役那筆**完全相同** | `202` 回現役那筆（`PUBLISHED`），不重 build |
| 這個版號已有一筆 BUILDING | `409 VERSION_CONFLICT` |

- 取代 = **新增一筆 + 切換指向**，不是改寫。舊那筆改成 `SUPERSEDED`、記 `superseded_at`，digest 一個字都不動；
  舊 Run 仍查得到當時跑的是哪個 image。`SUPERSEDED` 不出現在任何版本端點、不能被呼叫。
- 已建立（含排隊中）的 Run 綁定建立當下那一筆，**照舊 image 跑完**。
- `GET /versions/{semver}` 回**最近一次註冊**（輪詢靠它），所以取代失敗後它回 `FAILED`，但呼叫這個版號跑的仍是舊的 PUBLISHED。
- 資料庫保證：一個版號至多一筆 PUBLISHED、至多一筆 BUILDING（partial unique index）。

### 舊版本的回收：`zentra.version.retire`

取代發生的交易 commit 之後派送。

1. 還有未終結的 Run 綁著舊那筆 → 每 30 秒重試，最多 120 次（一小時）。還沒跑完就**放棄、留著 image**，寧可不刪。
2. 停掉綁著舊版本的常駐容器（撤銷 container token → 銷毀 → 標 STOPPED）。
3. 還有**任何 Agent** 的未取代版本用同一個 digest → 不刪 image。
4. 否則：刪內部 Registry 的 manifest、刪主機上的 image（不 force：還有容器在用就跳過）。

只刪平台用的那個 digest 參照；開發者在同一台主機上自己打的 tag 不動。
**刪 manifest 不等於釋放空間**，blob 要停機跑 `registry garbage-collect` 才消失。畫面上不可以說「已釋放空間」。

## 刪除 Agent（硬刪）

`DELETE /v1/agents/{id}` → `204`，**不可復原**。

同一個交易刪掉：Agent、所有版本、容器紀錄、Run、事件、LLM 呼叫、LLM 憑證、API key、設定、機密、出口規則、寄件者、出口稽核。
因為 `runs` 與 `agent_containers` 對版本**沒有 CASCADE**，要由下往上刪（`services/agents.py::delete_agent`）。

**刪 DB 之前**先收集要在平台外清的東西（容器 id、所有 `image_ref`），commit 後送 `zentra.agent.purge`：
停容器、刪主機 image、刪內部 Registry manifest（外部 Registry 的不碰）。**清理失敗不讓刪除失敗**，結果寫 worker log。

代價：稽核軌跡（`llm_calls`、`egress_calls`）一起消失，要留請先匯出 —— 確認視窗必須講出來。
沒有 bulk 端點，Console 批次刪除是逐一呼叫。

## 下游 API key

一個 Agent 可以發多把 key 給不同的下游應用。**key 買到的是「這筆用量是誰打的」，不是「擋住未授權的人」**。

### 發放與保存

- 明文格式 `zk_live_<token_urlsafe>`，**只在建立的回應出現一次**，沒有「再看一次」的端點 —— 遺失只能重發。
- 只存 **SHA-256 雜湊**（`agent_api_keys.key_hash`，全表唯一）與前綴 `key_prefix`（例 `zk_live_a1b2`，列表裡認得出是哪一把）。
  刻意跟 LLM 憑證不同：那個要把明文轉發給供應商，只能加密；這個只要驗證身分，雜湊更安全。
- **軟撤銷**：`DELETE` 回 `200` 帶 `revoked_at`，那一列保留 —— 硬刪會讓它過去的 Run 變成無主的帳。重複撤銷冪等。列表含已撤銷的。

### 驗證規則（REST 建 Run 與 MCP 一字不差）

| 請求 | 結果 |
|---|---|
| 沒帶 `Authorization` | 放行，`runs.api_key_id = NULL`（Console 都是這種） |
| 帶了有效 key | 放行，記 `api_key_id`、更新 `last_used_at` |
| 帶了無效或已撤銷的 key | `401 UNAUTHENTICATED` |
| 帶了**別的 Agent 的** key | `403 PERMISSION_DENIED` |
| 設 `ZENTRA_REQUIRE_AGENT_API_KEY=true` 且沒帶 | `401` —— **設之前先確認 Console 有辦法認證**，否則 Console 整個不能用 |

「帶了但打錯」跟「沒帶」是不同意圖，不能默默當匿名，否則呼叫端會以為用量有記到。

### 用量歸戶

`runs.api_key_id` 記觸發那筆 Run 的 key；`llm_calls` 透過 `run_id` join 回來，**不另存一份**（兩份會不一致）。
查詢端點見 [api.md](api.md) §用量與除錯。`by_key` 一定含 `key_id: null`（沒帶 key 的呼叫）。
