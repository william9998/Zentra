"""stock-suite 五個 Agent 共用的程式碼。

每個 Agent 的 agent.py 只負責宣告 entrypoint 與包成一張 graph；
真正的邏輯都在這裡，portfolio-advisor 也直接呼叫同一批函式（不經過 MCP）。
"""
