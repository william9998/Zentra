# 交接單 Handoff — ZEN-009（開發前驗收條件）

**任務**：`ZEN-009` 內部 Registry + 同機 `docker push` 發布路徑
**從**：@qa-verifier　**給**：主 session（orchestrator）→ @platform-eng / @backend-dev / @ui-designer / @frontend-dev
**日期**：2026-09-23
**依據**：`SPEC.md v0.4.1` · `api-contract.md v0.4.1` · [ADR-0009 R2](../../../specs/adr/ADR-0009-internal-registry-image-upload.md) · ZEN-009 feature spec（第二版）

## 0. 裁定

**ZEN-009 的開工閘門已開。** 驗收條件寫在
[`specs/features/ZEN-009/acceptance.md`](../../../specs/features/ZEN-009/acceptance.md)。

**這一輪沒有驗收任何東西。** ZEN-009 的實作尚未開始，本文件不宣稱任何 ZEN-009 項目通過。
任務狀態維持 `SPEC_READY`。

## 1. 產出

| 檔案 | 內容 |
|---|---|
| `specs/features/ZEN-009/acceptance.md` | 14 節。§0 環境前置 · §2 範圍上限 · §3 compose 拓撲 · §4 P5 可重現性 · §5 P4 隔離 · §6 平台不得代理 Registry · §7 撤回項反向斷言 · §8 `image_ref` 正規化護欄 · §9 GC 禁令與維運說明 · §10 Q-008/009/010 · **§11 ZEN-008 的 137 skipped 評估** · §12 明確不覆蓋 · §13 重跑方式 · §14 骨架現況 |
| `tests/zen009/conftest.py` | AirPlay-proof 的 registry 探測、compose YAML 解析、原始碼檢索 |
| `tests/zen009/test_compose_topology.py` | §3 的靜態閘門（**現在 6 紅，那是設計**） |
| `tests/zen009/test_withdrawn_and_no_proxy.py` | §6 §7 §9 的靜態護欄（現在全綠） |
| `tests/zen009/test_image_ref_normalization.py` | §8 的單元護欄 |

```
$ PYTHONPATH=. /private/tmp/zentra-backend-venv/bin/pytest tests/zen009 -q
6 failed, 32 passed in 0.23s
```

6 紅全部是同一個原因：`compose.yaml` 還沒有 `registry` 服務。失敗訊息直接寫出該怎麼改。

## 2. ⚠️ @platform-eng 開工前必讀：這台機器的 port 5000 現在是 AirPlay 的

**實測，不是推論**：

```
$ lsof -nP -iTCP:5000 -sTCP:LISTEN
ControlCe 720 william 13u IPv4 ... TCP *:5000 (LISTEN)
ControlCe 720 william 14u IPv6 ... TCP *:5000 (LISTEN)

$ curl -si http://127.0.0.1:5000/v2/ | head -3
HTTP/1.1 403 Forbidden
Content-Length: 0
Server: AirTunes/935.7.1
```

它綁的是 `*`（不是 loopback），所以 **compose 的 `127.0.0.1:5000:5000` 會 bind 失敗**。
spec §5.1 已裁定處置是教使用者關掉 AirPlay Receiver，不是把 port 做成設定項 ——
這裡只是證實那個「已知環境衝突」在驗收機上**現在就是真的**。

**它還會污染兩類測試，acceptance §0 已寫成硬規則**：

1. 「registry 活著嗎」的探測**不得只看連得上或 HTTP 有回應** ——
   AirPlay 會回 403，任何寬鬆探測都被騙過。唯一可接受的判定是
   `GET /v2/` 回 `200` **且** header 含 `Docker-Distribution-Api-Version: registry/2.0`。
2. 「非 loopback 位址連不到 5000」**不得斷言 connection refused** ——
   實測 `curl http://192.168.0.101:5000/v2/` 一樣回 403。
   正確斷言是「得不到 Zentra Registry」＋`docker inspect` 的 `HostIp == 127.0.0.1`，兩條合看。

`tests/zen009/conftest.py` 的 `require_product_registry()` 已經照這個寫：
撞到 AirPlay 時它 **`fail` 而不是 `skip`**，並指出關掉的位置。

## 3. 對 137 個 ZEN-008 skipped 的評估（你特別要的那題）

### 3.1 實測拆解 —— 數字對，但「卡在同一個前置條件」不對

