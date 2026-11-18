# 平台的測試 Agent。寫法和使用者的 Agent 一模一樣：base image + agent.py。
#
# build context 是**這個目錄**（push 就是註冊）：
#   docker build -t localhost:6000/probe-agent:1.1.0 examples/probe-agent
#   docker push localhost:6000/probe-agent:1.1.0

FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
