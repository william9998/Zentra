---
description: 開工對齊 — 讀完必讀文件並回報對專案的理解，確認全隊認知一致
---

執行認知對齊檢查。依序讀取：

1. `CLAUDE.md`
2. `specs/SPEC.md`（特別注意 §2 五條核心立場、§5 分層、§9 Run 狀態機）
3. `AGENTS.md`
4. `specs/glossary.md`
5. `coordination/BOARD.md`（現在的任務狀態與未決的 SPEC-QUESTION）
6. 若有指定任務 ID（$ARGUMENTS）：讀 `specs/features/<id>/` 底下全部檔案與 `coordination/handoffs/<id>/` 的交接單

讀完後**簡潔**回報（不要複述文件內容）：

- **專案一句話**：用你自己的話說 Zentra 是什麼
- **五條核心立場**：列出 P1–P5，各一行
- **目前任務狀態**：BOARD 上進行中、被阻塞的各有哪些
- **未決的 SPEC-QUESTION**：有幾則、卡住誰
- **如有指定任務**：它屬於哪一層、觸碰哪些契約、是否觸碰 P1–P5、驗收條件是什麼、誰負責

最後明確指出：**你發現的任何規格矛盾、缺漏或過時之處**。沒發現就說沒發現。
