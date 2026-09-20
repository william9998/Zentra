"""Agent Runtime —— **在沙盒內**執行的平台程式碼。

它被烘進 zentra/agent-runtime 映像，`api` 與 `worker` 永遠不 import 它（P1）。
職責：跟 Internal Gateway 取 input → 取得使用者的 StateGraph → **平台 compile 並注入事件 hook**
→ 逐節點執行 → 回寫事件與結果。
"""
