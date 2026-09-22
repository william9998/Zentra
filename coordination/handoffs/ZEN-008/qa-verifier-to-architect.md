# ZEN-008 QA 驗收條件交接給架構師

**任務**：`ZEN-008` OCI image 型 Agent 註冊、呼叫與一次性執行
**從**：@qa-verifier　**給**：@architect
**日期**：2026-09-22
**Spec 版本**：`v0.3.0`

## 1. 我做了什麼

已在產品開發前建立 ZEN-008 的可執行驗收門檻，將 OCI digest 註冊、PostgreSQL metadata、`q.build`、validation、REST/MCP/Console、`q.run`、一 Run 一盒與終態清理拆成 36 組斷言。安全門檻分開驗證 Registry credential、validation token 與 run token，並完整列出 Docker 網路、核心控制、資源與檔案隔離。gVisor、核心逃逸與實體主機隔離固定列為 skipped/未覆蓋，不會被誤報為通過。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `specs/features/ZEN-008/acceptance.md` | 開發前驗收條件、fixture、測試分層、G1–G6 與未覆蓋聲明 |
| `coordination/handoffs/ZEN-008/qa-verifier-to-architect.md` | 本交接單 |

## 3. 契約影響

- [x] 沒有修改對外契約。
- 驗收逐欄位引用 `api-contract.md v0.3.0`；UI selector/元件細節保留給目前進行中的 UI contract 更新，Console 主線斷言已固定。

## 4. 下一個人要做什麼

1. 裁決 Console 與外部 REST 共用 `POST /v1/agents/{id}/runs` 時，後端如何可信產生 `trigger=CONSOLE`；API v0.3.0 目前沒有 discriminator。收斂契約後更新 `Z8-29`/`Z8-30`，不得由 QA 自創 header/body 欄位。
2. 等 `@ui-designer` 交付 ZEN-008 UI contract 後，核對本 acceptance 的 `Z8-28`/`Z8-29` 無衝突。
3. 確認 API/UI/acceptance 一致且無 SPEC-QUESTION 後，由 `@architect` 決定是否把 ZEN-008 推進 `SPEC_READY`。
4. 派 backend/platform/frontend 開發時，要求各自對應 `Z8-*` 提供可重跑證據；真 Docker/Registry 安全測試不得用 fake driver 代替。

## 5. 我沒做的事（誠實列出）

- 未撰寫產品測試或執行任何功能、安全、整合、E2E、負載測試。
- 未宣稱 ZEN-008、Docker 四層隔離或任何 G1–G6 閘門通過。
- 未更新 BOARD 狀態；`SPEC_READY` 是 architect 的裁定，且 UI contract 尚待交付。

## 6. 已知限制與風險

| 項目 | 影響 | 建議 |
|---|---|---|
| UI contract 正在平行更新 | 最終元件名稱與 selector 可能調整 | architect 核准前比對 `Z8-28`/`Z8-29`；流程語意不得縮減 |
| Console trigger 尚無 ingress discriminator | API/MCP/Console 三入口無法完整驗 `trigger` 欄位 | architect 裁決並更新契約後，QA 再收斂 `Z8-29`/`Z8-30` |
| 真 Registry/Docker 測試環境尚未建立 | 單元測試不能證明 digest pull、隔離與銷毀 | 用測試期間本機 Registry fixture；cache hit/miss 使用不同 repository/digest |
| 無 gVisor | 無法證明核心層容器逃逸防護 | 固定 skipped 並明示只跑信任 image；威脅模型改變時重開 ADR |
| Registry credential 由部署配置持有 | 容易在 log、Docker inspect 或 SandboxSpec 漏出 | 依 `Z8-18`/`Z8-19` 對 DB、Redis、task payload、env、mount、log 做負面掃描 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| 單元測試 | 未跑 | 產品實作尚未開始 |
| 契約測試 | 未跑 | 本次只定義 API v0.3.0 驗收斷言 |
| Docker／安全／E2E | 未跑 | fixtures 與測試碼尚未建立 |
| Markdown 格式 | 通過 | `git diff --check`；兩份新增檔另以 `git diff --no-index --check /dev/null <file>` 檢查 |

## 8. 未決的 SPEC-QUESTION

- [ ] 無
- [x] 有 → Console 與外部 REST 共用 Run endpoint，但 API v0.3.0 未定義如何產生 `trigger=CONSOLE`；已直接送 architect 裁決，尚未自行編號或修改 BOARD，避免與主 session 串行整合衝突。
