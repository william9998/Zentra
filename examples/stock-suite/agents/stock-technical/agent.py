"""stock-technical —— stock-suite 的 tool。邏輯在 stockkit/technical.py，這裡只宣告入口。

build: cd examples/stock-suite && ./build.sh stock-technical
"""

from zentra_sdk import agent

from stockkit import technical
from stockkit.common import envelope_output_schema, security_input_schema
from stockkit.graph import security_graph


@agent(
    name="stock-technical",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "個股技術分析：輸入台股或美股的代碼、名稱與（選填）成本價、股數，計算 MA、RSI、MACD、KD、布林通道、量比、區間報酬與規則訊號。use_llm=true 時另外回傳趨勢判讀與買賣建議",
        "input_schema": security_input_schema(with_position=True),
        "output_schema": envelope_output_schema(technical.TOOL, technical.DATA_SCHEMA, technical.ANALYSIS_SCHEMA),
    }],
)
def build(ctx):
    return security_graph(ctx, technical.TOOL, technical.run)
