# ZEN-008 開發前驗收條件

| 欄位 | 值 |
|---|---|
| 依據 | `SPEC.md v0.3.1`、`api-contract.md v0.3.1`、ZEN-008 feature spec、ADR-0005、ADR-0008 |
| 範圍 | OCI digest 註冊 → `q.build` pull/inspect/validate → PostgreSQL metadata → Console/REST/MCP → `q.run` → 一 Run 一盒 → 終態銷毀 |
| 狀態 | **開發前門檻；尚未執行、尚未裁定通過** |

本文件在產品實作前定義「完成」的可執行證據。任何 `skipped`、未啟用的整合環境或 mock driver 結果都不能替代真 Docker／PostgreSQL／Redis／Celery 證據。測試不得依賴公網或真實 LLM；OCI 測試使用測試期間啟動的本機 Registry fixture，它不是產品 compose 的第五個長駐服務。

## 1. 測試分層與固定 fixture

| 層級 | 必要環境與證據 |
|---|---|
| 單元 | digest reference parser、semver/唯一性、狀態轉換、`q.build`/`q.run` claim、token scope/TTL、SandboxSpec 組裝與錯誤 stage mapping；使用 fake Docker/Registry，不宣稱隔離通過。 |
| 契約 | 對 live FastAPI/OpenAPI 執行 schema 測試；逐欄位比對 `api-contract.md v0.3.1`；MCP 使用 Streamable HTTP client，不以函式直呼代替。 |
| Docker 整合 | 真 Docker daemon、本機 OCI Registry、真 PostgreSQL/Redis/Celery。以唯一 repository/digest 分開驗 cache miss、cache hit、cache 被清除與 Registry 故障。 |
| E2E | Playwright 操作真 Console 與後端；不得以攔截 API 的合成回應當成功主線證據。 |
| 安全 | 真 validation/run container 內執行探針；檢查網路、env、mount、uid/capabilities、資源限制、token 越權與容器銷毀。 |
| 負載／競爭 | 至少兩個真 Celery delivery 競爭同一 build/run；Run 併發上限依 SPEC 預設 10 驗證。 |

固定建立下列 image fixture，全部以 digest push 到本機 Registry：

1. `valid-v1`：符合 `zentra.image/v1`，回傳未 compile `StateGraph`，有兩個 entrypoint，可回報容器 id、RepoDigest 與安全探針結果。
2. `valid-v2-same-tag`：與 `valid-v1` 不同內容，測試 tag 被重指後仍執行原 digest。
3. 缺 label、label 值錯誤、缺 executable、host platform 不相容、不能以 `65534:65534` 啟動、需要寫 rootfs、回傳非 `StateGraph`、回傳已 compile graph、manifest/schema 無效的各一個 fixture。
4. validate hang/crash、run hang/crash、run 不回寫 result、事件重送、env/network/filesystem/resource probe fixture。

所有容器必須帶可查詢的 Zentra role 與 resource id label。清理斷言使用有界輪詢，終態後以 Docker inspect 與 label query 兩種方式確認不存在。

## 2. 可執行驗收條件

