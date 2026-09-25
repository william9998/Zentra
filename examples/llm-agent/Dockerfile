# 跟不打 LLM 的 agent 完全一樣的兩行 —— base image 已經有 langchain-openai。
#
#   cd examples/llm-agent
#   docker build -t localhost:6000/llm-agent:1.0.0 .

FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
