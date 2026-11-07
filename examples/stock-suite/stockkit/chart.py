"""Tool 2：K 線圖（stock-chart）。

## 為什麼不是「截圖」

沙盒沒有對外網路、也沒有瀏覽器，打不開看盤網站去截圖（P4）。
改成：經 egress 抓日線 → 在容器裡用 mplfinance 畫 → PNG base64 回傳。
內容就是一張 K 線圖（含均線、成交量、成本線），而且不怕網站改版。

`use_llm=true` 時把這張圖交給視覺模型看（`VISION_MODEL`，沒設就用預設 model），
回傳型態判讀。

## matplotlib 在唯讀檔案系統

image 的根目錄是唯讀的，只有 `/workspace` 可寫。matplotlib 啟動時要寫字型快取，
所以 import 之前把 `MPLCONFIGDIR` 指到 `/workspace`。**import 放在函式裡**，
其他四個 Agent 不會被拖慢、也不會因為這裡出錯而驗證失敗。
"""

from __future__ import annotations

import base64
import io
import os
from typing import Any

from .common import LLM, compact_json, envelope, position_stats
from .market import MarketData

TOOL = "stock-chart"

#: 畫幾根 K 棒。半年日線看得清楚又有趨勢；MA60 需要的前置資料另外多抓。
DEFAULT_BARS = 120


def render_png(bars: list[dict[str, Any]], *, title: str, cost: float | None,
               show_bars: int = DEFAULT_BARS) -> tuple[str, int, int]:
    """回 (base64 PNG, 寬, 高)。"""
    os.environ.setdefault("MPLCONFIGDIR", "/workspace/.mplconfig" if os.path.isdir("/workspace")
                          else os.path.join(os.getcwd(), ".mplconfig"))
    import matplotlib
    matplotlib.use("Agg")
    import mplfinance as mpf
    import pandas as pd

    frame = pd.DataFrame(bars)
    frame.index = pd.DatetimeIndex(frame.pop("date"))
    frame = frame.rename(columns=str.capitalize)       # mplfinance 要 Open/High/Low/Close/Volume
    for n, color in ((5, "#f5a623"), (20, "#4a90e2"), (60, "#9b59b6")):
        frame[f"MA{n}"] = frame["Close"].rolling(n).mean()
    view = frame.tail(show_bars)

    # 台股慣例紅漲綠跌；美股反過來。這張圖兩邊都用，選台股慣例並在圖例寫清楚。
    colors = mpf.make_marketcolors(up="#e74c3c", down="#27ae60", inherit=True)
    style = mpf.make_mpf_style(marketcolors=colors, gridstyle=":", y_on_right=True)
    addplots = [mpf.make_addplot(view[f"MA{n}"], color=c, width=1.0)
                for n, c in ((5, "#f5a623"), (20, "#4a90e2"), (60, "#9b59b6"))
                if view[f"MA{n}"].notna().any()]
    kwargs: dict[str, Any] = {}
    if cost:
        low, high = float(view["Low"].min()), float(view["High"].max())
        span = high - low
        # mplfinance 不會為了 hlines 擴大 Y 軸。成本離區間不遠就把它納入；
        # 太遠（例如十年前買的）硬納入會把 K 線壓成一條線，改在標題註明。
        if low - span * 0.5 <= cost <= high + span * 0.5:
            kwargs["hlines"] = {"hlines": [cost], "colors": ["#555555"], "linestyle": "--", "linewidths": 1}
            pad = span * 0.03
            kwargs["ylim"] = (min(low, cost) - pad, max(high, cost) + pad)
            title += f"  cost {cost:g}"
        else:
            title += f"  cost {cost:g} ({'below' if cost < low else 'above'} chart range)"

    buffer = io.BytesIO()
    width, height, dpi = 10, 6, 100
    # 標題只用 ASCII：容器裡沒有 CJK 字型，中文會變成方框。
    mpf.plot(view, type="candle", style=style, volume=True, addplot=addplots or None,
             title=title, ylabel="Price", ylabel_lower="Volume",
             figsize=(width, height), tight_layout=True,
             savefig={"fname": buffer, "format": "png", "dpi": dpi}, **kwargs)
    return base64.b64encode(buffer.getvalue()).decode("ascii"), width * dpi, height * dpi