### A. Agent Registry、OCI 註冊與 PostgreSQL

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z8-01` Agent metadata | 契約／整合 | `POST /v1/agents` 回 `201`，欄位、UUID v7、UTC 時間與 null 語意逐欄位符合 API v0.3.1；PostgreSQL 恰新增一筆 `agents`。`GET /v1/agents` 只列未 soft-delete 項目並正確 cursor 分頁；`GET /v1/agents/{id}` 同時有 `rest`、`mcp`、`openapi` 三個 endpoint。 |
| `Z8-02` canonical digest 邊界 | 單元／契約 | `POST /v1/agents/{id}/versions` 只接受 `repository@sha256:<64 hex>` 的完整 canonical reference。tag-only、tag 加 digest、缺 repository、digest 長度錯、非 hex、非 `sha256`、空字串與多餘 command/port/network/volume/user/capability 欄位皆回 `422 VALIDATION_ERROR`，且 DB 無版本列、`q.build` 無訊息。 |
| `Z8-03` 建立版本契約 | 契約／整合 | 合法請求回 `202`；response 的 `agent_version_id`、`semver`、`version_id`、`source_type=OCI_IMAGE`、`artifact`、`status=BUILDING`、`error=null`、`created_at` 完全符合 API v0.3.1。transaction commit 後才有一個 `q.build(agent_version_id)` 訊息；worker 不接受 image ref 作 task authority。 |
| `Z8-04` DB metadata only | migration／整合／安全 | 全新 PostgreSQL 從零 migrate 到 head 後，`agents`/`agent_versions` 欄位、FK、`(agent_id,semver)` 唯一約束及完整 `image_ref`/digest 約束存在。schema 不含 image/blob/archive/layer 欄位，也不含 Registry password/token/auth 欄位；資料列、API response、結構化 log、Celery payload 均不含 Registry credential 或 image bytes。image 本體只存在 Registry 與 Docker cache。 |
| `Z8-05` P5 不可變 | 整合 | 成功發布後，`semver`、`image_ref`、`image_digest`、`runtime_contract`、`manifest`、`entrypoints` 皆不可 PATCH/覆寫。相同 `agent_id+semver` 的 HTTP 重送回 `409 VERSION_CONFLICT`，資料庫仍一列且不新增 `q.build`；同 digest 可由另一 Agent/semver 明確註冊成獨立列。 |

### B. `q.build` pull、inspect、validate 與發布

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z8-06` 發布成功主線 | Docker 整合 | cache miss 時 worker 使用平台側 Registry credential pull request 中的 exact digest；inspect 的 RepoDigest 必須等於 DB 完整 digest，platform 相容且 label 正確。validation container 以固定 `validate` command、uid/gid `65534:65534`、read-only rootfs、`/workspace` tmpfs、internal network 啟動。合法 payload、exit 0、digest 再確認後，單一 transaction 保存 platform/size/runtime contract/manifest/entrypoints、設定 `published_at` 並轉 `PUBLISHED`。 |
| `Z8-07` digest 不隨 tag | Docker 整合 | 先發布 `valid-v1` digest，再把同 repository tag 重指到 `valid-v2-same-tag`。後續 validate 與至少兩次 Run 的 RepoDigest 都仍是 DB 的 `valid-v1` 完整 digest，輸出也為 v1；程式路徑中不得 resolve 或執行 tag。 |
| `Z8-08` build claim／冪等 | 單元／Celery 整合 | 同一 `agent_version_id` 同時送兩個真 Celery delivery，只有一個取得 row-lock/claim，至多一次 pull、一次 validation container create；另一個不建立資源。`BUILDING` 中斷後重送可安全接續或收斂為單一終態；`PUBLISHED`/`FAILED` 終態重送只回既有結果，不改 metadata、不重拉、不再建 container。Celery 設定需證明 `q.build` 與 `q.run` 分流且 task `acks_late=True`。 |
| `Z8-09` stage failure mapping | 單元／Docker 整合 | Registry 連線/認證失敗與不存在 digest → `FAILED + BUILD_FAILED + details.stage=PULL`；RepoDigest 不符、platform 不相容、label/path/啟動條件不符 → stage `INSPECT`；validation 回報 invalid、schema/graph/runtime contract 錯、非零退出、逾時、零退出但無 payload → stage `VALIDATE`。三組皆 `manifest/entrypoints/published_at=null`，只產生一次終態。 |
| `Z8-10` runtime contract 矩陣 | Docker 整合 | 逐一執行 §1 的 invalid fixture；label、固定 executable、`validate|run` mode、host platform、non-root、read-only rootfs、`/workspace` tmpfs、manifest、JSON Schema、未 compile `StateGraph` 任一缺失都不能 `PUBLISHED`。測試須證明平台覆寫 image ENTRYPOINT/CMD，忽略 image 宣告的 port、healthcheck、user、volume。 |
| `Z8-11` validation Gateway | 契約／整合 | `/internal/versions/{id}/validation` 不出現在公開 OpenAPI。合法 payload 回 `202 {"accepted":true}` 但不自行轉 `PUBLISHED`；相同 payload 重送冪等，不同第二 payload 回 `422 VALIDATION_ERROR`。worker 只有在 payload 存在、container exit 0 且 digest 相同時才發布。 |
| `Z8-12` validation token | 安全 | token 綁定單一 `agent_version_id`、TTL `<=10m`；缺少/無效/過期為 `401`，A token 打 B version 為 `403` 且 B 不變。validation token 打任何 `/internal/runs/*` 或 `/internal/llm/*` 為 `403`；成功、失敗、逾時與 worker crash recovery 後 token 均撤銷，再用為 `401`。 |
| `Z8-13` validation 清理 | Docker 整合 | 成功、PULL/INSPECT/VALIDATE 失敗、timeout、cancel/revoke 與 worker retry 每條路徑完成後，validation container 在有界時間內 inspect 不存在，`/workspace` 資料不可由下一個 container 取得，無 `zentra.role=validation,zentra.agent_version_id=<id>` 殘留。 |
| `Z8-14` cache 與 Registry 故障 | Docker 整合 | `PUBLISHED` image cache 被刪後，worker 只按 DB exact digest 重拉；Registry 暫時不可用時 Run 收斂為 `FAILED` 且 `error.code=SANDBOX_ERROR`、`retryable=true`，不得改用同 tag、同 repository 其他 image 或任一本地 image。Registry 恢復後依 ZEN-004 重試可使用原 digest成功。 |