```
$ PYTHONPATH=. ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 pytest tests/zen008 -q -rs
2 failed, 66 passed, 137 skipped in 2.08s
```

| 卡在什麼 | 條數 | registry 進 compose 後會解封嗎 |
|---|---|---|
| `require_registry()`（需要本機測試 Registry） | **112** | **不會自動解封**，見 §3.2 |
| `ZENTRA_TEST_DB_URL`（socat 橋接容器 Postgres） | 21 | 不會。與 registry 無關 |
| `ZENTRA_TEST_REDIS_URL` | 1 | 不會 |
| **永久不會解封**：ZEN-004 `cancel`、`Idempotency-Key`、ADR-0005 gVisor | 3 | **永遠不會。這 3 條不准被算進「可解封」** |

那 `2 failed` 是 BOARD 已登記的兩個 ZEN-008 契約缺陷，與 registry 無關：

```
test_agent_list_always_carries_next_cursor_key
  AssertionError: 最後一頁少了 next_cursor：keys=['items']
test_agent_summary_keeps_nullable_contract_fields
  AssertionError: GET /v1/agents/{id} 少了 ['description', 'latest_published_version']
                  keys=['agent_id','created_at','endpoints','name','updated_at']
```

### 3.2 為什麼「registry 進 compose」不會自動解封那 112 條

`tests/zen008/conftest.py` 的 `require_registry()` 用的是**容器名稱**：

```python
["docker", "inspect", "-f", "{{.State.Running}}", images.REGISTRY_CONTAINER]  # "zentra-qa-registry"
```

compose 起的容器叫 `zentra-registry-1`，名字對不上 → 112 條照樣 skip。
需要兩處改動（都在 `tests/**`，是我的可寫範圍，但**本輪刻意沒動**，理由見 §3.3）：

1. `require_registry()` 改成 HTTP 探測（`/v2/` → 200 + `Docker-Distribution-Api-Version`）。
   **不可以只看連得上** —— 見 §2。可直接用 `tests/zen009/conftest.py` 的 `registry_probe()`。
2. 執行時設 `ZENTRA_TEST_REGISTRY`（`images.REGISTRY` 本來就吃這個 env）。

### 3.3 我的建議：**併「跑」，不併「判」；而且 ZEN-008 不要指向產品 registry**

**不建議把 ZEN-008 的 fixture 推進 `localhost:5000`**，三個理由：

1. **產品 registry 是 `delete.enabled=false` 且沒有 GC。** ZEN-008 的 8 個 fixture repository
   推進去就是**永久、刪不掉**的垃圾，躺在 `registrydata` 裡 —— 而 ADR-0009 R2 才剛把
   `registrydata` 提升成與 `pgdata` 同等的備份範圍。要清掉得走
   「停機 → 暫時開 delete → `garbage-collect` → 關回去」的完整維運程序。
   **拿一次測試環境的方便，換一個生產資料卷的永久污染，不划算。**
2. **ZEN-008 自己的驗收條件禁止這件事。** 它的 §1 明寫「OCI 測試使用測試期間啟動的本機
   Registry fixture，**它不是產品 compose 的第五個長駐服務**」。改指向產品 registry
   等於偷偷改掉一份已簽核的驗收條件 —— 那是規格變更，不是 QA 的方便。
3. **ZEN-008 有一條測試是故意用同一個 tag 推第二個不同內容的 image**
   （`valid_v2` 與 `valid_v1` 共用 `zentra-qa/agent:stable`，驗 Z8-07 digest 不隨 tag）。
   那會在產品 registry 裡**刻意製造 untagged manifest** —— 正是 `--delete-untagged`
   禁令（acceptance §9 `Z9-G1`）在講的危險狀態。在生產儲存裡故意製造它，
   是把測試的副作用留給維運者收。

**建議的做法**：

- ZEN-008 繼續用**可丟棄**的 registry（現有的 `zentra-qa-registry:5010` 就可以）。
  **所以先不要清掉它** —— 等 §3.2 的 conftest 改動與 ZEN-008 重跑策略定案再說。
  只把 `require_registry()` 換成 HTTP 探測，112 條立刻可跑，零污染。
- ZEN-009 的主線**必須**打產品 registry（`localhost:5000`），但只用
  `zentra-zen009-accept/*` 這一組 repository 名，驗收交接單要明列
  「在 `registrydata` 留下了哪幾個 repository」，讓維運者決定要不要走 GC 程序。
