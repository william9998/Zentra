---
name: zentra-verify
description: 驗收 Zentra 指定任務，派遣 QA 檢查品質閘門、收集測試證據並結合規格稽核判定是否可完成。
---

# Zentra 任務驗收

定位專案根目錄，讀 AGENTS.md、specs/SPEC.md、指定任務的交接與驗收條件。
使用 qa-verifier 子 agent；已是 qa-verifier 時直接執行，勿重複派遣自身。
要求讀 .claude/agents/qa-verifier.md、當前 API／UI 契約與 BOARD。
無驗收條件時先標明缺漏，由 QA 補定後再驗；沒有工具時不可宣稱獨立驗收。

按 AGENTS.md §6 的 G1–G6 列出適用性、結果與證據。
依改動選擇必要的契約、核心路徑、失敗與安全檢查；不為純文件修改啟動產品服務。
不適用項目寫明原因；測試跑不起來標未驗證，不標通過。
MVP 是單租戶；不得套用舊版的多租戶／知識庫必測規則。
沙盒相關修改依 SPEC §6.2 與 QA 角色清單驗證，無 gVisor 項如實 skipped。

由 spec-keeper 提供 G1 與契約漂移證據；產品檔案保持唯讀，缺陷退回原責任角色。
本次任務相關的未決 SPEC-QUESTION 由 architect 裁決，其他項目如實列出不混為本次阻塞。
全部適用閘門及稽核通過後，只有 QA 能裁定 DONE；主 session 可依該裁定整合 BOARD。
完成或退回都寫具體 handoff，附執行命令、結果、未覆蓋部分與重現步驟。
