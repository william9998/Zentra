# ZEN-008 缺陷修正交接：Docker Hub image 無法註冊

**任務**：修正 `RepoDigests` 字面比對導致 Docker Hub（含 `myuser/*`）一個都註冊不了
**從**：@platform-eng　**給**：@orchestrator（請轉 @qa-verifier / @backend-dev）
**日期**：2026-09-23
**對齊**：`specs/SPEC.md v0.4.1 §7.1`、`specs/features/ZEN-009/spec.md §5.6`、ADR-0009 R2-6

---

## 1. 改了什麼

**只動一個檔案**：`apps/sandbox/drivers/docker_driver.py`。沒有碰 `apps/api/**`、`apps/worker/**`、
`tests/**`、`specs/**`、`compose.yaml`、`docker/**`，也沒有做 ZEN-009 的 registry 服務。

| 位置 | 改動 |
|---|---|
| 新增 `_split_registry(repository)` | 把 repository 切成 `(registry_host, remainder)`。第一段**同時**滿足「後面有 `/`」且「含 `.` 或 `:` 或等於 `localhost`」才算 registry host |
| `_registry_for(repository)` | 改為呼叫 `_split_registry`，行為不變（見 §3 的唯一例外）。憑證查找規則**沒有**改 |
| 新增 `_normalized_repository(repository)` | **只有**預設 registry 會被正規化，其他 host 原樣回傳 |
| 新增 `_comparison_key(reference)` | 回傳 `正規化repository@digest`；不是 digest reference 就回 `None` |
| `_inspect_object()` 的比對 | 從 `if image_ref not in repo_digests:` 改成比對兩邊的 `_comparison_key`，並在錯誤訊息裡附上正規化後的形式與 daemon 實際回報的內容 |

### 護欄遵守情況（`specs/features/ZEN-009/spec.md §5.6`）

| 護欄 | 狀態 |
|---|---|
| 1. 正規化只發生在「比較時」 | ✅ `_comparison_key` 只在 `_inspect_object` 的比對裡使用，不回傳給呼叫端 |
| 2. 不得改寫存進 DB 的 `image_ref` | ✅ `ImageMetadata.image_ref` 仍是呼叫端註冊的原字串（實測見 §4-b、§4-e）；`repo_digests` 仍是 daemon 的原始回報值，沒有被正規化污染 |
| 3. 第一段含 `.`／`:`／等於 `localhost` → registry host 原樣保留 | ✅ `_normalized_repository` 對非預設 host 直接 `return repository` |
| 4. 不准把 `localhost:5000/x` 正規化成 `x` | ✅ 有正面與反面實測（§4-c、§4-d） |

另外：`version_id` 計算、`image_ref` 儲存方式、`SandboxSpec`、四層隔離參數**一行都沒動**。

---

## 2. 正規化規則的完整值域

**規則**（只有三步）：

1. `repository` 第一段後面有 `/`，且該段含 `.` 或 `:` 或等於 `localhost` → 它是 **registry host**。
2. registry host ∈ {`docker.io`, `index.docker.io`}（或沒有 host）→ 這是**預設 registry**：
   host 正規化成 `docker.io`，且剩下的路徑若**只有一段**就補上 `library/`。
3. 其他 host（`ghcr.io`、`localhost:5000`、`registry.example.com:5000`、`registry-1.docker.io`…）
   → **原樣回傳，一個字元都不動**。

| 輸入 repository | 比較用的正規化形式 | 分組 |
|---|---|---|
| `docker.io/library/alpine` | `docker.io/library/alpine` | A |
| `docker.io/alpine` | `docker.io/library/alpine` | A |
| `alpine` | `docker.io/library/alpine` | A |
| `index.docker.io/library/alpine` | `docker.io/library/alpine` | A |
| `docker.io/myuser/img` | `docker.io/myuser/img` | B |
| `myuser/img` | `docker.io/myuser/img` | B |
| `index.docker.io/myuser/img` | `docker.io/myuser/img` | B |
| `ghcr.io/owner/img` | `ghcr.io/owner/img` | 自成一組 |
| `localhost:5000/x` | `localhost:5000/x` | 自成一組（**不等於** `x`、也不等於 `docker.io/x`） |
| `localhost:5000/ns/x` | `localhost:5000/ns/x` | 自成一組 |
| `localhost/x` | `localhost/x` | 自成一組 |
| `registry-1.docker.io/library/alpine` | `registry-1.docker.io/library/alpine` | 自成一組（見下方註記） |
| `registry.example.com:5000/a/b` | `registry.example.com:5000/a/b` | 自成一組 |
| `localhost:5010/demo-echo` | `localhost:5010/demo-echo` | 自成一組 |