- **同一輪跑、分開裁定**：環境（Docker、socat、base_url、registry）搭一次兩邊共用，
  省掉重複成本；但兩個 `DONE` 各自獨立。
  **綁在一起有兩種壞結果**：ZEN-009 被 ZEN-008 的舊缺陷擋住，
  或 ZEN-008 的缺陷搭 ZEN-009 的順風車矇混過關。
- 那 3 條永久 skip 在任何報告裡都要維持 `skipped` 並註明理由，
  不准因為「數字比較好看」被算成解封。

## 4. 我注意到、但不屬我處理的事

1. **工作區有一份未 commit 的 `apps/sandbox/drivers/docker_driver.py` 變更**
   （`_split_registry` / `_normalized_repository` / `_comparison_key`，
   `_inspect_object` 改成正規化 key 比對）。那是 @platform-eng 的 Docker Hub 平行任務。
   我的 `tests/zen009/test_image_ref_normalization.py` 三條護欄已對它生效並通過 ——
   **但那是平行任務的成果，不得計入 ZEN-009 的通過項**。
   測試寫成了「函式不存在就 skip」，所以該變更被回退時 `Z9-F4` 會自動回到 `skipped`，
   而 `Z9-F1` 的否定案例在兩種狀態下都必須綠。
2. **我沒有重跑你給的那段 push → 註冊 → PUBLISHED → Run 實測。**
   compose registry 還沒上線，`localhost:5000` 現在是 AirPlay。
   我改為驗證那條路徑的**前提**（純單元，零成本、可重現）：
   `parse_image_ref("localhost:5000/my-agent@sha256:<64hex>")` 通過且不改寫字串；
   `_registry_for("localhost:5000/x") == "localhost:5000"`。
   完整實測是 acceptance §4（`Z9-B1`–`Z9-B13`）的事，要等實作。
3. **ZEN-008 的兩個契約缺陷仍未修**（`next_cursor`、null 欄位），我原樣複述，不重新開單。

## 5. 我沒做的事（誠實列出）

- **沒有驗收任何 ZEN-009 項目。** §4 §5 §9 §10 一條都沒跑過。
- **沒有改 `coordination/BOARD.md`**（紀律）。需要整合的內容見 §6。
- **沒有改任何產品程式碼**，沒有改 `compose.yaml`。
- **沒有改 `tests/zen008/**`** —— §3.2 的 `require_registry()` 改動是**提案**，
  我不想在 ZEN-008 的裁定還沒收斂時去動它的 conftest。要我動請說一聲。
- 沒有清掉 `zentra-qa-registry`（`:5010`）—— 理由見 §3.3，它現在還有用。
- 沒有開 socat 代理（本輪不需要 DB 斷言）；驗收時要開，用完刪。
- 沒有寫 §4–§10 的活服務測試程式碼。骨架只涵蓋靜態與單元那一半，§14 已誠實列出缺口。

## 6. 要請主 session 整合進 BOARD 的內容

1. ZEN-009 的驗收條件已就位（`specs/features/ZEN-009/acceptance.md`），**開工閘門已開**，
   @platform-eng 可以動 `compose.yaml` 了。
2. **環境前置**：驗收機的 port 5000 目前被 macOS AirPlay Receiver 佔用（`*:5000`），
   `registry` 服務起不來。這是 spec §5.1 已預期的衝突，處置是關掉它。
3. **ZEN-008 的 137 skipped 拆解**：112 registry-gated（需改 conftest 的探測方式才會解封）、
   21 DB-gated、1 Redis-gated、**3 條永久不可解封**。
   建議「併跑不併判」，且 ZEN-008 繼續用可丟棄的 registry（理由見 §3.3）。
   → 這代表 `zentra-qa-registry:5010` **先不要清掉**。
4. ZEN-009 驗收會在 `registrydata` 留下 `zentra-zen009-accept/*` 的 repository，
   `delete.enabled=false` 下刪不掉。這是已知且被接受的代價，驗收報告會列出清單。
5. **如果有人提議把 registry 綁 `0.0.0.0` 或支援遠端 push，我會擋下來並開 `SPEC-QUESTION`**
   —— ADR-0009 R2-1 硬規則 4 明文要求「必須先解決 TLS 與認證」，
   不得以「先開 `0.0.0.0` 之後再補」的順序進行。
