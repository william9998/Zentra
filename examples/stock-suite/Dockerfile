# stock-suite：五個 Agent 共用這一份 Dockerfile，用 AGENT 指定要包哪一個。
#
#   cd examples/stock-suite
#   docker build --build-arg AGENT=stock-technical -t localhost:6000/stock-technical:1.0.0 .
#
# 一般用 ./deploy.sh，它會 build、push（= 註冊）、等發布、設好 egress 規則。
FROM zentra/agent-base:1.0

# 圖表（stock-chart）要用。五個 image 都裝，換來同一份 Dockerfile、同一層 cache。
# mplfinance 用了 six 卻沒宣告依賴，要自己裝。
USER root
RUN pip install --no-cache-dir \
    "pandas==2.2.3" "numpy==2.1.3" "matplotlib==3.9.2" "mplfinance==0.12.10b0" "six==1.17.0"
USER 65534:65534

COPY stockkit /opt/zentra/app/stockkit
ARG AGENT
COPY agents/${AGENT}/agent.py /opt/zentra/app/agent.py
