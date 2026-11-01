# 平台的測試 Agent。寫法和使用者的 Agent 一模一樣：base image + agent.py。
#
# 通常不用手動 build —— `make fixture` 會 build、push（push 就是註冊）並等它發布。
# 手動的話，build context 是**這個目錄**：
#   docker build -t localhost:6000/probe-agent:1.0.0 examples/probe-agent
#   docker push localhost:6000/probe-agent:1.0.0

FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
