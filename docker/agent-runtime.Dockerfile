# 共用的沙盒映像 —— 不是 per-agent build（ADR-0005）。
# ZEN-000 階段 agent 寫死在映像裡；ZEN-001 起改成唯讀掛載 /app/code + /app/venv。
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN uv pip install --system "langgraph>=0.2.50" "httpx>=0.28"

# 平台程式碼：runtime（負責 compile 與注入事件 hook）+ SDK（使用者的 ctx）
COPY apps/sandbox/runtime /opt/zentra/zentra_runtime
COPY packages/sdk/zentra_sdk /opt/zentra/zentra_sdk

ENV PYTHONPATH=/opt/zentra PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# L2：非 root。容器建立時還會再加 cap_drop ALL / no-new-privileges（見 docker_driver.py）
USER 65534:65534
CMD ["python", "-m", "zentra_runtime"]
