# ADR-0010：要不要讓 Agent 用 LangGraph 以外的框架（例如 OpenAI Agents SDK）

| | |
|---|---|
| 狀態 | **提案中，等產品負責人裁決 (Proposed)** |
| 日期 | 2026-09-25 |
| 撰寫者 | @architect（依使用者要求評估推翻 P2） |
| 相關 | **提議修改 [SPEC](../SPEC.md) §2 的 P2**；牽動 [ADR-0002](ADR-0002-celery-langgraph-boundary.md)、[ADR-0003](ADR-0003-agent-packaging-dual-interface.md)、[ADR-0008](ADR-0008-oci-image-agent-packaging.md)；P1／P4／P5 不受影響 |

> **這份 ADR 不做決定，它定價。** 五條立場只有產品負責人能鬆。
> 下面把「鬆開 P2 要付什麼」逐項寫出來，好讓那個決定是基於數字而不是直覺。

## 背景

使用者問：「現在這個框架可以支援 OpenAI Agents SDK 嗎？」

答案是不行，而且有兩層阻擋：

1. **規格層**：P2 寫著「Agent 框架統一 LangChain + LangGraph」，理由是
   「收斂成一種，串流 / 觀測 / 之後的 checkpoint 才只要寫一次」。
2. **實作層**：runtime 會實際檢查回傳值的型別 ——
   [`image_runtime.py`](../../packages/sdk/zentra_sdk/image_runtime.py) 的
   `_is_uncompiled_state_graph()`，不是 `StateGraph` 就 `BUILD_FAILED`。

值得先講清楚一件事，因為它會影響這個決定的價值：**「用 OpenAI 的模型」與
「用 OpenAI 的 agent 框架」是兩件不同的事。** 前者早就支援 ——
ZEN-002 的 `ctx.llm()` 是 OpenAI 相容的（契約 §4），使用者現在就能用 GPT 系列模型，
只是流程由 LangGraph 組。**這份 ADR 討論的只有後者。**

## 平台從「未 compile 的 StateGraph」拿到什麼

這是定價的關鍵：P2 不是品味問題，它換到了四樣具體的東西。

| 拿到什麼 | 怎麼拿到的 | 換成別的框架會怎樣 |
|---|---|---|
| **節點級事件** | 平台自己 `compile()`，在每個節點前後注入 hook，產出 `node.start` / `node.end` | Run 觀測頁的時間軸變空的。使用者只看得到「開始」與「結束」 |
| **單一 runtime 契約** | `zentra.image/v1` 只要求一個型別 | 每種框架要一組 adapter、一組驗證邏輯、一組事件對映 |
| **可預測的驗證** | `validate` 只需 import + 檢查型別 + 抽 schema | 不同框架的「entrypoint」概念不同，`entrypoints` 這個唯一 schema 來源（P3）會失去共同形狀 |
| **之後的 checkpoint / resume** | LangGraph 原生有 checkpointer | 得為每種框架各做一次，或放棄該功能 |

**第一項是最貴的。** Run 觀測頁是這個產品對「agent 到底在做什麼」的答案；
節點事件沒了，它就退化成一個 log viewer。

## 選項

### 選項 A：維持 P2，不支援其他框架（建議）

- **好處**：上表四項全部保留。零實作成本。一種路徑 = 一套測試、一種失敗模式。
- **代價**：熟悉 OpenAI Agents SDK 的使用者要改用 LangGraph 重寫流程。
  但注意——**模型還是可以用 OpenAI 的**，改的是編排方式，不是供應商。
- **這個代價有多大**：對「內部工具、單租戶、只跑信任的程式碼」的交付條件
  （[ADR-0005](ADR-0005-mvp-single-host-scope.md)）來說，使用者就是開發這個平台的人自己。
  重寫一個 graph 的成本遠低於維護兩套 runtime 契約。

### 選項 B：開放任意框架，只要求 `build()` 回傳「可呼叫的東西」

- 實作：把 `_is_uncompiled_state_graph()` 換成「有 `invoke`／`ainvoke` 就算過」。
- **好處**：使用者想用什麼就用什麼。實作改動最小（一個函式）。
- **代價**：
  - **節點事件全部消失**，因為平台不再知道怎麼進到流程內部。Run 觀測頁退化。
  - `entrypoints` 的 schema 來源失去共同形狀，P3「同一份定義生成 REST + MCP」開始有例外。
  - 驗證只能驗「有沒有那個方法」，抓不到框架特有的錯 ——
    使用者會在 `RUNNING` 時才發現問題，而不是在 `validate` 時。
- **否決理由**：它用產品最重要的畫面換一個函式的彈性。
  **改動最小的選項，代價最大。**

### 選項 C：多框架 adapter 層

- 實作：定義一個 `AgentAdapter` 介面（載入、抽 entrypoints、包事件），
  LangGraph 與 OpenAI Agents SDK 各實作一份。
- **好處**：節點事件與 P3 都能保住，因為 adapter 負責把框架的內部事件對映成平台事件。
- **代價（這是真的要寫、要測、要維運的）**：
  - 一個新的抽象層，加上每個框架一份 adapter 與其事件對映
  - `zentra.image/v1` 要加一個「宣告我用哪個框架」的欄位，並在 validate 時分派
  - base image 要裝兩套框架，或變成多個 base image
  - 每次任一框架改版都可能打破對映 —— **這是持續成本，不是一次性成本**
  - 測試矩陣乘二：每條隔離／事件／契約測試都要跑兩種框架
- **否決理由（在當前交付條件下）**：這是為「多框架」這個還沒有人要求的能力
  先付一個持續成本。使用者現在只問了「可以嗎」，不是「我有一個非 LangGraph 的 agent 要上」。
  **等真的有那個 agent 再付這筆錢，那時的定價也會更準。**

## 建議

**選項 A：維持 P2。**

理由收斂成一句：**P2 換到的節點級觀測，是這個產品區別於「一個會跑 Python 的佇列」的地方，
而 OpenAI Agents SDK 沒有帶來使用者現在拿不到的能力**——模型已經能用了（ZEN-002），
差的只是編排風格。

## 什麼情況該重新檢討

- **有一個真實存在、非 LangGraph 寫的 agent 必須上線**，且重寫成本明顯高於 adapter 成本。
  屆時走選項 C，並且**先把事件對映設計出來再動手** —— 不要先開放型別檢查再補觀測。
- **LangGraph 本身變成風險**（停止維護、授權變更、重大不相容改版）。
  這時要換的不是「多支援一種」，而是「換一種」，那是另一個決定。
- **出現「平台組裝式」需求**（使用者不寫程式碼，用 UI 拉流程）。
  那條路在 [BOARD §刻意不做](../../coordination/BOARD.md)，但它一旦回來，
  框架抽象層的定價會完全不同 —— 因為那時平台自己就是流程的作者。

## 後果

### 採納選項 A 的後果

- 使用者要用 LangGraph 寫流程。文件要把「模型可以用 OpenAI，框架不行」講清楚，
  因為這是最容易誤會的一點。
- `_is_uncompiled_state_graph()` 這個型別檢查**是規格的一部分**，不是實作細節。
  它的錯誤訊息要能讓人知道為什麼被擋，而不只是「型別不對」。

### 沒有被這個決定影響的

P1（使用者程式碼不在平台行程內）、P4（沙盒拿不到憑證）、P5（版本不可變）
與框架選擇正交 —— 不論用什麼框架，程式碼都在沙盒裡跑、都拿不到金鑰、版本都不可變。