### C. P1–P5、憑證與四層隔離

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z8-15` P1 執行邊界 | 靜態＋Docker 整合 | API/worker 原始碼與映像不得 import/exec/eval/runpy image 內模組，也不把 image filesystem bind 到平台 process。帶 host marker 的 `build()` 只能在 validation/run container process namespace 執行；host/API/worker filesystem 不出現 marker。平台 process 只經 Docker/SandboxDriver 控制容器。 |
| `Z8-16` P2 framework | Docker 整合 | valid image 的 `build(ctx)` 回未 compile LangGraph `StateGraph`，平台 runtime compile 後正確執行。回非 StateGraph、已 compile graph 或其他 Agent framework 的 fixture 在 `VALIDATE` 失敗，不得降級成任意 command 執行。 |
| `Z8-17` P3 schema 同源 | 契約 | 對每個 entrypoint，把 DB `entrypoints.input_schema`、Agent OpenAPI 3.1 input schema、MCP `tools/list` input schema 正規化後做深比較，語意完全等價；output schema 與名稱/描述也同源。不得有只存在 REST 或只存在 MCP 的 entrypoint。 |
| `Z8-18` P4 mode env | 安全 | 真 validation container 的 `ZENTRA_*` 精確為 `ZENTRA_GATEWAY_URL`、`ZENTRA_AGENT_VERSION_ID`、`ZENTRA_VALIDATION_TOKEN`；真 run container 精確為 `ZENTRA_GATEWAY_URL`、`ZENTRA_RUN_ID`、`ZENTRA_RUN_TOKEN`。兩者 `env`、mount、inspect config 與 `/proc/*/environ` 均無 Registry credential、LLM key、DB/Redis URL/password、API key、Docker socket；validation/run token 不出現在 API response 或一般 log。 |
| `Z8-19` Registry credential 邊界 | 安全 | 私有 Registry fixture 證明 worker 能用 VM Docker credential store/compose secret pull；該 credential 不寫 PostgreSQL/Redis/Celery payload，不傳 validation/run SandboxSpec，不 mount 進 container，不出現在 Docker inspect、runtime env、Gateway payload、API/MCP/Console 或結構化 log。pull 失敗訊息需遮蔽 credential。 |
| `Z8-20` L1 網路 | 安全／Docker 整合 | validation 與 run container 內連 `postgres:5432`、`redis:6379`、`1.1.1.1`、`169.254.169.254` 全失敗；帶正確短效 token 存取各自 Internal Gateway 路由成功。Docker inspect 顯示只接 `internal: true` sandbox network，無 default bridge/host network、無 published port。 |
| `Z8-21` L2 核心 | 安全／Docker 整合 | 兩種 container 內 `id` 為 `65534:65534`；`cap_drop=ALL`、`no-new-privileges`、Docker 預設 seccomp；`mount -t proc`、`unshare`、`ptrace` 失敗，不能 privileged、不能掛 docker.sock。`SandboxDriver.capabilities().l2_kernel_isolation` 必須為 `false`。gVisor、核心逃逸與實體主機隔離測試固定標 `skipped/未覆蓋`，不得標 passed 或用 digest 取代。 |
| `Z8-22` L3 資源 | 安全／負載 | inspect 證明 memory 2 GiB、pids 256、CPU 2 core、無 swap、stdout 10 MiB 上限與 wall-clock timeout。fork bomb、記憶體炸彈、無窮迴圈各自被終止、container 銷毀、版本/Run 收斂為規定錯誤，且同時運行的鄰近 sandbox 成功完成。 |
| `Z8-23` L4 檔案 | 安全／Docker 整合 | 寫 `/`、`/app`、image code/runtime path 失敗；只可寫 512 MiB `/workspace` tmpfs。無 hostPath、任意 volume、Docker socket；container 銷毀後 secret marker 不存在，下一個 validation/Run 讀不到前一盒資料。 |
| `Z8-24` token 交叉權限 | 安全／整合 | run token A 對 run B 的 bootstrap/events/result 皆 `403` 且 B 無資料變化；run token 打 validation endpoint為 `403`。validation token與run token都不能互換、不能存取超出 scope 的 Internal Gateway。Run 終態後 token 再用為 `401`。 |

### D. REST、MCP、Console 與 Run 生命週期

| ID | 層級 | 通過斷言 |
|---|---|---|
| `Z8-25` 版本選擇 | 契約／整合 | REST 指定 `PUBLISHED` semver 時 Run FK 指向該 `agent_version_id`；省略 version 使用最新 `PUBLISHED`。`BUILDING`/`FAILED`/未知版本回 `409 AGENT_NOT_PUBLISHED` 且不建 Run、不送 `q.run`。呼叫 body 夾帶 `image_ref` 或容器控制欄位回 `422`。 |
| `Z8-26` REST 建 Run | 契約／整合 | `POST /v1/agents/{id}/runs` 回 `202` 與 API v0.3.1 完整 shape，DB 建一筆 `QUEUED` Run、`trigger=API`，transaction commit 後恰送一個 `q.run(run_id)`。input 依所選 entrypoint schema 驗證；空值、缺 required、型別錯、未知 entrypoint、timeout 0/901 皆 `422` 且不建 Run。request body 出現任何 `trigger` 值都回 `422 VALIDATION_ERROR`，DB/queue 不變；合法 body 分別搭配 `X-Zentra-Trigger`、`Origin`、`Referer` 與任意同義自訂 header 時仍建立 `trigger=API` 的 Run。同步 `runs:invoke` 使用同一路徑，60 秒上限符合契約。 |
| `Z8-27` MCP 建 Run | 契約／整合 | `tools/list` 完成 `Z8-17`；每個 `tools/call` 都建立相同 Run model 並恰送一個 `q.run`，DB `trigger=MCP`。預設同步成功回完整結果，超過 60 秒依契約回 timeout；`_async:true` 回 `run_id`，可由公開 GET/SSE 查同一 Run。未知 tool/schema 錯誤不建 Run。 |
| `Z8-28` Console 註冊 | Playwright E2E | 從 Agent 列表建立 metadata，進入註冊版本頁；表單要求 semver 與 canonical `image_ref`，不出現檔案拖放、tar upload、Dockerfile build 或 credential 欄位。提交 JSON 後顯示/輪詢 `BUILDING → PUBLISHED`；失敗顯示完整 `BUILD_FAILED` message 與 stage。長 image ref 視覺不溢位，Copy 得完整 canonical ref。Loading/Empty/Error/Streaming/Success 五態及鍵盤、focus、文字狀態依屆時已由 architect 核准的 `ui-contract.md` 驗證。 |
| `Z8-29` Console 呼叫 | Playwright E2E | Agent 詳情顯示 PUBLISHED 版本、完整 digest 可複製、entrypoints、REST/MCP/OpenAPI 三個 endpoint。使用者可選版本（預設最新 PUBLISHED）與 entrypoint、輸入 JSON；前端 schema 提示後仍由後端權威驗證。Playwright 攔截實際 request 證明 Console 不送 body/header ingress discriminator；成功 POST 後 DB 固定 `trigger=API`、恰送一個 `q.run`，瀏覽器導向 `/console/runs/{run_id}` 並由真 SSE 顯示終態。BUILDING/FAILED 版本不可選。 |
| `Z8-30` 三門面同底層 | 整合 | 分別由 direct REST、Console、MCP 對同一 version/entrypoint 建 Run。三筆 Run 都可在 DB/Celery trace 證明經 `q.run`，使用相同發布 digest與相同 input schema；trigger 依序為 `API`、`API`、`MCP`。任何門面都不直連 image port或繞過 worker/Sandbox Manager。 |
| `Z8-31` 一 Run 一盒 | Docker E2E | 三門面各一筆加兩筆並發 Run，共五筆的 container ID 全不同；每筆只有一個 Celery task claim、一個 sandbox，inspect RepoDigest 都等於各自 Run FK 的 DB digest，固定 command 為 `zentra-agent run`。LangGraph node 不拆成 Celery task。每筆終態後 label query 與 inspect 均證明 container 不存在。 |
| `Z8-32` 事件與唯一終態 | 整合回歸 | bootstrap/events/result 完整符合 API v0.3.1；同 `(run_id,event_id)` 重送只存一筆與同 seq，DB commit 後 Redis 暫時失敗可用同 event_id 補送，SSE 依 seq 去重排序。result endpoint 不改 Run 終態；worker 在 container exit 後唯一寫一個終態與一個 `run.status`。Run trigger 值域只有 `API`/`MCP`；不得出現 `WAITING_INPUT`、`tool.call`、`tool.result`、`interrupt`、`tier`。 |
| `Z8-33` Run 失敗與清理 | Docker 整合 | create/start 失敗、image cache miss 且 Registry 不可用、runtime 非零退出、零退出未回 result、user exception 分別收斂為契約枚舉中的 `FAILED` error；output/error/null 欄位正確、部分事件保留、唯一終局事件、token 撤銷、container 在有界時間內銷毀。 |
| `Z8-34` 逾時與取消 | Docker 整合／E2E | hang fixture 以 `timeout_seconds=1` 被 wall-clock 終止，狀態 `FAILED`、`error.code=SANDBOX_TIMEOUT`；QUEUED/SCHEDULING/RUNNING 各階段取消皆最終 `CANCELLED`。兩者都停止 task/process、撤銷 token、銷毀 container 且不晚到覆寫終態；終態後 cancel 回 `409`。 |
| `Z8-35` 重送與 recovery | Celery／Docker 整合 | 相同 `run_id` 的兩個真 delivery 只有一個 claim/create；worker 在 DB 終態 commit、Redis publish、token revoke、destroy 任一邊界 crash 後重送，須補完尚未完成的清理/通知，但不得建立第二盒、重跑 user code、改 output 或重複終局事件。使用 ZEN-004 `Idempotency-Key` 時，24 小時內 HTTP 重送仍只建一筆 Run。 |
| `Z8-36` 併發上限 | 負載 | 同時維持 10 個 active Run 可受控執行；第 11 個依 SPEC/API contract 回 `429 QUOTA_EXCEEDED` 與 `Retry-After`，不建立 sandbox。釋放名額後新 Run 可執行；壓力結束零 sandbox/validation container 殘留。 |

## 3. 品質閘門與裁定規則

| Gate | ZEN-008 必要證據 |
|---|---|
| G1 | spec-keeper 確認實作符合 SPEC v0.3.1、ADR-0008 與 P1–P5，`coordination/DRIFT.md` 無本任務未解漂移。 |
| G2 | API/OpenAPI/MCP/Internal Gateway 契約測試全綠，逐欄位回報所驗版本 `v0.3.1`。 |
| G3 | 新增純邏輯有單元測試，OCI 發布與三門面 Run 有真 Registry/PostgreSQL/Redis/Celery/Docker 整合證據；Console 有真後端 Playwright E2E。 |
| G4 | `Z8-18`–`Z8-24` 全部有真容器證據；`capabilities()` 誠實。報告必須寫：「Docker 四層控制已依各斷言驗證；gVisor／核心逃逸與實體主機隔離未覆蓋，本架構不防此類攻擊，只允許信任來源 image。」不可只寫「安全測試通過」。 |
| G5 | backend/platform/frontend/QA 交接齊全，CHANGELOG 有 ZEN-008，SPEC/API/UI 版本與實作一致。 |
| G6 | BOARD 無 ZEN-008 未解 `SPEC-QUESTION`。UI 驗收以 architect 推進 `SPEC_READY` 時核准的 `ui-contract.md` 版本為準。 |

判定規則：任何斷言失敗、未執行或因環境缺失而 skipped，都不得把該斷言標為通過。真 Docker 不可用時，可保留單元/契約結果，但 G3/G4 不通過。gVisor／核心逃逸／實體隔離是 ADR-0005 明定的唯一預期 skipped；它們不是缺陷，也不能被計入通過數。若 image 來源改為第三方或不可信，必須停止驗收、開 SPEC-QUESTION，重新檢討 ADR-0001/0005，不能沿用本門檻放行。

## 4. 開發前狀態

- 上述測試目前全部 **未跑**；本文件不宣稱任何功能或品質閘門通過。
- 尚未建立產品測試碼；開發後由 QA 依實際模組拆成單元、契約、整合、E2E、安全與負載套件。
- UI 契約 `ui-contract.md v0.5.1` 已由 architect 核准並與 SPEC/API v0.3.1 對齊；Console selector、元件與五態細節固定依該版本驗收。
- Q-008 已由 architect 收斂到 SPEC/API v0.3.1：trigger 表示 server-known ingress protocol；REST 與 Console 固定 `API`，FastMCP 固定 `MCP`，client body/header 不能改寫。

---

## 5. 驗證後補註（2026-09-22，@qa-verifier）

> §1–§4 是**開發前**寫的門檻，刻意不修改。這一節是執行結果。

### 5.1 執行環境（可重現）

```bash
docker run -d --name zentra-qa-registry -p 5010:5000 registry:2
docker run -d --name zentra-qa-pg-proxy    --network zentra-net -p 127.0.0.1:15432:15432 \
  alpine/socat tcp-listen:15432,fork,reuseaddr tcp-connect:postgres:5432
docker run -d --name zentra-qa-redis-proxy --network zentra-net -p 127.0.0.1:16379:16379 \
  alpine/socat tcp-listen:16379,fork,reuseaddr tcp-connect:redis:6379
PYTHONPATH=. python tests/zen008/fixtures/images.py          # build + push 8 個 fixture image

ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 \
ZENTRA_TEST_DB_URL=postgresql+asyncpg://zentra:zentra@127.0.0.1:15432/zentra \
ZENTRA_TEST_REDIS_URL=redis://127.0.0.1:16379/0 \
PYTHONPATH=. pytest tests/zen008 -q
```

主機原生 Postgres 佔住 `127.0.0.1:5432`，所以 DB/Redis 走 socat 代理。
Registry 與兩個代理都是**測試期間**的容器，跑完即刪（§1 的前提沒有被破壞）。

### 5.2 結果

| 套件 | 結果 |
|---|---|
| `tests/zen008` | **194 passed, 7 failed, 3 skipped, 1 xfailed** |
| `tests/zen000` | 23 passed, 1 skipped（skip：主機 venv 無 LangGraph） |
| `tests/zen006a` | 143 passed, 2 xfailed |

7 個 failed 對應 **4 個產品缺陷**（D1 佔 3 條、D2 佔 2 條、D3、D6 各 1 條），
逐條證據見 `coordination/handoffs/ZEN-008/qa-verifier-to-orchestrator.md`。

### 5.3 判定不通過的斷言

| ID | 斷言 | 結果 |
|---|---|---|
| `Z8-01` | list/detail 的 `next_cursor`、`description`、`latest_published_version` 逐欄位符合 v0.3.1 | ❌ D2：null 欄位被整個省略 |
| `Z8-23` | 只可寫 512 MiB `/workspace` tmpfs；容器銷毀後不殘留 | ❌ D1：image 宣告的 `VOLUME` 變成宿主機上的 writable 匿名 volume，銷毀後仍在 |
| `Z8-33` | user exception 收斂為契約枚舉中的 `FAILED` error | ❌ D3：回 `SANDBOX_ERROR`+`retryable=true`，`USER_CODE_ERROR` 從未使用 |
| `Z8-36` | 第 11 個 Run 回 `429 QUOTA_EXCEEDED` 與 `Retry-After` | ❌ D6：回 `202`，Run 之後才收成 `FAILED/QUOTA_EXCEEDED` |

### 5.4 未覆蓋（不得計入通過）

| 項目 | 原因 |
|---|---|
| gVisor／核心逃逸／沙盒與平台實體隔離 | ADR-0005 的已知取捨，`skipped`。**不是缺陷，也不是通過** |
| `Z8-34` 取消半邊、`Z8-35` `Idempotency-Key` | 端點屬 ZEN-004（BOARD `BACKLOG`），ZEN-008 未交付，`skipped` |
| `Z8-36` 十個真沙盒的負載 | Colima VM 只有 1.9 GiB，跑不動十個 langgraph 容器。只驗了配額決策路徑 |
| `Z8-19` 私有 Registry 認證 pull | 只驗了 credential store 單元行為與錯誤訊息遮蔽，**沒有**架私有 Registry 實跑 |
| `Z8-12` validation token TTL/跨版本越權 | token 明文只存在 worker 行程與容器 env，QA 取不到；只驗了「build 結束後 Redis 索引已清空」 |
| `Z8-22` 記憶體炸彈、鄰居存活 | 只驗了 fork bomb 與 cgroup 設定；VM 記憶體不足以安全地做記憶體炸彈 |
| Safari／Firefox、窄螢幕、螢幕報讀器、對比度 | 只用 Chrome 153、1440×1000 |
