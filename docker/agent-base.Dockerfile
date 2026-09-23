# zentra/agent-base:1.0 —— 使用者的 agent image 以此為基底（ADR-0009 Q-010）
#
#   docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .
#
# 這個 image 把 zentra.image/v1 契約要求的東西都備好，使用者的 Dockerfile
# 只需要三行：FROM、COPY agent.py、（必要時）pip install 自己的依賴。
#
# 刻意不做的（ADR-0009 §2「明確不做」）：
#   不發佈到公開 Registry、不做簽章、不做多架構 build。
#   這是本機 build 的產物 —— 要給別人用就 push 進你自己的 registry。

FROM python:3.12-slim

# SDK 的執行期依賴。版本以 packages/sdk/pyproject.toml 為準，改那邊要一起改這裡。
# 沒有 jsonschema，image_runtime 會在 import 階段就死（Q-009）。
RUN pip install --no-cache-dir \
    "langgraph>=0.2.50" \
    "httpx>=0.28" \
    "jsonschema>=4.23"

# 平台 SDK 與契約要求的啟動器。
# 正式發佈 zentra-sdk 到 PyPI 之後，這兩行會變成 pip install zentra-sdk。
COPY packages/sdk/zentra_sdk /opt/zentra/zentra_sdk
COPY packages/sdk/bin/zentra-agent /opt/zentra/bin/zentra-agent
RUN chmod +x /opt/zentra/bin/zentra-agent

# runtime 固定 import 名為 `agent` 的模組（ADR-0003）。
# 使用者把自己的檔案 COPY 到 /opt/zentra/app/agent.py 即可 —— 檔名可以不同，
# 但模組名必須是 agent。
WORKDIR /opt/zentra/app

ENV PYTHONPATH=/opt/zentra:/opt/zentra/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 平台靠這個 label 認得出這是 Zentra agent image。少了它註冊會被擋下。
LABEL io.zentra.image-contract="zentra.image/v1"

# 契約要求能在 uid/gid 65534、read-only rootfs、無 HOME 下執行。
# 平台建立容器時還會再加 cap_drop ALL、internal 網段、tmpfs /workspace。
USER 65534:65534
