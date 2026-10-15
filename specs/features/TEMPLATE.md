# Feature Spec：<ZEN-00X> <功能名稱>

| 欄位 | 值 |
|---|---|
| 狀態 | `DRAFT` \| `SPEC_READY` \| `IMPLEMENTED` |
| 擁有者 | @architect |
| 對齊 Spec | `specs/SPEC.md vX.Y.Z` |
| 涉及分層 | L0 / L1 / L2 / L3 / L4 / L5 / L6 |

## 1. 要解決什麼問題
<使用者的痛點，一到三句。不要寫技術方案。>

## 2. 範圍

### 做
-

### **明確不做**（同等重要）
-

## 3. 對核心立場的影響
| 立場 | 有無觸碰 | 說明 |
|---|---|---|
| P1 使用者程式碼不在平台行程執行 | ☐ | |
| P2 框架統一 LangChain/LangGraph | ☐ | |
| P3 API + MCP 雙門面 | ☐ | |
| P4 沙盒無長期憑證 | ☐ | |
| P5 版本不可變 | ☐ | |

## 4. 契約變更
- [ ] 無變更
- [ ] `specs/api-contract.md` → 變更內容 + 新版本號
- [ ] `specs/ui-contract.md` → 變更內容 + 新版本號

## 5. 設計要點
<資料模型、流程、時序。複雜的話畫 Mermaid。>

## 6. 分工
| 角色 | 工作 | 可寫範圍 |
|---|---|---|
| @backend-dev | | `apps/api/**` |
| @frontend-dev | | `apps/console/**` |
| @platform-eng | | `apps/sandbox/**` |

## 7. 驗收條件
> 直接寫在這一節，條列「怎樣算做完」。要能對應到測試或手動檢查。

## 8. 風險與未決問題
| 風險 | 影響 | 對策 |
|---|---|---|

未決 → 開 `SPEC-QUESTION` 於 `coordination/BOARD.md`。