非 reference 的輸入一律回 `None`（不會誤判成相等）：`alpine:1.0`（無 `@`）、`alpine@`、`@sha256:ab`。

**兩個刻意的取捨，請 QA 知道**：

- `registry-1.docker.io` **不**視為預設 registry。理由：Docker 自己的 reference 正規化只把
  `index.docker.io` 當 legacy domain 折成 `docker.io`，`registry-1.docker.io` 是 pull 端點而不是
  reference domain，daemon 不會把它折掉。我跟著 daemon 的實際行為走，不自創等價關係。
- `repository` 沒有 `/` 時（例如 `localhost`、`alpine`）一律視為**沒有 registry host**。
  這也是 daemon 的行為（`localhost` 單獨一段會被當成 `docker.io/library/localhost`）。
  舊 `_registry_for("localhost")` 會回 `"localhost"`，現在回 `"https://index.docker.io/v1/"` —— 這是
  此次唯一的 `_registry_for` 行為變化，**輸入是不合法的 reference（沒有 `/` 不可能通過 API schema）**，
  且往正確方向靠。所有合法輸入的憑證查找結果不變（`tests/zen008/test_platform_unit.py` 的憑證測試全綠）。

---

## 3. 驗過哪些 case（指令與輸出）

四個服務在跑；內部 registry 由使用者手動起在 **5010** 埠。

### (a) 修正前的狀態（同一顆 image、同一個 reference，用未修補的 `zentra-worker:latest` image 重現）

```
$ docker run --rm -i -v /var/run/docker.sock:/var/run/docker.sock zentra-worker:latest python - <<'PY' ...
has _comparison_key: False
BEFORE ImageInspectError: Docker RepoDigests does not contain registered reference
  docker.io/library/alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
```

daemon 側事實：

```
$ docker image inspect alpine:3.20 --format '{{json .RepoDigests}}'
["alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"]
```

### (b) 修正後・driver 層（真 Docker daemon、真 RepoDigests）

以 `zentra-worker:latest` + 掛入修補後的 `apps/` 執行：

```
### 1. real Docker Hub image, registered as docker.io/library/...
   ImageInspectError: image label io.zentra.image-contract must be zentra.image/v1
   OK: digest comparison passed; stopped at the contract label instead
### 2. internal registry image (localhost:5010) must still inspect cleanly
   image_ref  : localhost:5010/demo-echo@sha256:5f177b80485c...e14b
   digest     : sha256:5f177b80485c...e14b
   repo_digests: ('localhost:5010/demo-echo@sha256:5f177b...', 'localhost:5010/my-own-agent@sha256:5f177b...',
                  'localhost:5010/uploaded/demo@sha256:5f177b...')
   platform   : linux/arm64/v8 | contract: zentra.image/v1
### 3. negative: localhost:5000/x must NOT match a Docker Hub RepoDigest
   OK rejected: Docker RepoDigests does not contain registered reference localhost:5000/alpine@sha256:d9d9…
   (compared as localhost:5000/alpine@sha256:d9d9…, reported ['docker.io/library/alpine@sha256:d9d9…'])
### 4. positive: same fake RepoDigests accepts all default-registry spellings
   OK accepted (stored verbatim): alpine@sha256:d9d9…
   OK accepted (stored verbatim): docker.io/alpine@sha256:d9d9…
   OK accepted (stored verbatim): docker.io/library/alpine@sha256:d9d9…
   OK accepted (stored verbatim): index.docker.io/library/alpine@sha256:d9d9…
### 5. negative: ghcr.io and other hosts stay verbatim
   OK rejected: ghcr.io/library/alpine@sha256:d9d9…
   OK rejected: registry-1.docker.io/library/alpine@sha256:d9d9…
   OK rejected: myuser/alpine@sha256:d9d9…
ALL DRIVER CHECKS PASSED
```

第 1 點就是這次修正的核心證據：**同一個 reference，錯誤從「RepoDigests 不含」變成「缺 image contract
label」** —— 比對已經通過，alpine 只是本來就不是 Zentra agent image。

### (c) 修正後・端到端 (a) Docker Hub canonical reference

