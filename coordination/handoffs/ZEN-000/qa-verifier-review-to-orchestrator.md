# ZEN-000 QA REVIEW 裁定

**任務**：`ZEN-000` 貫穿骨架  
**從**：@qa-verifier　**給**：@orchestrator  
**日期**：2026-09-21  
**Spec／API 契約**：`v0.2.2`

## 裁定

**維持 `REVIEW`，尚不得進 `DONE`。** 已跑通 API → Celery → 獨立沙盒 → 兩節點 LangGraph → Gateway → SSE 主線，四筆節點事件與結果符合契約；但 G1 仍待 spec-keeper 重稽，G2／G3／G4 的真故障注入與 sandbox 憑證／網路正反斷言尚未全部完成。舊的 [`qa-verifier-to-orchestrator.md`](qa-verifier-to-orchestrator.md) 是**開發前**交接，裡面的「未跑」不是本次 REVIEW 結果；逐項證據以 [`acceptance.md`](../../../specs/features/ZEN-000/acceptance.md) 和 [`qa-verifier-to-spec-keeper.md`](qa-verifier-to-spec-keeper.md) 為準。

## 已取得的獨立證據

| 項目 | 本輪結果 |
|---|---|
| `bash -n scripts/verify-zen000.sh && bash scripts/verify-zen000.sh` | 27 passed／0 failed／2 skipped（gVisor／逃逸依 ADR-0005 跳過）。腳本已修正實際 labels、四筆 node events 與 10 秒零殘留輪詢。 |
| Compose one-off worker，啟用 API／Postgres／Redis 的 `tests/zen000` | 23 passed／1 skipped；worker image 按 P1 不含 LangGraph，故 runtime 模組跳過。 |
| host Python 3.11.7 的 LangGraph runtime test | 3 passed；與容器結果分列。 |
| 40 次 Run 的冷啟保守上界 | `POST → 首筆 node.start SSE` nearest-rank P95 1.341s（min 1.266／median 1.297／max 1.359）；40 個不同容器，終態後 40/40 inspect 不存在。 |
| 真 PostgreSQL 競態／補發／TTL | 同 run_id 兩個 delivery 只一個取得 row lock claim；終態 pending event 重送沿用原 seq；token TTL 精確 90 秒。 |
| 失敗終態資料庫測試 | 非零退出、無 `/result`、timeout flag、USER_CODE_ERROR 都 `FAILED` 且終局事件各一筆；這**不等於**真 Docker 故障注入已通過。 |

## 下一棒

1. 派 @spec-keeper 依已修正檔案重查 `ZEN000-DRIFT-01～06`，在 `coordination/DRIFT.md` 重新裁定 G1。
2. 由 @backend-dev／@platform-eng 協助 QA 注入真 create/start 失敗、非零退出、無結果與 1 秒 timeout；驗狀態、錯誤、事件、token 撤銷、容器銷毀。
3. QA 補從真 sandbox 帶有效 token 取 bootstrap、env/docker.sock 檢查、fresh DB migration／重啟持久性，以及 DB／Redis 故障後 live SSE。這些未覆蓋條件完成前保持 REVIEW。

本輪無新增 SPEC-QUESTION；沒有修改產品程式或 API 契約。
