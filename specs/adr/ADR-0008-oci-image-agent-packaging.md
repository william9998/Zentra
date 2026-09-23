# ADR-0008：Docker image 型 Agent 的發布格式與執行契約

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-22 |
| 決策者 | @architect（依產品負責人明確需求） |
| 相關 | [ADR-0001](ADR-0001-sandbox-isolation.md)、[ADR-0002](ADR-0002-celery-langgraph-boundary.md)、[ADR-0003](ADR-0003-agent-packaging-dual-interface.md)、[ADR-0005](ADR-0005-mvp-single-host-scope.md)；P1–P5 |

> ⚠️ **本 ADR 對「選項 B：Zentra 自帶 OCI Registry」的否決，已被
> [ADR-0009](ADR-0009-internal-registry-image-upload.md) 推翻（2026-09-22）。**
> 平台現在有一個**內部**的 OCI Registry。
> （ADR-0009 R1 用它承接「上傳 image tar」；**R2 撤回了上傳**，改為使用者在平台主機上直接
> `docker push localhost:5000/<repo>:<tag>`，再走**本 ADR 既有的** digest 註冊路徑。
> 也就是說 ADR-0008 的進場形狀在 R2 之後是**唯一**的進場形狀。）
> 下方「選項 B」的代價評估是**假設要做對外通用 Registry**的定價，ADR-0009 逐條重新定價後改判。
> **本 ADR 的其餘部分全部有效**：canonical digest 身份、`zentra.image/v1` 契約、
> PostgreSQL 只存 metadata、外部 Registry 註冊仍是正途、P1–P5 不變。

## 背景

原本的 MVP 發布路徑是上傳 Python `.tar.gz`，由平台安裝 per-version venv，再以共用 runtime image
啟動沙盒。產品負責人現在明確要求：開發者自行 build Docker image，把它註冊成 Agent 版本，之後仍由
REST／MCP 建立 Run，經 Celery 派工，一 Run 建立一個臨時容器，結束後銷毀。

ZEN-001 尚未實作，因此目前沒有已發布的 bundle 版本需要相容。這讓我們可以在實作前收斂成一條發布路徑，
避免同時維護 bundle 安裝與 OCI image 兩套 build、執行和驗收矩陣。

## 選項 A：API 直接接收 `docker save` tar

- 好處：使用者可以把檔案直接送到 Zentra VM，不需要外部 Registry。
- 代價：API 必須處理數 GB 串流、斷點續傳、磁碟配額與清理；worker 還要安全地 `docker load`。
  image tar 不是適合存進 PostgreSQL 的資料，若另存本機 volume，又形成第二套 artifact store。
- 風險：單機 API 容易被大檔塞滿；重送與部分上傳的冪等處理成本高。

## 選項 B：Zentra 自帶 OCI Registry

- 好處：使用者可直接 push 到平台自己的 Registry，操作直覺。
- 代價：長駐服務由 4 個增加為 5 個，還要處理 Registry 的 TLS、認證、垃圾回收、容量與備份。
- 風險：違反 ADR-0005 的單機降維原則，且 Registry 維運與 Agent 平台核心能力無直接關係。

## 選項 C：使用既有 OCI Registry，註冊 immutable digest（採用）

- 好處：沿用 Docker／OCI 標準分發機制；平台只保存 metadata，image 本體由 Registry 保存並拉到 VM 的
  Docker image cache。以 `repository@sha256:...` 執行，tag 被改動也不會改變已發布版本。
- 代價：使用者必須先有 Docker Hub、GHCR 或內部 Registry；私有 Registry 憑證需由 VM 維運者預先配置。
- 風險：Registry 在發布時不可用會讓版本驗證失敗；本機 image cache 被人工清除時需要重新拉取。

## 包裝路徑選項

### A. bundle 與 OCI image 長期共存

- 好處：開發者可選擇較方便的格式。
- 代價：兩套發布 pipeline、兩套 SandboxSpec、兩倍契約與安全測試；與「收斂 > 彈性」相反。

### B. OCI image 取代尚未實作的 bundle 路徑（採用）

- 好處：維持 ADR-0005 的單一主線；依賴由 image build 階段固定，平台不再執行 `uv pip install`。
- 代價：開發者必須理解 Docker build/push；簡單 Agent 也要先產生 image。