```
$ curl -X POST http://localhost:8000/v1/agents/<id>/versions -H 'Content-Type: application/json' \
    -d '{"semver":"1.0.0","image_ref":"docker.io/library/alpine@sha256:d9e853e87e55…"}'
```
輪詢結果：
```json
{
  "version_id": "d9e853e87e55",
  "artifact": {"image_ref": "docker.io/library/alpine@sha256:d9e853e87e55…", "digest": "sha256:d9e853e87e55…"},
  "status": "FAILED",
  "error": {"code": "BUILD_FAILED", "details": {"stage": "INSPECT"},
            "message": "image label io.zentra.image-contract must be zentra.image/v1", "retryable": false}
}
```
`422` 沒有出現、`PULL` 沒有失敗、**`RepoDigests` 比對也沒有失敗**；停在 alpine 本來就該擋下的地方。
`artifact.image_ref` 原封不動（護欄 2）。

### (d) 修正後・端到端 (b) `localhost:5010/demo-echo` 仍然可用

```json
{
  "version_id": "5f177b80485c",
  "artifact": {"image_ref": "localhost:5010/demo-echo@sha256:5f177b80485c…",
               "platform": "linux/arm64/v8", "size_bytes": 219437944, "runtime_contract": "zentra.image/v1"},
  "status": "PUBLISHED",
  "entrypoints": [{"name": "default", ...}]
}
```

而且同一個版本可以實際執行（`POST /v1/agents/<id>/runs:invoke`）：

```json
{"status": "SUCCEEDED", "trigger": "API",
 "output": {"echo": "normalization fix smoke test", "question": "normalization fix smoke test"},
 "usage": {"llm_calls": 0, "cost_usd": "0"}}
```

### (e) daemon 是否接受完整 reference（`create` 路徑的殘餘風險）

```
$ docker image inspect docker.io/library/alpine@sha256:d9e853… --format '{{json .RepoDigests}}'
["alpine@sha256:d9e853e87e55…"]
$ docker create docker.io/library/alpine@sha256:d9e853… /bin/true   # → 成功，已刪除
```
表示 `images.get()` 與 `containers.create(image=<註冊字串>)` 都吃得下 `docker.io/` 前綴，
`_create()` 不需要任何改動。

### (f) 既有單元測試回歸（**我只執行，沒有寫任何 `tests/**`**）

```
$ docker run --rm -v <repo>:/repo:ro -w /repo zentra-worker:latest \
    sh -c "pip install -q pytest pytest-asyncio; python -m pytest tests/zen000/test_driver_unit.py tests/zen008/test_platform_unit.py -q"
........................................                                 [100%]
40 passed in 0.39s
# 確認匯入的是修補後的檔案：
/repo/apps/sandbox/drivers/docker_driver.py | patched: True
```

### (g) 平行 QA session 剛寫好的 ZEN-009 正規化護欄測試（同樣只執行，沒有修改）

```
$ ... python -m pytest tests/zen009/test_image_ref_normalization.py -q
..................                                                       [100%]
18 passed in 0.40s
```

裡面包含 `Z9-F1`（`localhost:5000/alpine` ≠ `alpine` ≠ `docker.io/alpine` ≠ `docker.io/library/alpine`）、
`Z9-F4`（`docker.io/library/alpine` ≡ `docker.io/alpine` ≡ `alpine` ≡ `library/alpine`）與
`Z9-F6`（憑證 key 與比較用正規化是兩個函式）。
`acceptance.md` 把 `Z9-F4` 標成「在 Docker Hub 缺陷修好前必須 `skipped`」——
**這個前置條件現在解除了，Z9-F4 可以改判 `passed`**（請 QA 自行覆核，不是我說了算）。

---

## 4. 哪些沒驗（誠實清單）

1. **沒有跑完「Docker Hub 上的合格 agent image → `PUBLISHED` → Run」整條路**。
   需要把帶 `io.zentra.image-contract` label 的 image push 到 Docker Hub，我沒有（也不會自行使用）
   使用者的 Hub 憑證。替代證據是 §3(b)-1 / §3(c)（比對已過、停在 label）與 §3(e)（daemon 接受該字串）。
   **要完全關掉這個風險，需要有人用自己的 Hub 帳號推一顆 `zentra.image/v1` image 再註冊一次。**
2. **沒有驗私有 Docker Hub image（要憑證那種）** —— 見 §5 的已知缺口 1。
3. **沒有驗 `myuser/img@sha256:…`（不帶 host 的短形式）端到端**。API schema 允許（有 `/`），
   比對端也已支援（正規化成 `docker.io/myuser/img`），但同樣缺一顆 Hub 上的合格 image。
