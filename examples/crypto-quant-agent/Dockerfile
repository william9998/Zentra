# crypto-quant-agent —— ZEN-019 的驗收載體。
#
#   cd examples/crypto-quant-agent
#   docker build -t localhost:6000/crypto-quant-agent:1.0.0 .
#   docker push localhost:6000/crypto-quant-agent:1.0.0
#
# 然後在 Console 用 digest 註冊版本（平台不接收 image 檔案上傳，ADR-0009）。
FROM zentra/agent-base:1.0

# 五支都要 COPY。base image 的 WORKDIR 是 /opt/zentra/app 且 PYTHONPATH 含它，
# 所以 `import indicators` 這種平放的 import 可以用。
# **少 COPY 一支的症狀是 validation 時 ModuleNotFoundError**，
# 而錯誤訊息只會說「沒有那個模組」，不會說是你忘了 COPY。
COPY agent.py /opt/zentra/app/agent.py
COPY signals.py /opt/zentra/app/signals.py
COPY indicators.py /opt/zentra/app/indicators.py
COPY harmonics.py /opt/zentra/app/harmonics.py
COPY marketdata.py /opt/zentra/app/marketdata.py

# 沒有額外的 pip install：四支運算模組是純 Python 標準函式庫，
# 而 HTTP 由平台代打（ctx.http），不需要 requests 或任何交易所 SDK。
