"""portfolio-advisor —— stock-suite 的 tool。邏輯在 stockkit/portfolio.py，這裡只宣告入口。

build: cd examples/stock-suite && ./build.sh portfolio-advisor
"""

from zentra_sdk import agent

from stockkit import portfolio
from stockkit.common import envelope_output_schema
from stockkit.graph import portfolio_graph


@agent(
    name="portfolio-advisor",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "整體庫存調整建議：輸入所有已買庫存（代碼、名稱、台股/美股、成本價、股數），"
                       "內部對每檔取得新聞、技術面、財報（呼叫端可用 precomputed 帶入已有的結果），"
                       "計算台幣市值、權重與損益。use_llm=true 時由分析師 LLM 給出配置建議"
                       "（每檔 ADD/HOLD/TRIM/EXIT、建議權重、調整步驟）。持股多時很慢，建議以 _async 呼叫",
        "input_schema": portfolio.INPUT_SCHEMA,
        "output_schema": envelope_output_schema(portfolio.TOOL, portfolio.DATA_SCHEMA,
                                                portfolio.ANALYSIS_SCHEMA, with_security=False),
    }],
)
def build(ctx):
    return portfolio_graph(ctx, portfolio.TOOL, portfolio.run)
