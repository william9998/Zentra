# 交接單 Handoff

**任務**：`DEV-CODEX` Codex 專案協作設定驗收
**從**：@qa-verifier　**給**：@orchestrator
**日期**：2026-09-21（驗證跨日完成）
**Spec 版本**：`v0.2.1`；API 契約 `v0.2.1`；UI 契約 `v0.4.0`

## 1. 我做了什麼

依 BOARD 的 DEV-CODEX 完成條件，驗證七角色 TOML、Claude 正文同步、五個技能格式與流程、文件實體路徑。
初查回報 QA 錯誤碼來源未修正，主 session 修正後重驗通過；同時複查 architect／QA 的 BOARD 交棒指示。
已讀 spec-keeper-to-qa-verifier.md 與 DRIFT 的修正紀錄，兩項發現均關閉。
**QA 裁定 DEV-CODEX 可進 DONE**；只適用於協作設定維護，不代表任何 ZEN 產品任務或安全驗收通過。

## 2. 產出的檔案

| 檔案 | 說明 |
|---|---|
| `coordination/handoffs/DEV-CODEX/qa-verifier-to-orchestrator.md` | 本次驗證證據、裁定與未覆蓋範圍 |

## 3. 契約影響

- [x] 沒有影響對外契約；產品 SPEC／API／UI 契約版本不變。
- [ ] 有影響

## 4. 下一個人要做什麼

1. 主 session 依本 QA 裁定串行更新 BOARD 的 DEV-CODEX 為 DONE，連結本交接單。
2. 向使用者說明入口、七角色與五技能的位置，保留下列實際執行驗證限制。
3. 不變更 ZEN 任務狀態，不將設定格式驗證解讀為產品功能已完成。

## 5. 我沒做的事（誠實列出）

- 未重啟客戶端／新 session 驗證全部設定載入，未完整跑五個技能的產品工作流。
- 未執行產品單元、契約、整合、E2E、負載或沙盒安全測試；本次無產品實作變更。
- 未讀取執行中的 SandboxDriver capabilities，也未作出「四層隔離通過」或核心逃逸覆蓋的宣稱。
- 未修改產品、角色、技能、BOARD，未 commit／push。

## 6. 已知限制與風險

| 項目 | 影響 | 後續方式 |
|---|---|---|
| 靜態格式與流程檢查 | 不保證每次實際派工都遵守流程 | 使用時核對讀檔、範圍與交接證據 |
| MD／TOML 正文各存一份 | 單邊修改會漂移 | 同步修改並重做正文等價驗證 |
| 角色指示不是硬性 ACL | 不提供作業系統目錄隔離 | 保留範圍約束、diff 檢查及獨立稽核 |

## 7. 驗證狀態

| 檢查 | 結果 | 證據 |
|---|---|---|
| TOML／必要欄位 | 通過 7/7 | Python `tomllib.loads`；`name / description / developer_instructions` 均為非空字串，name 等於檔名 |
| 角色同步 | 通過 7/7，修正後重驗 | TOML description 等於 Claude metadata；instructions 等於去 frontmatter 後正文 |
| 技能格式 | 通過 5/5 | 下方 quick_validate 命令各回 `Skill is valid!`，exit 0 |
| 技能流程 | 通過本次文件驗收 | 五技能保留角色邊界、規格優先、交接、QA DONE 裁定及 spec-keeper 稽核；未宣稱缺工具也完成獨立驗證 |
| 文件實體路徑 | 通過 | 15 個具體必讀／技能路徑存在；README／AGENTS 新 Codex 入口連結存在；角色佔位路徑對應七份實體檔案 |
| 已回報缺陷複查 | 通過 | QA 範例改為 API 契約 §0；architect／QA 改交主 session 串行整合 BOARD；兩份 TOML 同步 |
| diff 格式 | 通過 | `git diff --check` exit 0，無輸出 |
| 獨立漂移稽核 | 通過本次設定範圍 | `spec-keeper-to-qa-verifier.md`；D-CODEX-01／02 均已關閉 |

七角色驗證可重現命令（專案根目錄）：

```bash
python3 - <<'PY'
from pathlib import Path
import re, tomllib
files = sorted(Path('.codex/agents').glob('*.toml'))
assert len(files) == 7
for path in files:
    data = tomllib.loads(path.read_text())
    for key in ('name', 'description', 'developer_instructions'):
        assert isinstance(data.get(key), str) and data[key].strip()
    md = Path('.claude/agents', path.stem + '.md').read_text()
    assert data['name'] == path.stem
    assert data['description'] == re.search(r'^description: (.+)$', md, re.M)[1]
    body = re.sub(r'\A---\n.*?\n---\n', '', md, count=1, flags=re.S)
    assert data['developer_instructions'].strip() == body.strip()
    print('PASS', path.stem)
PY
```

輸出：architect、backend-dev、frontend-dev、platform-eng、qa-verifier、spec-keeper、ui-designer 全部 PASS。

技能驗證實際以 Python subprocess 對五個目錄逐一執行下列命令，各回 exit 0：

```text
/opt/anaconda3/bin/python /Users/william/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/zentra-align
/opt/anaconda3/bin/python /Users/william/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/zentra-task
/opt/anaconda3/bin/python /Users/william/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/zentra-handoff
/opt/anaconda3/bin/python /Users/william/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/zentra-verify
/opt/anaconda3/bin/python /Users/william/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/zentra-drift
```

適用閘門：

| 閘門 | 本次裁定 |
|---|---|
| G1 | 設定指示未新增違反 P1–P5，由 spec-keeper 提供稽核證據；產品實作未驗 |
| G2 | 產品契約測試不適用：未改 API/UI；角色對現行契約的引用已複查 |
| G3 | 產品單元／整合不適用：無新增產品邏輯；工具設定格式與文件驗證通過 |
| G4 | 不適用：未改沙盒、憑證、網路出口；安全測試未跑 |
| G5 | 架構、實作、稽核、QA 交接齊備；BOARD 有 DEV-CODEX 變更紀錄；無產品 SPEC 變更 |
| G6 | 本次無未決 SPEC-QUESTION；BOARD Q-001 明示為範例，不構成本次阻塞 |

## 8. 未決的 SPEC-QUESTION

- [x] DEV-CODEX 無。
- [ ] 有