4. **沒有驗 `alpine@sha256:…` 這種官方短形式** —— 它在 `apps/api/schemas.py` 就被擋（§5 的問題 2）。
5. **沒有跑 ZEN-008/ZEN-009 的完整驗收套件**（`tests/` 是 QA 的區域，而且有平行的 QA session 在跑，
   我避免互相干擾）。我只跑了 §3(f) 那兩支純單元測試。
6. **沒有驗四層隔離**（這次沒碰隔離參數，`capabilities()` 也一行沒動：
   `l1=True / l2=False / l3=True / l4=True / l5=False`）。

### 環境狀態（請接手的人注意）

- `docker compose build worker && docker compose up -d worker` **已經跑完**，
  目前的 `zentra-worker-1` 是從重建後的 image 起來的：
  ```
  $ docker exec zentra-worker-1 python -c "import apps.sandbox.drivers.docker_driver as m; print(m.__file__, hasattr(m,'_comparison_key'))"
  /srv/apps/sandbox/drivers/docker_driver.py True
  ```
  §3(c)/(d) 的端到端結果在 image 重建**之後**又完整重跑過一次，結論相同
  （Hub → `FAILED/INSPECT/label`、`localhost:5010` → `PUBLISHED`）。
  四個服務都還在跑，`api`/`postgres`/`redis` 沒有重啟過。
- 驗證過程在 DB 留下四個測試 agent：兩個 `dockerhubfix-hub-*`（各一個 FAILED 版本）與兩個
  `dockerhubfix-local-*`（PUBLISHED 1.0.0，其中一個還有一次 SUCCEEDED 的 Run）。要清掉可以直接刪 agent。

---

## 5. 轉給 @backend-dev / @architect 的兩件事（我沒有動，因為不是我的區域／不在本次範圍）

1. **`_registry_for()` 對 `docker.io/` 前綴找不到 Docker Hub 憑證**（在我的區域，但我刻意不改，避免擴張範圍）。
   `registry_for("docker.io/acme/private")` 回 `"docker.io"`，而標準 `config.json` 把 Hub 憑證存在
   `https://index.docker.io/v1/`，兩者對不上 → **私有 Docker Hub image 會在 `PULL` 階段失敗**。
   公開 image 不受影響（不帶 auth 也能拉），所以這次的驗證不受影響。
   建議另開一張小票：把 `docker.io` / `index.docker.io` 也納入既有的 Hub alias 清單。
   這會動到憑證解析，應該和 QA 的憑證測試一起改，不適合塞進這次的比對修正。
2. **短形式 `alpine@sha256:…` 目前被 `apps/api/schemas.py` 擋掉**
   （`parse_image_ref` 要求 repository 含 `/` → `422 image_ref must contain a canonical lowercase repository`）。
   比對端現在**已經**支援這個形式，所以要不要放寬純粹是 API 契約決定，不是實作限制。
   我的建議：**維持現狀不放寬**。理由是 §5.6 護欄 4 只允許「放寬後仍拒絕 tag-only 與非 64 hex」，
   而 `docker.io/library/alpine@sha256:…` 這條合法 canonical 路徑現在已經會通，使用者沒有被擋住；
   多一種等價寫法只會讓 DB 裡出現三種指向同一顆 image 的字串。要改請 @architect 裁決後由 @backend-dev 動手。

## 6. 給 @qa-verifier 的回歸建議（測試檔我沒寫，這是清單）

- 正面：`docker.io/library/<img>@sha256:…`、`docker.io/<img>@sha256:…`、`index.docker.io/library/<img>@sha256:…`、
  `<user>/<img>@sha256:…` 都必須能通過 `RepoDigests` 比對（可用 fake image attrs 做純單元測試，
  RepoDigests 給 `["<img>@sha256:…"]`）。
- **反面（§5.6 護欄 4，必測）**：`localhost:5000/alpine@sha256:X` 對上 RepoDigests `["alpine@sha256:X"]`
  **必須** `ImageInspectError`。同樣要測 `ghcr.io/library/alpine@sha256:X` 與
  `registry-1.docker.io/library/alpine@sha256:X` 也被拒。
- 端到端：`localhost:<內部registry>/…` 這條路徑的 `PUBLISHED` + Run 必須持續綠燈（ZEN-009 依賴它）。
- 不變式：任何路徑下 `artifact.image_ref` 都等於註冊時送出的字串（逐字元相等）。
