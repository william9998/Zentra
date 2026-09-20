# ADR-0002：Celery 與 LangGraph 的職責邊界

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-20 |
| 決策者 | @architect |
| 相關立場 | P1、P2 |

## 背景

我們有兩套「編排」機制，功能看起來重疊：
- **Celery**：分散式任務佇列，有重試、排程、佇列路由、worker 擴縮
- **LangGraph**：Agent 內的狀態機，有 checkpoint、條件分支、HITL 中斷、串流

不劃清邊界，會出現兩種災難：
1. 每個 LangGraph node 都做成一個 Celery task → 狀態在 broker 與 checkpointer 之間來回搬運，延遲暴增、除錯地獄
2. 全部塞進一個 Celery task 裡跑幾小時 → 無法擴縮、worker 重啟就全丟、看不到進度

## 考慮過的選項

### 選項 A：只用 Celery，LangGraph 只當函式庫
每個 node 一個 task，用 chain 串起來。
- ✅ 每一步都有重試與可見性
- ❌ 節點間狀態要序列化進 broker，大 state（含對話歷史、檢索結果）會撐爆訊息
- ❌ 失去 LangGraph 的串流與 HITL 能力
- ❌ 一次 Agent 執行變成數十個 task，trace 支離破碎

### 選項 B：只用 LangGraph，Celery 只當觸發器
- ✅ 單純
- ❌ 無法依資源特性分池（長任務與短任務搶同一批 worker）
- ❌ 跨 Agent 的 fan-out/fan-in 沒有好的原語
- ❌ 排程、死信、退避重試都要自己造

### 選項 C：依「邊界性質」劃分 ← **選這個**

## 決定

**一條判準：需要跨機器 / 需要排隊 / 需要獨立擴縮 → Celery；只是同一 Agent 內部的下一步 → LangGraph node。**

| Celery 負責 | LangGraph 負責 |
|---|---|
| 佇列與優先權（tier 分流） | 節點狀態機與條件分支 |
| Worker 擴縮與資源隔離 | Checkpoint 與續跑 |
| 重試、逾時、死信 | HITL `interrupt()` |
| 排程（Beat） | 串流 token 與事件 |
| **多個 Agent 之間**的 fan-out/fan-in | 子圖與工具迴圈 |

具體來說：
1. **一次 Agent Run = 一個 Celery task**。不管圖裡有 30 個 node，對 Celery 來說就是一件事。
2. **`runner-worker` 不執行圖**（P1）。它申請沙盒、轉送事件、回寫狀態。圖在沙盒裡跑。
3. **HITL 中斷時釋放算力**：`interrupt()` → 寫 checkpoint → 沙盒銷毀 → Celery task 結束。
   `resume` 時是**一個新的 Celery task**，從 checkpoint 在**新沙盒**續跑。
   （這也順帶滿足「沙盒不重用」的規則。）
4. **Agent Flow 跨 Agent 編排用 Celery chord**（預設模式 2，每個 Agent 一個沙盒），
   只有當所有 Agent 都是平台組裝、信任邊界相同時，才編成單一 supervisor graph（模式 1）。

## 冪等性要求（這個決定的必要配套）

Celery 的 `acks_late=True` 意味著 **task 可能被執行超過一次**（worker 在 ack 前掛掉）。
因此每個 `runner-worker` task 必須：
1. 以 `run_id` 為冪等鍵
2. 執行前檢查 Run 狀態，**已終結（SUCCEEDED/FAILED/CANCELLED）就直接返回**
3. 絕不在重試路徑上重複呼叫會產生副作用的外部 API 或重複計費

**沒有做到冪等，`acks_late` 會把「偶爾重跑」變成「偶爾重複扣款」。**

## 後果

### 正面
- Trace 乾淨：一個 `run_id` 對應一個 Celery task 對應一個沙盒，出事好定位
- 兩套機制各用所長，沒有功能重疊造成的混淆
- HITL 長時間等待不占算力

### 負面
- **單一 Run 內的節點失敗，Celery 幫不上忙** —— 節點層級的重試要在 LangGraph 圖裡自己寫
- 一個 Run 跑很久的話，Celery 只看得到「進行中」，細節要靠事件流觀測（所以事件流是必要基礎設施，不是加分項）
- `resume` 換新沙盒意味著冷啟成本，且**圖裡不能依賴沙盒本地狀態**（必須全部進 checkpoint）——這條要寫進 SDK 文件警告使用者

### 何時該重新檢討
- 出現單一 Agent 內部需要跨機器平行處理的場景（例如 map-reduce 式的大批次處理）
- LangGraph 提供了原生的分散式執行能力
