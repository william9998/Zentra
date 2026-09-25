# 你自己的 Agent image。只要兩行 —— base image 已經備好 SDK、相依套件、
# 平台契約要求的 label 與使用者權限。
#
# build context 是**這個目錄**，不是 repo 根目錄：
#   cd examples/my-agent
#   docker build -t localhost:6000/my-agent:1.0.0 .
#
# 模組名必須是 agent（ADR-0003）；檔名可以不同，但放進去之後要叫 agent.py。

FROM zentra/agent-base:1.0
COPY agent.py /opt/zentra/app/agent.py
