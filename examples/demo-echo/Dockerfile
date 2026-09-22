# zentra.image/v1 契約的最小示範映像（ZEN-008 spec §6）。
#
# **build context 是 repo 根目錄**，因為要把 SDK 複製進去：
#   docker build -f examples/demo-echo/Dockerfile -t <registry>/demo-echo:1.0.0 .
#
# 正式使用時你的 image 不會長這樣 —— 你會從 PyPI 安裝 zentra_sdk，
# 而不是從 repo 複製。這裡直接複製是因為 SDK 還沒發佈。

FROM python:3.12-slim

# jsonschema 是 SDK 的 image_runtime 需要的（驗證 entrypoint schema），
# 少了它驗證容器會在 import 階段就死。契約 §6 沒列出來，這裡明寫。
RUN pip install --no-cache-dir \
    "langgraph>=0.2.50" \
    "httpx>=0.28" \
    "jsonschema>=4.23"

# 平台 SDK 與契約要求的啟動器
COPY packages/sdk/zentra_sdk /opt/zentra/zentra_sdk
COPY packages/sdk/bin/zentra-agent /opt/zentra/bin/zentra-agent
RUN chmod +x /opt/zentra/bin/zentra-agent

# 你的 agent 程式碼。runtime 固定 import 名為 `agent` 的模組（ADR-0003），
# 檔名與路徑可以不同，但模組名必須是 agent。
COPY examples/demo-echo/agent.py /opt/zentra/app/agent.py

ENV PYTHONPATH=/opt/zentra:/opt/zentra/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 平台靠這個 label 認得出這是 Zentra agent image
LABEL io.zentra.image-contract="zentra.image/v1"

# 契約要求能在 uid/gid 65534、read-only rootfs、無 HOME 下執行。
# 平台建立容器時還會再加 cap_drop ALL、internal 網段、tmpfs /workspace。
USER 65534:65534
