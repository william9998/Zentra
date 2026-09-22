# ZEN-008 平台／沙盒交接給 QA

**任務**：`ZEN-008` OCI image 型 Agent 註冊、呼叫與一次性執行  
**從**：@platform-eng　**給**：@qa-verifier  
**日期**：2026-09-22  
**Spec 版本**：`v0.3.1`

## 1. 我做了什麼

平台端已把 `SandboxDriver` 擴成 exact-digest OCI 流程：只接受 canonical
`repository@sha256:...`，cache miss 只重拉同一 digest，inspect 必須確認完整 RepoDigest、host platform 與
`io.zentra.image-contract=zentra.image/v1`。validation/run 使用不同 `SandboxSpec` factory、固定
`/opt/zentra/bin/zentra-agent validate|run`、固定三個 mode env、resource labels 與相同 Docker hardening。
`SandboxManager` 對 backend 提供 prepare/inspect/create/wait/destroy 介面，也提供必定 destroy 的 lifecycle helper；
平台只回報 image/container/exit 事實，不寫 PostgreSQL 狀態。

Registry credential 由 worker 的 read-only compose secret 讀取，只在 daemon pull 的 `auth_config` 存活；
不進 `SandboxSpec`、container env/mount、Celery payload或 log。pull error 會遮蔽實際 password/token。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `apps/sandbox/drivers/base.py` | canonical digest parser、OCI mode/spec、image metadata、stage error 與 driver protocol |
| `apps/sandbox/drivers/docker_driver.py` | exact pull/inspect/cache recovery、credential store、固定啟動與 Docker hardening |
| `apps/sandbox/drivers/__init__.py` | 匯出後端需用的平台型別 |
| `apps/sandbox/manager.py` | `prepare_image`、`inspect_image`、`create_validation`、`create_run` 與 lifecycle helper |
| `compose.yaml` | worker-only Registry auth secret；長駐服務仍為原四個 |
| `docker/registry-auth.empty.json` | 公開 Registry 的空白 Docker auths 預設；正式環境以 compose host 檔案覆寫 |

## 3. 契約影響

- [x] 沒有改變 REST／MCP／UI 對外契約；實作依 `api-contract.md v0.3.1`。
- 平台內部介面如下：
  - `prepare_image(image_ref) -> ImageMetadata`
  - `inspect_image(image_ref) -> ImageMetadata`
  - `create_validation(agent_version_id, validation_token, image_ref) -> SandboxHandle`
  - `create_run(run_id, run_token, image_ref) -> SandboxHandle`
  - `driver.wait(handle, timeout) -> SandboxExit`、`driver.destroy(handle)`
- 錯誤 stage：`ImagePullError=PULL`、`ImageInspectError=INSPECT`；validation create/start 契約失敗為
  `SandboxCreateError(stage=INSPECT)`，Run create/start 為 `stage=SANDBOX`。

## 4. 下一個人要做什麼

1. 依 `acceptance.md` 用真 Docker daemon + 本機 Registry fixtures 驗 Z8-06～Z8-24、Z8-31、Z8-33～36；
   fake client 只能算單元證據。
2. inspect 真 container，確認 Docker SDK 將 `volumes={}`/`ports={}` 序列化為非 null 空設定，image 宣告的
   VOLUME／EXPOSE／HEALTHCHECK 不會取得控制權，唯一 writable mount 是 `/workspace` tmpfs。
3. 驗證 cache prune → exact digest re-pull、Registry unavailable 不 fallback、credential 不出現在 inspect/log/env。
4. 逐路徑確認 validation/run 成功、失敗、timeout、cancel/retry 後 label query 與 inspect 都沒有殘留容器。
5. 整合驗證 backend 在 validation 成功後、PUBLISHED 前再 `inspect_image` 比對 digest；Run platform error 映射為
   `SANDBOX_ERROR`，不能落成 `INTERNAL_ERROR`。

## 5. 我沒做的事

- 沒有真 Registry/Docker 整合證據；本機 Colima socket 在 sandbox 內被拒絕，升權測試未獲核准即中斷。
- 沒有修改 QA 的 `tests/**`；正式 fixtures、攻擊探針與安全回歸由 @qa-verifier 擁有。
- 沒有做 gVisor、核心逃逸、實體主機隔離、image 簽章或 CVE 掃描；這些依 ADR-0005/0008 不在 MVP。
- L5 SDK 攔截層不是 driver 能保證的能力，`capabilities().l5_sdk_interception=false`。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| 無 gVisor／實體隔離 | Docker 共用宿主核心，不防核心逃逸 | 僅允許信任來源 image；`l2_kernel_isolation=false` |
| worker 掛 docker.sock | worker 權限等同 host root | 維持只有 worker 掛載；威脅模型改變時重評 k3s/rootless/proxy |
| compose secret 是 Docker config `auths` | 未整合外部 credential-helper binary | 私有 Registry 使用 compose secret 的 auth/identitytoken；QA 驗證目標部署格式 |
| 真 Docker 尚未驗證 image VOLUME merge | 若 SDK/daemon 把 image VOLUME 合併回來，可能多出 writable volume | Z8-23 必須 inspect Mounts；失敗不得放行，需改用能明確送空 Config.Volumes 的低階 API |
| 舊 ZEN-000 compatibility path | 仍可用固定 tagged built-in runtime；只有 DB 發布版本走 OCI exact digest | QA 分開辨識舊骨架與 ZEN-008 主線，不能把 legacy 當 OCI 通過證據 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| canonical parser / mode env | ✅ | 直接 assertion：合法 digest；tag/tag+digest/non-sha256 拒絕；validate/run 各只有契約三個 env |
| Docker create config 單元 | ✅ | fake client assertion：固定 entrypoint/mode、non-root、cap_drop、read-only、空 volumes/ports、唯一 `/workspace` tmpfs、無 docker.sock |
| Python 語法 | ✅ | `python -m py_compile apps/sandbox/drivers/{base,docker_driver}.py apps/sandbox/manager.py` |
| Compose 結構 | ✅ | `docker compose config --quiet`；仍為 postgres/redis/api/worker 四服務，secret 只掛 worker |
| Diff whitespace | ✅ | `git diff --check` |
| 現有 driver pytest | 未跑 | 主機 Python 無 Docker SDK而 skipped；容器執行需要 Colima socket權限，核准等待中斷 |
| 真 Docker/Registry/四層攻擊驗證 | 未跑 | 必須由 QA 在可用 daemon 上依 Z8 acceptance 執行，未宣稱通過 |

隔離現況：L1 internal network、L2 Docker hardening、L3 cgroup/log/wall-clock controls、L4 read-only rootfs +
workspace tmpfs 已寫入 create config，但尚無真容器驗收證據。`capabilities()` 回報
`l1=true, l2_kernel_isolation=false, l3=true, l4=true, l5=false`。

不得把上述靜態／fake 證據寫成「沙盒安全已通過」。正確敘述是：Docker 四層控制已實作、尚待真容器逐項驗證；
gVisor／核心逃逸與實體主機隔離未覆蓋，本架構不防此類攻擊，只允許信任來源 image。

## 8. 未決的 SPEC-QUESTION

- [x] 無。若 QA 證明 Docker SDK 無法阻止 image 宣告 VOLUME 建立匿名 writable volume，這會阻擋 Z8-23，
  應退回 @platform-eng 修 driver；不需要放寬 SPEC。
