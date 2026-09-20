# ZEN-000 開發前 QA 交接

**任務**：`ZEN-000` 貫穿骨架
**從**：@qa-verifier　**給**：@orchestrator
**日期**：2026-09-21
**Spec 版本**：`v0.2.2`（API 契約 `v0.2.2`；UI 契約 `v0.4.0`）

## 1. 我做了什麼

在開發前定義 19 項可自動化的驗收條件，分別覆蓋四個架構假設 A1–A4、P1、Celery 重送與併發冪等、一 Run 一容器、最小 L1 網路正反測試、三條 Internal Gateway 路由與 token A→B 越權、事件部分提交重試、公開 API 欄位及 `capabilities()` 誠實性。驗收依據架構師更新後的 SPEC／API 契約 `v0.2.2`，包括固定 builtin Agent fixture、`event_id` 去重、`/result` 只回寫資料、worker 唯一決定終態。文件將 ZEN-000 最薄切片與 ZEN-003 四層硬化，以及 ZEN-004 完整 Run API 分開；目前只定義驗收，沒有判定實作通過。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/features/ZEN-000/acceptance.md` | `Z0-01` 至 `Z0-19` 的步驟、斷言、分期界線與報告格式 |
| `coordination/handoffs/ZEN-000/qa-verifier-to-orchestrator.md` | 本交接 |

## 3. 契約影響

- [x] QA 未修改對外契約；以架構師已更新的 `specs/api-contract.md v0.2.2` 撰寫驗收。
- [ ] QA 變更對外契約。
- 架構師的 `v0.2.2` 釐清 Internal Gateway 事件格式與 worker 終態權責。請主 session 將該變更按 AGENTS.md §3 通知 frontend-dev／backend-dev／qa-verifier；本次未發送跨角色通知。

## 4. 下一個人要做什麼

1. 將 `acceptance.md` 交 backend-dev 與 platform-eng 作為 ZEN-000 實作條件；尤其先對齊 `event_id`、`/result`、固定 builtin fixture 與三路由 token 驗證。
2. 實作進入 REVIEW 後派 QA 把各 `Z0-xx` 落為測試並執行，保存命令、輸出、DB/Redis/SSE 與 Docker 容器證據；不能以本份開發前文件宣告 G2–G4 通過。
3. 主 session 串行更新 BOARD；目前 `SPEC-QUESTION` 表顯示暫無未決問題，但 REVIEW 時仍須重新查 G6。不要由 QA 交接檔替代 BOARD 狀態。

## 5. 我沒做的事

- 未新增 `tests/**`：核心測試需要尚在實作中的 worker、runtime、migration 與可用的 compose 環境；此階段先固定可執行斷言，待 REVIEW 實作測試。
- 未執行單元、契約、整合、E2E、安全或負載測試；未驗證四層隔離，未判 ZEN-000 `DONE`。
- 未修改 `coordination/BOARD.md`、產品程式碼、SPEC／API/UI 契約或 CHANGELOG。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| 現有實作先於 `v0.2.2` 澄清而寫 | 既有事件 body、UUID 版本、`event_id`、結果路由可能與新契約不合；尚無測試結果可判定 | 開發按 `Z0-08`／`Z0-12`／`Z0-17`／`Z0-18` 修整，REVIEW 再提供實際失敗證據 |
| A4 僅定「延遲可接受」，無數值 SLO | QA 可驗證終態前逐筆送達、記錄延遲，但不能自行宣布某個延遲門檻 | 若要性能門檻，由 architect 走 SPEC-QUESTION／版本變更 |
| ZEN-003 尚未做完整隔離硬化 | ZEN-000 僅 L1 正反斷言；核心逃逸未覆蓋 | 報告明列 skipped／未覆蓋，勿寫「四層隔離通過」 |
| 本地依賴與 Docker 權限 | 本輪 QA 無可重現的測試執行環境證據 | REVIEW 時先記錄 runtime／Docker 可用性，再執行整合測試 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 文件格式 | 通過 | Python 檢查 `Z0-01`～`Z0-19` 均存在、無舊版號與尾端空白；八個交接段落齊全；`git diff --check` exit 0 |
| 單元測試 | 未跑 | 尚未新增測試 |
| 契約測試 | 未跑 | 尚未到 REVIEW |
| API→Celery→沙盒→SSE 整合 | 未跑 | 尚未到 REVIEW |
| L1／token／capabilities 安全驗證 | 未跑 | 尚未到 REVIEW |
| G1–G6／DONE | 未裁定 | 開發前階段，無權以驗收文件替代實測 |

## 8. 未決的 SPEC-QUESTION

- [x] QA 本輪未新增 SPEC-QUESTION；架構師已在 `v0.2.2` 釐清 `/result`、事件重送與終態責任。BOARD 原 `Q-001` 是樣例，主 session 已移出正式提問表。
