# Changelog

## Unreleased

- 完成 `ZEN-000` 貫穿骨架：固定內建 echo Agent、FastAPI Run API、Internal Gateway、Celery `q.run`、Alembic migrations、事件持久化與 SSE。
- 加入每次 Run 的 Docker sandbox、run-scoped token、LangGraph runtime、事件 UUIDv7 去重與終態冪等處理。
- 補上 ZEN-000 驗收條件、單元／契約／整合測試與角色交接紀錄。
- 完成 `ZEN-006A` Console 早期切片：Run 觀測頁與發起 Run 的 `<dialog>`，原生 HTML/CSS/ES modules、零建置（ADR-0006）。
- `api` 服務新增 `/console/*` 頁面路由與 `/console/static` 掛載（`include_in_schema=False`，不進 OpenAPI）；api 映像改帶 `apps/console`。
- 補上 ZEN-006A 驗收條件與 `tests/zen006a`（原始碼契約、路由、範圍上限與 CDP 瀏覽器 E2E，零 npm 依賴）。
- Console Agent 詳情頁新增「編輯 metadata」與「刪除」兩個原生 `<dialog>`，接既有的 `PATCH` / `DELETE /v1/agents/{id}`；`api.js` 補 `updateAgent()` / `deleteAgent()`。使用者直接授權，**ui-contract 尚未追認（BOARD Q-009）**。

