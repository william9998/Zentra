"""stock-fundamental —— stock-suite 的 tool。邏輯在 stockkit/fundamental.py，這裡只宣告入口。

build: cd examples/stock-suite && ./build.sh stock-fundamental
"""

from zentra_sdk import agent

from stockkit import fundamental
from stockkit.common import envelope_output_schema, security_input_schema
from stockkit.graph import security_graph


@agent(
    name="stock-fundamental",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "個股財報分析：輸入台股或美股的代碼、名稱與（選填）成本價、股數，回傳近 8 季損益與三率、月營收（台股）、資產負債、現金流、本益比/股價淨值比、配息。use_llm=true 時另外回傳基本面評等與分析",
        "input_schema": security_input_schema(with_position=True),
        "output_schema": envelope_output_schema(fundamental.TOOL, fundamental.DATA_SCHEMA, fundamental.ANALYSIS_SCHEMA),
    }],
)
def build(ctx):
    return security_graph(ctx, fundamental.TOOL, fundamental.run)
