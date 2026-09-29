# ADR-0013：刪 Agent 要連容器與 image 一起刪

| | |
|---|---|
| 狀態 | Accepted |
| 日期 | 2026-09-29 |
| 取代 | [ADR-0009](ADR-0009-internal-registry-image-upload.md) 的 R1「`delete.enabled=false` 寫死」 |

## 脈絡

`DELETE /v1/agents/{id}` 原本是**軟刪除**：標記 `deleted_at`，列表上看不到，
但版本、容器紀錄、Run 都留著，而且——這是關鍵——**常駐容器還在跑，image 還佔著磁碟**。

ADR-0009 的 R1 把 registry 的 `delete.enabled` 寫死成 `false`，理由是
「沒有版本刪除語意就做 GC，等於發明一個會刪掉 `PUBLISHED` 版本的定時炸彈」。
那個判斷在當時是對的：那時**確實沒有**任何刪除語意。

## 決策

1. **`DELETE /v1/agents/{id}` 改成硬刪。** Agent、版本、容器紀錄、Run、事件、
   LLM 呼叫、憑證、API key 全部消失。
2. **一併停掉它的常駐容器、刪掉主機上的 image、刪掉 registry 裡的 manifest。**
3. **`REGISTRY_STORAGE_DELETE_ENABLED` 改成 `true`。**

## 為什麼推翻 R1

R1 擔心的是「沒有刪除語意的 GC」。現在有了刪除語意，而且範圍很窄：

- **只有「刪整個 Agent」這一條路徑會刪 manifest。** 沒有任何端點可以單獨刪掉
  某一個版本的 image —— 那仍然違反 P5（版本不可變）。
- 刪掉的是**已經沒有主人的 image**：那個 Agent 連同它所有版本都不存在了。
  R1 擔心的「刪到某個 `PUBLISHED` 版本的 digest」在這條路徑上不可能發生。

真正壓倒 R1 的是另一件事：**軟刪讓使用者以為自己刪掉了東西，而那東西還在吃資源。**
一個常駐容器是 2 GB，而這台機器的 Docker 只有不到 2 GB。
「看不到但還在吃資源」比「查不到歷史」糟，而且糟得很難發現。

## 代價（誠實列出）

- **稽核軌跡會一起不見。** SPEC §10 要求「所有 LLM 呼叫 100% 留存於 `llm_calls`」——
  刪掉 Agent 之後那些列也沒了。要保留請在刪除前先匯出。這是使用者按下「確定刪除」
  時買的東西，所以確認視窗必須把它講出來。
- **舊資料的 `image_ref` 會指向不存在的 image。** 但那些 Run 也一起刪了，
  所以實際上沒有東西會去參照它。
- **刪 manifest 不等於釋放空間。** blob 要 `registry garbage-collect` 才真的消失，
  而那要停機做（程序仍見 ADR-0009）。**畫面上不可以說「已釋放空間」。**
- **`--delete-untagged` 的陷阱仍然成立**（ADR-0009 R2-4）。這份 ADR 沒有動 GC 程序。

## 實作上的兩個要點

- **清單要在刪 DB 之前收集。** worker 要停哪些容器、刪哪些 image，都記在 DB 裡；
  刪完就沒有人記得了。所以 API 先查出來、放進 Celery 的 payload，再刪 DB。
- **清理失敗不讓刪除失敗。** Agent 已經不在了，把錯誤丟回去只會讓使用者以為沒刪成功、
  然後再按一次。清理結果寫進 worker 的日誌。
