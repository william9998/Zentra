# 不用 zentra/agent-base，也不用 Python：任何語言照 specs/runtime.md 做就能發布。
#
#   cd examples/node-agent
#   docker build -t localhost:6000/node-agent:1.0.0 .
#   docker push  localhost:6000/node-agent:1.0.0

FROM node:22-alpine

WORKDIR /opt/zentra/app
COPY package.json agent.js zentra-agent.js ./

# 平台固定執行 /opt/zentra/bin/zentra-agent <validate|serve|run>。
RUN mkdir -p /opt/zentra/bin \
 && printf '#!/bin/sh\nexec node /opt/zentra/app/zentra-agent.js "$@"\n' > /opt/zentra/bin/zentra-agent \
 && chmod 755 /opt/zentra/bin/zentra-agent

# 平台靠這個 label 認出這是 Zentra 的單位，少了它會被擋。
LABEL io.zentra.image-contract="zentra.image/v1"

# 平台以 65534 執行、根目錄唯讀，只有 /workspace 可寫。
USER 65534:65534
