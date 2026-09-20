# ADR-0003：Agent 打包格式與 API/MCP 雙門面生成

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-20 |
| 決策者 | @architect |
| 相關立場 | P2（框架統一）、P3（雙門面）、P5（版本不可變） |

## 背景

兩個需求綁在一起：
1. 使用者有兩種建立 Agent 的方式（平台組裝、上傳程式碼），下游不應該為此分叉
2. 每個 Agent 都要同時提供 REST API 與 MCP 接口

如果放任兩種建立方式各自發展，派工、沙盒、觀測、計費全都要寫兩套。
如果 REST 與 MCP 各自定義 schema，遲早會出現「API 能傳這個欄位、MCP 不能」的不一致。

## 決定

### 一、統一收斂到 Agent Manifest

兩條路徑最終都產出**同一個 Manifest**。下游元件完全不需要知道 Agent 從哪來。

```yaml
# zentra.yaml
apiVersion: zentra/v1
name: research-agent
version: 1.2.0
runtime:
  python: "3.12"
  entry: "agent:build"          # 必須回傳 StateGraph（P2）
entrypoints:
  - name: default
    description: "研究一個主題並產出摘要"
    input_schema:               # 唯一的 schema 定義來源
      type: object
      properties:
        question: { type: string, description: "研究問題" }
        depth:    { type: integer, minimum: 1, maximum: 5, default: 3 }
      required: [question]
    output_schema:
      type: object
      properties:
        summary: { type: string }
        sources: { type: array, items: { type: string } }
tools:
  - ref: registry://web_search@2
  - ref: mcp://external-crm         # 外接 MCP 當 tool
knowledge:
  - collection: internal-docs
    mode: hybrid
resources:
  cpu: "2"
  memory: "4Gi"
  timeout_seconds: 900
egress:
  allow: ["api.example.com"]        # 白名單，由 Policy Engine 核可
```

### 二、`build()` 回傳 StateGraph，不回傳 CompiledGraph

```python
@agent(name="research-agent", version="1.2.0")
def build(ctx: ZentraContext) -> StateGraph:
    ...
```

**為什麼是未 compile 的圖？**
因為 `compile()` 的時候要掛上 checkpointer、store、串流回呼、trace hook —— 這些**由平台注入**。
如果使用者自己 compile，他就能換掉 checkpointer（狀態不進我們的 DB）、跳過 trace hook（觀測失效）。
把 compile 收在平台手上，觀測與持久化才是強制的，不是可選的。

### 三、Interface Generator：一份 entrypoints，兩個門面

```
entrypoints[].input_schema (JSON Schema)
        │
        ├──→ OpenAPI 3.1 operation  →  POST /v1/agents/{id}/runs
        └──→ MCP tool definition    →  tools/list / tools/call
```

**關鍵約束**：兩者**不得**有各自的 schema 定義。JSON Schema 同時是 OpenAPI 與 MCP tool schema 的原生格式，這讓單一來源成為可能。

QA 必須有一條契約測試：**對同一個 entrypoint，REST 的 input schema 與 MCP 的 tool input schema 語意等價**。
這條測試是 P3 的守門員，不能省。

### 四、不可變性（P5）

發布後凍結：`manifest` + `code bundle` 的內容位址雜湊 + `uv.lock` + image digest。
- `PATCH /v1/agents/{id}` 只能改 metadata（描述、標籤），**不能改已發布版本的行為**
- 要改行為 → 發新版本 → 用 alias 切流量
- 這讓「為什麼上週好好的今天壞了」有明確答案：要嘛 alias 換了，要嘛外部依賴變了，**不可能是程式碼被偷改**

## 考慮過但否決的選項

- **讓使用者自由選 agent 框架**（CrewAI / AutoGen / 自寫迴圈）
  否決理由：平台要提供統一的 checkpoint、串流、HITL、trace，就必須理解 agent 的內部結構。
  每多支援一個框架，這四項能力就要重寫一次。**收斂的價值大於彈性**（P2）。

- **MCP 由使用者自己在程式碼裡實作**
  否決理由：會出現每個 Agent 的 MCP 行為都不一樣，且使用者可能實作錯協定。平台生成才能保證一致。

- **允許使用者回傳 CompiledGraph**
  否決理由：見上方「為什麼是未 compile 的圖」。這是觀測與持久化的強制點。

## 後果

### 正面
- 下游元件（派工、沙盒、觀測、計費）只認 Manifest，不需分支邏輯
- REST 與 MCP 永遠一致，不會漂移
- 版本不可變讓事故排查有確定的起點

### 負面
- 使用者被限制在 LangGraph 一種框架。**這是刻意的取捨**，要在文件明講，不要讓使用者到上傳失敗才發現。
- `build()` 的契約是硬約束，Build 階段就要驗證並給出清楚的錯誤訊息（不能讓使用者等到 runtime 才知道）
- 平台注入 compile 參數意味著平台升級 LangGraph 版本時可能影響既有 Agent —— 需要版本相容性策略與測試矩陣

### 何時該重新檢討
- 出現 LangGraph 表達不了、但對使用者很重要的 agent 模式
- MCP 協定演進到 JSON Schema 不足以描述其 tool 定義
