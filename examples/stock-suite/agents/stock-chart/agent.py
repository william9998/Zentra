"""stock-chart —— stock-suite 的 tool。邏輯在 stockkit/chart.py，這裡只宣告入口。

build: cd examples/stock-suite && ./build.sh stock-chart
"""

from zentra_sdk import agent

from stockkit import chart
from stockkit.common import envelope_output_schema, security_input_schema
from stockkit.graph import security_graph


@agent(
    name="stock-chart",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "個股 K 線圖：輸入台股或美股的代碼、名稱與（選填）成本價、股數，回傳近半年日 K 線圖 PNG（base64，含 MA5/20/60、成交量、成本線）連同輸入資料與持倉損益。use_llm=true 時由視覺模型判讀趨勢、型態、支撐壓力",
        "input_schema": security_input_schema(with_position=True),
        "output_schema": envelope_output_schema(chart.TOOL, chart.DATA_SCHEMA, chart.ANALYSIS_SCHEMA),
    }],
)
def build(ctx):
    return security_graph(ctx, chart.TOOL, chart.run)
