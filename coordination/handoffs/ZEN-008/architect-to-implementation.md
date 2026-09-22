# ZEN-008 架構師交接給實作團隊

**任務**：`ZEN-008` OCI image 型 Agent 註冊、呼叫與一次性執行  
**從**：@architect　**給**：@backend-dev、@platform-eng、@frontend-dev；完成後給 @qa-verifier  
**日期**：2026-09-22  
**Spec 版本**：`v0.3.1`  
**API 契約**：`v0.3.1`　**UI 契約**：`v0.5.1`  
**狀態**：`SPEC_READY`

## 1. 開工依據

每個實作者先讀本交接，再讀 `AGENTS.md`、`specs/SPEC.md`、自己的角色檔，以及：

1. `specs/adr/ADR-0008-oci-image-agent-packaging.md`
2. `specs/features/ZEN-008/spec.md`
3. `specs/features/ZEN-008/acceptance.md`
4. `specs/api-contract.md`
5. 前端另讀 `specs/ui-contract.md` 與 `docs/design/oci-image-agent-console.md`
6. 平台另讀 ADR-0001、ADR-0005 與 `docs/architecture/oci-image-agent-flow.md`

不得把舊 bundle/venv、image tar upload、tag-only image reference、client-controlled trigger 或平台內建 Registry 加回來。

## 2. Architect 最終一致性裁定

- Image artifact 是 canonical `repository@sha256:...`；PostgreSQL 只存 metadata，不存 blob/credential。
- `q.build` pull/inspect/validate；`q.run` 一 Run 一 task、一 Run 一容器。
- Image 只實作 `/opt/zentra/bin/zentra-agent validate|run`，不自行暴露 REST/MCP port。
- REST 與 Console Run 的 server-derived `trigger=API`；FastMCP `trigger=MCP`。
  Body `trigger` 回 `422`，header 不得改寫。
- UI 不收 Registry credential、不提供 Docker build、不新增第六頁。
- validation/run token 與 Registry credential 是三個分離的信任邊界。
- UI contract、API contract、feature spec 與 acceptance 無未解 ZEN-008 SPEC-QUESTION，可開工。

## 3. 可平行實作範圍

三個角色的寫入目錄不重疊，可以在契約固定後平行。所有人都不是唯一工作者，不得還原他人修改；
遇到跨界需求以 handoff 協調，不可直接寫別人的目錄。

### @backend-dev

**可寫**：`apps/api/**`、`apps/worker/**`、`packages/sdk/**`

交付：

1. `agents` / `agent_versions` models、Alembic migration、唯一性與 OCI digest validation。
2. Agents/Versions API v0.3.1、strict request schema、`VERSION_CONFLICT` 與版本輪詢 shape。
3. `q.build(agent_version_id)` claim/冪等與版本終態；task authority 只傳 DB id，不信任 Celery payload 的 image ref。
4. Validation token、`/internal/versions/{id}/validation`、payload 冪等與 worker 唯一發布權。
5. Run 解析 PUBLISHED `agent_version_id/image_ref`，REST/Console=`API`、MCP=`MCP`，不得接受 discriminator。
6. Interface Generator、Agent OpenAPI、FastMCP `tools/list/tools/call` 與 schema 同源。
7. `zentra.image/v1` runtime/SDK executable：validate/run、bootstrap/events/result；worker/API 行程不可 import image code。

完成後寫：`coordination/handoffs/ZEN-008/backend-dev-to-qa-verifier.md`。

### @platform-eng

**可寫**：`apps/sandbox/**`、`docker/**`、`compose.yaml`

交付：

1. Docker pull/inspect exact RepoDigest；不得 resolve tag 或 fallback 到其他 local image。
2. OCI validation/run 的 `SandboxSpec` 與 Docker driver：固定 executable/mode、non-root、read-only、tmpfs、internal network、資源限制與 labels。
3. Registry credential 只在平台側 Docker credential store/compose secret；不得進 DB、Celery payload、SandboxSpec env/mount/log。
4. validation container 與 Run container 的 timeout、冪等 destroy、cache miss exact-digest re-pull 與故障 mapping 所需結果。
5. `capabilities()` 誠實維持 `l2_kernel_isolation=false`；不得宣稱 gVisor/核心逃逸防護。

`apps/sandbox` 對 backend 暴露的介面必須以 resource id + DB 已解析的 canonical image ref 為輸入；
平台端回傳 inspect metadata、container id、exit/timed_out/error，不直接寫 PostgreSQL 狀態。

完成後寫：`coordination/handoffs/ZEN-008/platform-eng-to-qa-verifier.md`。

### @frontend-dev

**可寫**：`apps/console/**`

交付：

1. `api.js` 的 Agent/Version/Run wrappers 與 view-model normalization；原始 snake_case 集中在契約層。
2. 五頁內完成 Agent metadata dialog、Agent list/version list、OCI registration、Run composer、endpoints/recent runs。
3. Version registration 只送 JSON `semver + image_ref`；移除 `.tar.gz`、FormData、拖放與 credential UI。
4. Run body 只送 `version/entrypoint/input`，不得送 `trigger` 或 `X-Zentra-Trigger`。
5. PUBLISHED/BUILDING/FAILED、API error、五態、鍵盤/focus、destroy 時清 interval/EventSource，全部依 UI v0.5.1。

完成後寫：`coordination/handoffs/ZEN-008/frontend-dev-to-qa-verifier.md`。

## 4. Backend / Platform 固定邊界

為避免兩邊各自猜 Docker 行為：

- backend 擁有版本/Run 狀態機、token、Celery task、DB transaction、Gateway 與錯誤映射。
- platform 擁有 pull/inspect/create/wait/destroy 與容器 hardening。
- backend 只能傳 DB 已解析的 canonical digest reference 與平台允許的 mode/resource limits；
  不得轉送 request 中的 command/network/volume/user/capability。
- platform 不自行把版本設成 `PUBLISHED`、不自行把 Run 設終態，也不直接發 `run.status`。
- driver 回報事實；worker 依 API contract 與 ADR-0007 唯一決定終態。

若現有 `SandboxDriver` 介面不足，由 @platform-eng 提案、@backend-dev 對照使用；影響契約或 P1–P5 時停下並開 SPEC-QUESTION。

## 5. 驗證與交棒

- 開發階段可寫責任區內必要單元測試；正式驗收測試與 `acceptance.md` 仍由 @qa-verifier 擁有。
- 三方完成後由主 session 整合，狀態進 `REVIEW`，再交 QA 執行 Z8-01～Z8-36。
- 真 Registry/Docker/PostgreSQL/Redis/Celery 與 Playwright 證據不能由 fake driver 或 mock response 取代。
- gVisor、核心逃逸、實體隔離保持 skipped/未覆蓋；本產品仍只允許信任來源 image。
- QA 通過後仍須 @spec-keeper 做 drift audit；只有 QA 能把 BOARD 推進 `DONE`。

## 6. 未決問題

- ZEN-008 無未解 SPEC-QUESTION。BOARD Q-008 已在 v0.3.1 收斂。
- BOARD 既有 Q-002～Q-007 屬 ZEN-006A/UI 通用議題；若實作觸及，依各自裁決處理，不能在 ZEN-008 私自猜測。
