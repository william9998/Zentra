---
name: zentra-drift
description: 稽核 Zentra 實作與 SPEC、API/UI 契約及 ADR 的偏差，輸出有證據的漂移報告；用於規格檢查或交付前稽核。
---

# Zentra 規格漂移稽核

定位專案根目錄，讀 AGENTS.md、specs/SPEC.md、API/UI 契約、相關 ADR 與指定任務交接。
使用 spec-keeper 子 agent；已是該角色時直接執行。
角色讀 .claude/agents/spec-keeper.md；只寫 DRIFT 與自己的交接單，產品檔案唯讀。
未指定稽核範圍則盤點專案；不存在或未實作的模組依 BOARD 與 feature 判定，不假定已完成。

追蹤實際執行路徑以核對 P1–P5、Run 狀態、錯誤码、API 欄位與資料／沙盒邊界。
用 rg 找候選處再讀上下文，搜尋命中不等於違規；文件範例與測試負例不可當成產品行為。
前端是原生 HTML/CSS/ES modules，檢查 api.js／fetch 的契約消費、狀態及設計 token，
不要求 TypeScript/codegen。以實際 schema 檔案為準，不假設必然有 schemas/ 目錄。
ADR 舊正文若有取代註記，遵守目前 SPEC 與已採納決策。

在 coordination/DRIFT.md 追加本次日期、範圍與發現，不覆蓋歷史報告。
每項附檔案行號、違反的規格段落、影響與責任角色；區分確認偏差、疑點及未驗證部分。
寫給 QA／主 session 的交接單，不自行修產品、變更契約或裁定 DONE。
