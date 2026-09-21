# 交接單 Handoff

**任務**：`DOC-REQUEST-FLOW` Zentra 請求到沙盒的整體流量圖
**從**：@architect　**給**：@orchestrator
**日期**：2026-09-22
**Spec 版本**：`v0.2.2`

## 1. 我做了什麼

建立一張由左到右的深色科技風 SVG，呈現 REST／MCP 進入 FastAPI、PostgreSQL 建立 Run、
Redis 與 Celery 派工、Sandbox Manager 透過 Docker API 建立一 Run 一盒 Sandbox 的主流程。
圖中另外用回程線標示 Sandbox 經 Internal Gateway 回寫事件與 result、SSE 通知，以及 Run 結束後
撤銷 token、銷毀 Sandbox、釋放 quota 的順序。安全區塊依目前能力誠實標示為 Docker hardening，
沒有 gVisor，`l2_kernel_isolation=false`。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `docs/architecture/zentra-request-flow.svg` | 可維護的 SVG 架構圖，包含服務卡片、分層邊界、編號箭頭、資料責任與安全限制 |
| `docs/architecture/detailed-architecture.md` | 在總覽新增架構圖連結與簡短說明 |
| `coordination/handoffs/DOC-REQUEST-FLOW/architect-to-orchestrator.md` | 本交接單 |

## 3. 契約影響

- [x] 沒有影響對外契約
- [ ] 有影響

## 4. 下一個人要做什麼

1. 向使用者交付 SVG，並指出 `DockerSandboxDriver.capabilities()` 目前明確回報沒有核心隔離。
2. 若要把 gVisor 納入產品範圍，另開 feature／ADR 評估 runtime、ARM64、CI 與威脅模型；不要把圖上的警示直接改成已完成。

## 5. 我沒做的事（誠實列出）

- 沒有更動 `apps/**`、Docker runtime 或任何產品行為。
- 沒有新增 gVisor；這張圖只描述並誠實標示現況。
- 沒有建立獨立 PNG 交付檔；正式圖檔維持 SVG，PNG 僅在 `/tmp` 用於視覺 QA。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| `q.build`、`agent_versions`、`llm_calls` 尚未在 ZEN-000 落地 | 若未標註階段，讀者可能誤認目前已有完整版本發布與 LLM 計量 | 圖中已將它們標為 ZEN-001／ZEN-002 後續能力 |
| Docker hardening 不提供獨立核心 | 不能宣稱可安全執行公開、不可信、多租戶程式碼 | 保留紅色 gVisor 警示與 `l2_kernel_isolation=false` 標示 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| SVG/XML 解析 | ✅ | `xmllint --noout docs/architecture/zentra-request-flow.svg` |
| 文件格式 | ✅ | `git diff --check -- docs/architecture/zentra-request-flow.svg docs/architecture/detailed-architecture.md` |
| 視覺 QA | ✅ | macOS Quick Look 渲染 1800 px PNG，人工檢查主卡片、文字、箭頭、圖例與邊界均可見；正式交付仍為 SVG |
| 單元／契約測試 | 未跑 | 純文件與 SVG 變更，不改產品行為 |

## 8. 未決的 SPEC-QUESTION

- [x] 無
- [ ] 有