ANALYSIS_SCHEMA = {
    "type": "object",
    "required": ["trend", "summary"],
    "properties": {
        "trend": {"type": "string", "enum": ["UPTREND", "DOWNTREND", "SIDEWAYS"]},
        "patterns": {"type": "array", "items": {"type": "string"},
                     "description": "看到的型態，例如頭肩頂、W 底、三角收斂"},
        "support_levels": {"type": "array", "items": {"type": "number"}},
        "resistance_levels": {"type": "array", "items": {"type": "number"}},
        "cost_line_comment": {"type": "string", "description": "成本線相對於股價的位置與意義（有成本才有）"},
        "summary": {"type": "string"},
    },
}

DATA_SCHEMA = {
    "type": "object",
    "required": ["image", "period", "last_close"],
    "properties": {
        "image": {
            "type": "object", "required": ["mime_type", "base64"],
            "properties": {"mime_type": {"type": "string", "const": "image/png"},
                           "base64": {"type": "string"},
                           "width": {"type": "integer"}, "height": {"type": "integer"}},
        },
        "yahoo_symbol": {"type": "string"},
        "currency": {"type": ["string", "null"]},
        "period": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"},
                                                    "bars": {"type": "integer"}}},
        "last_close": {"type": "number"},
        "legend": {"type": "string"},
        "position": {"type": ["object", "null"]},
    },
}


def collect(ctx: Any, security: dict[str, Any], market: MarketData) -> dict[str, Any]:
    # 跟 technical 抓同一個區間：portfolio-advisor 裡兩者共用 MarketData 快取，只打一次 egress。
    daily = market.daily(security, "2y")
    bars = daily["bars"]
    show = bars[-DEFAULT_BARS:]
    title = f"{daily['yahoo_symbol']}  daily  {show[0]['date']} ~ {show[-1]['date']}"
    png, width, height = render_png(bars, title=title, cost=security.get("cost"))
    return {
        "image": {"mime_type": "image/png", "base64": png, "width": width, "height": height},
        "yahoo_symbol": daily["yahoo_symbol"],
        "currency": daily["currency"],
        "period": {"start": show[0]["date"], "end": show[-1]["date"], "bars": len(show)},
        "last_close": round(bars[-1]["close"], 4),
        "legend": "紅 K 漲、綠 K 跌（台股慣例）；橘 MA5、藍 MA20、紫 MA60；灰色虛線 = 成本價；下方為成交量",
        "position": position_stats(security, bars[-1]["close"]),
    }


def interpret(llm: LLM, security: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    facts = {k: v for k, v in data.items() if k != "image"}
    prompt = (
        "你是技術分析師，請看這張日 K 線圖做型態判讀。\n"
        f"個股：{security['name']}（{security['market']} {security['symbol']}）\n"
        f"圖的說明與數字：{compact_json(facts)}\n\n"
        "以繁體中文回傳**一個 JSON 物件**：\n"
        '{"trend":"UPTREND|DOWNTREND|SIDEWAYS","patterns":["型態"],'
        '"support_levels":[數字],"resistance_levels":[數字],'
        '"cost_line_comment":"若有成本線，說明股價相對成本的位置；沒有就空字串",'
        '"summary":"150 字內"}'
    )
    return llm.look(prompt, data["image"]["base64"])


def run(ctx: Any, security: dict[str, Any], use_llm: bool) -> dict[str, Any]:
    data = collect(ctx, security, MarketData(ctx))
    warnings: list[str] = []
    analysis = None
    if use_llm:
        try:
            analysis = interpret(LLM(ctx), security, data)
        except Exception as exc:              # noqa: BLE001
            warnings.append(f"LLM 看圖失敗，只回圖片：{type(exc).__name__}: {exc}"[:300])
    return envelope(TOOL, data=data, analysis=analysis, warnings=warnings, security=security)