### C. 平台把 OCI image 轉回 bundle + venv

- 好處：執行端可以沿用原規劃。
- 代價：無法可靠地從任意 image 還原原始碼與依賴鎖，且會破壞使用者已測過的執行環境。

## 決定

採用「選項 C + 包裝路徑 B」：

1. 開發者先把 image push 到可由 Zentra VM 存取的 OCI Registry。
2. 註冊時只接受 canonical digest reference：`<registry>/<repository>@sha256:<64 hex>`；tag-only reference 拒絕。
3. PostgreSQL 只存 Agent、版本、canonical image reference、完整 digest、platform、runtime contract、
   manifest、entrypoints、狀態與錯誤；**不存 image blob，也不存 Registry 密碼**。
4. 私有 Registry 憑證只存在 worker 所在 VM 的 Docker credential store／compose secret，
   不回傳 API，也不注入驗證或 Run 沙盒。
5. `q.build` 負責 pull exact digest、inspect、啟動一次性 validation container；成功後版本才是 `PUBLISHED`。
6. Run 時以同一個 digest 建立容器，不使用 tag，不接受呼叫端覆寫 image、command、network、volume、user、capability 或資源限制。
7. OCI image 必須實作 `zentra.image/v1` runtime contract：
   - label `io.zentra.image-contract=zentra.image/v1`
   - 可執行檔 `/opt/zentra/bin/zentra-agent`
   - 接受 `validate` 與 `run` 兩種 mode
   - 能以 uid/gid `65534:65534`、唯讀 rootfs、只有 `/workspace` 可寫的條件執行
   - `run` 透過 Internal Gateway 的 bootstrap/events/result 契約工作，不在容器內開對外 HTTP port
8. 驗證容器拿到的是綁定單一 `agent_version_id`、最長 10 分鐘的 validation token；Run 容器仍只拿
   綁定 `run_id`、TTL 等於 timeout 的 run token。兩者都不是 Registry 或 LLM 長期憑證。
9. 外部 REST 與 MCP 契約不因 artifact 改變。兩者都先建立同一個 Run，後續仍是
   `q.run → runner-worker → Sandbox Manager → 一 Run 一盒 → 終態後 destroy()`。

這個決定取代 ADR-0003 中「bundle + uv.lock + image digest」的 bundle 打包部分；
ADR-0003 的 Manifest、未 compile `StateGraph`、單一 entrypoints 生成 REST/MCP 與 P2/P3 原則繼續有效。
它也取代 ADR-0005 中「共用 runtime image + per-version venv」的 artifact 實作；
4 個長駐服務、單機 compose 與沙盒安全邊界不變。

## 後果

### 正面

- 版本不可變性由 OCI manifest digest 直接保證；P5 不依賴可變 tag。
- 平台不再安裝使用者依賴，P1 的發布邊界更單純。
- 開發者本機測過的 image 與平台執行的 image 是同一個 digest。
- image 本體不進 PostgreSQL，資料庫備份不會被大型 blob 撐爆。

### 負面

- 這不是「把 image tar 上傳到 Zentra API」；使用者需要一個 Registry。
- image 可能很大，第一次註冊的 pull 時間不計入 Run 冷啟 P95；版本必須在 pull + validate 完成後才可呼叫。
- MVP 仍只跑信任的 image，沒有 CVE／license／簽章驗證。OCI digest 保證內容沒變，不代表內容安全。
- 單機 Docker cache 是本機狀態。VM 重建或 image 被人工 prune 後，要從 Registry 重新拉取；
  Registry 不可用時該版本暫時無法啟動。
- bundle 上傳退出 MVP；若未來要恢復，必須重新引入 artifact union 與完整雙路徑驗收，不能私下加回。

## 何時該重新檢討

- 確認部署環境完全不能使用任何 OCI Registry，且 direct upload 是硬需求：重評選項 A，先定大檔儲存與配額。
- 要讓 Zentra 成為完整 image hosting 產品：重評選項 B，並把 Registry 當正式長駐服務與備份範圍。
- 要跑第三方或不可信 image：先重讀 ADR-0001/0005，升級核心隔離、供應鏈簽章與掃描，不能只靠 digest。
- 簡單 Agent 的 Docker 門檻成為主要採用阻力：以新 ADR 評估恢復 bundle，但不得讓兩種路徑共用未驗證的隱式行為。
