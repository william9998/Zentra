# 交接單 Handoff

**任務**：`ZEN-006A` Console 靜態檔納入 api 映像
**從**：@platform-eng　**給**：@orchestrator
**日期**：2026-09-21
**Spec 版本**：`v0.2.2`

## 1. 我做了什麼
在 `docker/api.Dockerfile` 加一行 `COPY apps/console ./apps/console`，讓 api 映像在建置時把
Console 的原生 HTML/CSS/JS 一起打進去，對齊 ADR-0006「Console 由 api 服務靜態檔、零建置」。
`docker/worker.Dockerfile` 完全沒動。`compose.yaml` 完全沒動（判斷理由見 §4）。
沒有執行 `docker compose build`／`up`，因此**沒有驗證過映像實際建置成功**。

## 2. 產出的檔案
| 檔案 | 說明 |
|---|---|
| `docker/api.Dockerfile` | 在既有 `COPY apps/api ./apps/api` 之後新增 `COPY apps/console ./apps/console`，沿用現有「一行一個 COPY、依相依順序排列」的風格，放在最後一行（目前 Dockerfile 裡最後被 COPY 的就是它依賴的東西，console 邏輯上依賴 api 已存在，順序合理）。 |

## 3. 契約影響
- [x] 沒有影響對外契約
  （只動 Dockerfile 的 COPY 清單，不涉及 `api-contract.md` / `ui-contract.md` 定義的任何介面）

## 4. 下一個人要做什麼
1. 等 `@frontend-dev`（`apps/console/**`）與 `@backend-dev`（`apps/api/**` 的靜態檔服務路由）都交件後，
   由主 session 統一跑一次 `docker compose build api` 驗證：
   - `apps/console` 目錄此刻**尚不存在**（`ls apps/console` → No such file or directory），
     若在它建立之前就 build，`COPY apps/console ./apps/console` 會直接 build 失敗（COPY 來源不存在）。
     這是預期中的順序依賴，不是這次改動的問題，但主 session 排 build 順序時要注意。
2. build 成功後，建議至少跑一次 `docker compose up -d api` + `curl http://localhost:8000/console/agents`（或
   `api-contract.md` §0 定義的實際路徑）確認靜態檔真的能從容器內被 `api` 服務讀到、路徑對得上。
3. **compose.yaml 是否要為 Console 加 bind mount／熱重載** — 我判斷**不需要**，理由見 §6，
   如果你們決定要加，麻煩先開新的交接單或在 BOARD 記錄理由，不要默默加回來。

## 5. 我沒做的事（誠實列出）
- 沒有執行 `docker compose build` 或 `docker compose up`（任務明確禁止，留給主 session 三方交件後統一做）。
- 沒有驗證 `apps/api` 目前的靜態檔服務程式碼（Console 掛載路徑、`StaticFiles` 設定等）是否已經對齊
  `apps/console` 這個路徑——那是 `@backend-dev` 的範圍，我只確保「容器裡拿得到這個目錄」。
- 沒有動 `docker/worker.Dockerfile`（刻意，理由見 §6）。
- 沒有動 `compose.yaml`（刻意，理由見 §6）。
- 沒有檢查 `.dockerignore`（若存在且排除了 `apps/console`，COPY 仍會失敗）——目前 repo 內我沒看到
  `.dockerignore` 檔案，但這點值得主 session build 前再確認一次。

## 6. 已知限制與風險
| 項目 | 影響 | 建議 |
|---|---|---|
| worker 映像刻意不加 `apps/console` | worker 不服務靜態檔（那是 api 的職責，見 SPEC 目錄地圖：`apps/console` 由 `api` 服務），加了只會讓映像變胖、職責混淆，也不必要地擴大 worker 的攻擊面 | 維持現狀，不加 |
| `compose.yaml` 未改動 | 這個切片的目標只是「讓 api 映像包含 console 檔案」，不涉及本機開發熱重載體驗；沒有任何既有服務（api/worker）目前有 bind mount 自己的原始碼，加 console 的 bind mount 會是本切片唯一的例外，且會打破「本機與正式是同一套」的一致性（ADR-0005）；若要熱重載，應是獨立的 DX 決策，不該在這個小切片裡順手加 | 維持不改；如未來需要，另開任務討論 |
| build 順序依賴 `apps/console` 存在 | 若在 frontend-dev 交件前就對 api 映像跑 build，會直接失敗 | 主 session 排 build 順序時，確認 `apps/console/` 目錄（至少含 `runs.html` 等入口）已落地再 build |
| 映像內容未實測 | 我沒有驗證 COPY 後的檔案在容器內路徑、權限是否符合 api 服務靜態檔的預期（例如是否需要 `--chown`） | 主 session 統一 build 後跑一次容器內 `ls /srv/apps/console` 確認 |

## 7. 驗證狀態
| 檢查 | 結果 | 證據 |
|---|---|---|
| Dockerfile 語法／內容檢查 | ✅ 已用 `cat` 人工確認新增一行、其餘不變 | `git diff -- docker/ compose.yaml` 只顯示 `docker/api.Dockerfile` 多一行 `COPY apps/console ./apps/console` |
| worker.Dockerfile 未變動 | ✅ | 同上 diff，worker.Dockerfile 無變更 |
| compose.yaml 未變動 | ✅ | 同上 diff，compose.yaml 無變更 |
| `docker compose build` | 未跑（任務明確禁止） | — |
| `docker compose up` / 容器內驗證 | 未跑（任務明確禁止） | — |
| Console 靜態檔實際可被 api 服務讀到 | 未驗證（`apps/console` 目前不存在，也不在我的可寫範圍） | — |

> 測試沒跑就寫「未跑」：本次除了人工比對 diff 之外，沒有任何建置或執行層級的驗證。

## 8. 未決的 SPEC-QUESTION
- [x] 無
