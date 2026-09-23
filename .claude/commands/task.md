---
description: 派工 — 判斷這個任務該自己做還是派 subagent，該派誰
---

任務：$ARGUMENTS

## 1. 先判斷是哪一級（`AGENTS.md` §流程分級）

| 規模 | 判準 | 怎麼做 |
|---|---|---|
| **小** | 單一區域、不動契約、不碰沙盒/憑證 | **你自己做**，跑受影響的測試就好。不派 subagent、不寫交接單。 |
| **中** | 跨區域但契約不變 | 派**一個** `builder` 一次做完 |
| **大** | 會改 `api-contract.md` / `ui-contract.md`、需要新 ADR、或動到沙盒與憑證邊界 | `architect` → `builder` → `reviewer`，寫交接單 |

判斷不了 → 當成小的先做，發現契約要改再升級。

## 2. 要派工的話，prompt 裡一定要有

```
你是 <architect|builder|reviewer>。角色定義在 .claude/agents/<role>.md。
任務：<一句話>
可寫範圍：<明確路徑>
不要做：<明確的排除清單>
這次會用到的端點與 payload 形狀：<實測過的，不要讓它自己去挖>
已知的坑：<會 404 的東西、會炸的邊界、環境限制>
回報：做了什麼、哪裡沒做、有沒有偏離契約、有沒有要我裁決的事。
```

只有**大**級才加一句「完成後寫 `coordination/handoffs/<id>/<you>-to-<next>.md`」。

平行派工只用在寫入範圍**不重疊**的工作。

## 3. 收工

- 更新 `coordination/BOARD.md` 的狀態（只有 BOARD 上有列的任務才需要）
- subagent 回報了規格不清楚的地方 → **直接問使用者**，不要自己決定
