# ZEN-000 規格守門員最終稽核交接

**任務**：`ZEN-000` 貫穿骨架  
**從**：@spec-keeper　**給**：@orchestrator  
**日期**：2026-09-21  
**依據**：SPEC/API 契約 `v0.2.2`、ZEN-000 feature spec、ADR-0001～0007

## 1. 裁定

**G1：不通過。** 本次沒有發現 P1～P5 的直接紅旗，但仍有三項實作契約偏離與兩項驗收證據失真，不能把 ZEN-000 推進 `DONE`。

## 2. 關鍵證據

1. `apps/worker/tasks/run.py:192-215` 的 duplicate task 檢查在 row lock 外，會讓同一 `run_id` 建立兩個 sandbox；`apps/sandbox/manager.py:92-99` 的相同 Redis member 不會替它擋下第二次建立。
2. `_finish_sync()` 在 `apps/worker/tasks/run.py:121-125` 的 Redis 發布失敗後，重送會被 `apps/worker/tasks/run.py:192-195` 的 terminal early return 略過，無法補發 pending terminal event，違反契約 §4。
3. `apps/worker/tasks/run.py:36,210-212` 把 run token TTL 設成 timeout + 30 秒，契約規定是 timeout_seconds。
4. `apps/sandbox/runtime/__main__.py:71-95` 只產生四個 node events；`specs/features/ZEN-000/acceptance.md:21-31,53` 與 `scripts/verify-zen000.sh:27-29` 卻要求／宣稱 `log`、`custom` 和 11 筆事件。
5. `scripts/verify-zen000.sh:39` 查 `zentra.managed=true`，但 `apps/sandbox/drivers/docker_driver.py:44` 沒有這個 label，因此零殘留斷言無效。
6. `docker/agent-runtime.Dockerfile:5` 使用 `pip install`，偏離 SPEC §6／§11 的 `uv only` 套件管理要求。

## 3. 一致項目與範圍界線

- worker／api 未找到載入使用者模組的 `exec`、`eval`、`importlib` 或 `subprocess` 路徑；P1 靜態檢查通過。
- compose 的網段與 socket 拓撲符合：只有 api 同接兩網段，只有 worker 掛 `docker.sock`；沙盒只注入三個 run-scoped `ZENTRA_*` 變數。
- 狀態枚舉、事件 UUID v7、事件 `(run_id,event_id)` 唯一性與 `capabilities()` 的 false 回報方向符合契約。
- gVisor／容器逃逸／L3 壓力測試等 ZEN-003 項目仍是未覆蓋或 skipped；本次沒有把它們誤判為通過。

## 4. 建議下一棒

- `@backend-dev` 先修正 duplicate delivery 的鎖內重查、terminal pending event 補發與 token TTL；完成後交 `@qa-verifier`。
- `@platform-eng`／`@qa-verifier` 對齊 runtime 事件內容、驗收腳本與實測報告，重新取得可重現證據。
- `@platform-eng` 將 runtime image 安裝改為 `uv`，再跑容器建置。

## 5. 未決 SPEC-QUESTION

本次沒有新增產品規格提問；若團隊要保留 token buffer 或要求 log/custom 為 ZEN-000 固定輸出，請先由 `@architect` 裁決並更新契約／feature spec。
