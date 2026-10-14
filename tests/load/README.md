# 流量測試

對照 [SPEC §10 NFR](../../specs/SPEC.md) 量平台撐不撐得住。**不是 pytest 測試**：
檔名刻意不用 `test_` 開頭，`make test` 不會跑到。要跑請手動執行。

## 前置

- `make up`，整套 compose 跑著
- 主機上的 `python3` 裝好 `httpx`；腳本也會從 repo 讀 `apps/api/enums.py` 的枚舉常數
- 從主機打 `http://localhost:8000`，要換位址就設 `ZENTRA_BASE_URL`

## 腳本

| 腳本 | 對照的 NFR | 會留下資料嗎 |
|---|---|---|
| `api_latency.py` | API 延遲 P95 < 100ms | 不會，只打讀取端點 |
| `concurrent_runs.py` | 併發 Run 上限預設 10，超過的要拿到 `QUOTA_EXCEEDED` | **會**，跑完要 `make clean-test-data` |

```bash
python3 tests/load/api_latency.py                       # 每個端點 200 次，併發 10
python3 tests/load/api_latency.py --concurrency 1       # 拿來跟併發 10 比，分辨是單筆慢還是在排隊
python3 tests/load/concurrent_runs.py                   # 對 builtin-echo 一次丟 15 筆
python3 tests/load/concurrent_runs.py --agent-id <id>   # 指定別的 Agent
make clean-test-data
```

兩支都輸出 JSON 報告，通過的話 exit 0，沒過是 exit 1。

## 注意

- **單機**：每個沙盒的上限是 2g/2cpu。10 個同時跑會壓到 Colima VM，VM 太小會把 Docker daemon 拖死。
- `concurrent_runs.py` 預設打 `builtin-echo`。它不呼叫 LLM，也不用常駐容器，不會產生費用。
  如果改打真的 Agent（例如會呼叫 LLM 的 stock-agent），每筆 Run 都會真的計費。
- inflight 是用 `docker compose exec redis` 讀的，不從主機直連 6379，原因是主機上的同名服務可能會遮蔽容器裡那個。

## 量測紀錄

| 日期 | 腳本 | 結果 |
|---|---|---|
| 2026-10-07 | `api_latency.py` 併發 10 | **沒過**。三個端點 P50 約 40ms，P95 在 218–276ms |
| 2026-10-07 | `api_latency.py` 併發 1 | P95 在 10–18ms。所以併發 10 的慢是請求在排隊，不是單筆查詢慢 |
| 2026-10-07 | `concurrent_runs.py` | **沒跑成**：這台 DB 的 `builtin-echo` fixture 不見了（POST 回 404） |
