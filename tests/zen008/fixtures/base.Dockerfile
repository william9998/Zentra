# QA fixture 的共用底層：語言執行環境 + zentra_sdk + zentra.image/v1 可執行檔。
# 刻意疊在 zentra/agent-runtime:dev 上，只是為了不在驗收時重裝 langgraph；
# 產品程式碼不依賴這個 image，使用者也可以用任何 base 只要滿足 v1 契約。
FROM zentra/agent-runtime:dev
USER root
RUN uv pip install --system "jsonschema>=4.20"
COPY zentra_sdk /opt/zentra/zentra_sdk
COPY zentra-agent /opt/zentra/bin/zentra-agent
RUN chmod 0555 /opt/zentra/bin/zentra-agent && mkdir -p /app
ENV PYTHONPATH=/opt/zentra:/app
USER 65534:65534
