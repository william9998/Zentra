"""stock-news —— stock-suite 的 tool。邏輯在 stockkit/news.py，這裡只宣告入口。

build: cd examples/stock-suite && ./build.sh stock-news
"""

from zentra_sdk import agent

from stockkit import news
from stockkit.common import envelope_output_schema, security_input_schema
from stockkit.graph import security_graph


@agent(
    name="stock-news",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "個股新聞：輸入台股或美股的代碼與名稱，用模型網路搜尋（三組關鍵字）抓近一個月、近一季、近一年的新聞、即將到來的大事（法說會、除權息、股東會、財報公布），以及所屬產業與總體經濟動態。use_llm=true 時另外回傳消息面統整（情緒、重點、催化劑、風險）",
        "input_schema": security_input_schema(with_position=False),
        "output_schema": envelope_output_schema(news.TOOL, news.DATA_SCHEMA, news.ANALYSIS_SCHEMA),
    }],
)
def build(ctx):
    return security_graph(ctx, news.TOOL, news.run)
