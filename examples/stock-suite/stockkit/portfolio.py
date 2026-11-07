"""Tool 5：整體庫存調整建議（portfolio-advisor）。

    對每一檔持股：拿到 news / technical / fundamental / chart
        ├─ 呼叫端在 precomputed 給了 → 直接用（不再抓）
        └─ 沒給 → 在**同一個 process 裡**呼叫 1–4 的 collect()，等同 use_llm=false
    → 算整體部位（市值、權重、損益，美股以 USD/TWD 換算）
    → use_llm=true：分析師 LLM 給配置建議

## 為什麼不是透過 MCP 呼叫 1–4

沙盒的網路對端只有 Gateway（P4），連不到平台自己的 `/mcp`。
1–4 的程式碼就在同一個 image 裡（`stockkit`），直接呼叫函式即可，結果完全相同。

## precomputed

呼叫端（例如 Stock-Insight-Chat 的排程）通常已經對每檔呼叫過 1–4，把那些輸出
原封不動放進來就不用重抓：省錢、省時間、也不會撞到每筆 Run 100 次的 egress 上限。
放進來的輸出會檢查 `tool` 與 `security` 對不對得上，對不上就丟棄並重抓（寫 warning）。
**stock-chart 的輸出請先拿掉 `data.image.base64`**，這裡用不到圖，留著只會讓輸入變大。

## egress 預算

沒有 precomputed 時，一檔台股約 1–2（行情）+ 6（FinMind）次 egress、美股約 1–2 + 1 次，
再加上匯率 1 次。超過 `ctx.config["EGRESS_BUDGET"]`（預設 90，平台硬上限 100）
的那幾檔不抓財報、寫 warning —— 寧可少一塊資料，也不要整筆 Run 因 `RUN_CALL_LIMIT` 失敗。
"""

from __future__ import annotations

import threading
from typing import Any

from . import chart, fundamental, news, technical
from .common import LLM, compact_json, describe, envelope, normalize_security, parallel, position_stats
from .market import MarketData

TOOL = "portfolio-advisor"

PARTS = ("news", "technical", "fundamental", "chart")
_TOOL_OF = {"news": news.TOOL, "technical": technical.TOOL,
            "fundamental": fundamental.TOOL, "chart": chart.TOOL}

_HOLDING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["symbol", "name", "market", "cost", "shares"],
    "properties": {
        "symbol": {"type": "string", "pattern": r"^[A-Za-z0-9.\-]{1,15}$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 60},
        "market": {"type": "string", "enum": ["TW", "US"]},
        "cost": {"type": "number", "minimum": 0, "description": "每股成本價（原幣）"},
        "shares": {"type": "number", "exclusiveMinimum": 0},
    },
}

INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["holdings", "use_llm"],
    "properties": {
        "holdings": {"type": "array", "minItems": 1, "maxItems": 30, "items": _HOLDING_SCHEMA,
                     "description": "所有已買庫存（觀察清單不要放進來）"},
        "use_llm": {"type": "boolean",
                    "description": "true = 由分析師 LLM 給整體配置建議（analysis）；false = 只回彙整後的原始資料與部位統計"},
        "investor_profile": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "risk_tolerance": {"type": "string", "enum": ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]},
                "cash_twd": {"type": "number", "minimum": 0, "description": "可動用現金（台幣）"},
                "notes": {"type": "string", "maxLength": 500},
            },
        },
        "precomputed": {
            "type": "array", "maxItems": 30,
            "description": "呼叫端已經拿到的 1–4 輸出（整份原樣），有給的部分不會重抓",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["symbol", "market"],
                "properties": {
                    "symbol": {"type": "string"}, "market": {"type": "string", "enum": ["TW", "US"]},
                    **{part: {"type": "object", "description": f"{_TOOL_OF[part]} 的完整輸出"} for part in PARTS},
                },
            },
        },
    },
}

ANALYSIS_SCHEMA = {
    "type": "object",
    "required": ["overall_assessment", "risk_level", "allocation", "summary"],
    "properties": {
        "overall_assessment": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "diversification": {"type": "string", "description": "產業、市場、單一持股集中度"},
        "allocation": {
            "type": "array",
            "items": {
                "type": "object", "required": ["symbol", "market", "action", "reason"],
                "properties": {
                    "symbol": {"type": "string"}, "name": {"type": "string"},
                    "market": {"type": "string", "enum": ["TW", "US"]},
                    "current_weight_pct": {"type": ["number", "null"]},
                    "suggested_weight_pct": {"type": ["number", "null"]},
                    "action": {"type": "string", "enum": ["ADD", "HOLD", "TRIM", "EXIT"]},
                    "reason": {"type": "string"},
                },
            },
        },
        "rebalancing_steps": {"type": "array", "items": {"type": "string"}},
        "watch_points": {"type": "array", "items": {"type": "string"}, "description": "接下來要盯的事件與價位"},
        "summary": {"type": "string"},
    },
}

DATA_SCHEMA = {
    "type": "object",
    "required": ["portfolio", "holdings"],
    "properties": {
        "portfolio": {"type": "object"},
        "holdings": {"type": "array", "items": {"type": "object"}},
        "egress_budget": {"type": "object"},
    },
}


class _Budget:
    """這筆 Run 的 egress 預算（跨執行緒共用，所以加鎖）。"""

    def __init__(self, limit: int) -> None:
        self.limit, self.used = limit, 0
        self._lock = threading.Lock()

    def take(self, n: int) -> bool:
        with self._lock:
            if self.used + n > self.limit:
                return False
            self.used += n
            return True


def _match_precomputed(raw: list[dict[str, Any]] | None, security: dict[str, Any],
                       warnings: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for entry in raw or []:
        try:
            sec = normalize_security({"symbol": entry["symbol"], "name": "-", "market": entry["market"]})
        except Exception:                      # noqa: BLE001 - 代碼不合法就當沒給
            continue
        if (sec["market"], sec["symbol"]) != (security["market"], security["symbol"]):
            continue
        for part in PARTS:
            output = entry.get(part)
            if not output:
                continue
            given = output.get("security") or {}
            if output.get("tool") != _TOOL_OF[part] or not isinstance(output.get("data"), dict) or \
                    str(given.get("symbol", "")).upper() != security["symbol"]:
                warnings.append(f"{security['symbol']} 的 precomputed.{part} 不是 {_TOOL_OF[part]} "
                                "對這檔股票的輸出，已改為重抓")
                continue
            found[part] = output
    return found


def _gather_one(ctx: Any, security: dict[str, Any], given: dict[str, dict[str, Any]],
                market: MarketData, budget: _Budget, warnings: list[str]) -> dict[str, Any]:
    """拿齊一檔的四份資料。每份是 {"data", "analysis", "source"}，source 是：
    precomputed（呼叫端給的）· fetched（這裡抓的，analysis 一律 null）·
    derived（chart：沒畫圖，只從 technical 推出收盤價）· missing（抓不到，warnings 有原因）。
    """
    sym = security["symbol"]
    parts: dict[str, dict[str, Any]] = {}
    for part, output in given.items():
        data = dict(output["data"])
        if part == "chart" and isinstance(data.get("image"), dict):
            data["image"] = {"omitted": True}  # 圖對文字 LLM 沒用
        parts[part] = {"data": data, "analysis": output.get("analysis"), "source": "precomputed"}

    def fetched(data: dict[str, Any]) -> dict[str, Any]:
        return {"data": data, "analysis": None, "source": "fetched"}

    def missing(reason: str) -> dict[str, Any]:
        warnings.append(f"{sym} 的 {reason}")
        return {"data": None, "analysis": None, "source": "missing"}

    # 行情：台股可能先試 .TW 再試 .TWO，所以預留 2 次。
    if "technical" not in parts:
        if budget.take(2):
            try:
                parts["technical"] = fetched(technical.collect(ctx, security, market))
            except Exception as exc:          # noqa: BLE001
                parts["technical"] = missing(f"技術面抓取失敗：{describe(exc)}")
        else:
            parts["technical"] = missing("技術面因 egress 預算用完而略過")

    # K 線圖對這裡的價值只在「視覺模型的判讀」（precomputed 的 analysis）。
    # 沒給的話不畫圖：畫了也沒有文字 LLM 能讀，只從 technical 推出收盤價。
    if "chart" not in parts:
        tech = parts["technical"].get("data") or {}
        last_close = (tech.get("indicators") or {}).get("last_close")
        parts["chart"] = {"data": {"last_close": last_close, "image": {"omitted": True}} if tech else None,
                          "analysis": None, "source": "derived" if tech else "missing"}

    if "fundamental" not in parts:
        if budget.take(fundamental.EGRESS_CALLS[security["market"]]):
            try:
                local: list[str] = []
                parts["fundamental"] = fetched(fundamental.collect(ctx, security, local))
                warnings.extend(f"{sym}：{w}" for w in local)
            except Exception as exc:          # noqa: BLE001
                parts["fundamental"] = missing(f"財報抓取失敗：{describe(exc)}")
        else:
            parts["fundamental"] = missing("財報因 egress 預算用完而略過")

    if "news" not in parts:                    # 新聞走 LLM 搜尋，不吃 egress 預算
        try:
            local = []
            parts["news"] = fetched(news.collect(ctx, security, local))
            warnings.extend(f"{sym}：{w}" for w in local)
        except Exception as exc:              # noqa: BLE001
            parts["news"] = missing(f"新聞搜尋失敗：{describe(exc)}")
    return parts


def _last_close(parts: dict[str, Any]) -> float | None:
    for part, path in (("technical", ("indicators", "last_close")), ("chart", ("last_close",))):
        value: Any = (parts.get(part) or {}).get("data")
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, (int, float)):
            return float(value)
    return None


def portfolio_stats(holdings: list[dict[str, Any]], fx: float | None, warnings: list[str],
                    cash_twd: float | None) -> dict[str, Any]:
    """在 holdings 每一項上補 `position`（含 weight_pct），回傳整體統計。"""
    total_twd = 0.0
    total_cost_twd = 0.0
    by_market: dict[str, float] = {"TW": 0.0, "US": 0.0}
    priced = True
    for h in holdings:
        pos = h["position"]
        if not pos:
            priced = False
            continue
        rate = 1.0 if h["market"] == "TW" else fx
        if rate is None:
            priced = False
            continue
        pos["market_value_twd"] = round(pos["market_value"] * rate, 0)
        pos["cost_basis_twd"] = round(pos.get("cost_basis", 0) * rate, 0)
        total_twd += pos["market_value_twd"]
        total_cost_twd += pos["cost_basis_twd"]
        by_market[h["market"]] += pos["market_value_twd"]
    if not priced:
        warnings.append("部分持股沒有價格或匯率，權重只計算有價格的持股")
    denominator = total_twd + (cash_twd or 0)
    for h in holdings:
        pos = h["position"]
        if pos and "market_value_twd" in pos and denominator:
            pos["weight_pct"] = round(pos["market_value_twd"] / denominator * 100, 2)
    return {
        "base_currency": "TWD",
        "usd_twd": fx,
        "total_market_value_twd": round(total_twd, 0),
        "total_cost_twd": round(total_cost_twd, 0),
        "unrealized_pnl_twd": round(total_twd - total_cost_twd, 0),
        "unrealized_pnl_pct": round((total_twd / total_cost_twd - 1) * 100, 2) if total_cost_twd else None,
        "cash_twd": cash_twd,
        "market_split_pct": {m: round(v / denominator * 100, 2) if denominator else None
                             for m, v in by_market.items()},
        "holdings_count": len(holdings),
    }


def _brief(h: dict[str, Any]) -> dict[str, Any]:
    """給分析師 LLM 的精簡版（完整資料在 data 裡，prompt 只放判斷需要的）。"""
    def part(name: str) -> dict[str, Any]:
        return h["parts"].get(name) or {}

    tech = part("technical").get("data") or {}
    fund = part("fundamental").get("data") or {}
    news_data = part("news").get("data") or {}
    company_news = news_data.get("company_news") or {}
    return {
        "symbol": h["symbol"], "name": h["name"], "market": h["market"], "position": h["position"],
        "technical": {"indicators": tech.get("indicators"), "signals": tech.get("signals"),
                      "analysis": part("technical").get("analysis")},
        "chart_analysis": part("chart").get("analysis"),
        "fundamental": {"quarterly": (fund.get("quarterly") or [])[-4:],
                        "monthly_revenue": (fund.get("monthly_revenue") or [])[-3:],
                        "valuation": fund.get("valuation"), "balance_sheet": fund.get("balance_sheet"),
                        "analysis": part("fundamental").get("analysis")},
        "news": {"analysis": part("news").get("analysis"),
                 "recent_headlines": [i.get("title") for i in (company_news.get("last_1m") or [])[:6]],
                 "upcoming_events": (news_data.get("upcoming_events") or [])[:5],
                 "industry": (news_data.get("industry") or {}).get("summary"),
                 "macro": (news_data.get("macro") or {}).get("summary")},
        "missing": [p for p in PARTS if part(p).get("source") == "missing"],
    }


def interpret(llm: LLM, stats: dict[str, Any], holdings: list[dict[str, Any]],
              profile: dict[str, Any] | None) -> dict[str, Any]:
    prompt = (
        "你是資深投資組合分析師。下面是一位投資人的全部持股，每檔附技術面、K 線判讀、基本面、消息面，"
        "以及以台幣計的整體部位。請像分析師一樣評估整體配置，給出具體的調整建議。\n"
        "規則：只能根據給定資料；資料缺漏（missing）要在理由中說明並降低信心；"
        "suggested_weight_pct 加總（含現金）約 100；不要建議資料裡沒有的新個股。\n"
        f"投資人設定：{compact_json(profile or {'risk_tolerance': '未提供'})}\n"
        f"整體部位：{compact_json(stats)}\n"
        f"各持股：{compact_json([_brief(h) for h in holdings], limit=90_000)}\n\n"
        "以繁體中文回傳**一個 JSON 物件**：\n"
        '{"overall_assessment":"整體評估","risk_level":"LOW|MEDIUM|HIGH",'
        '"diversification":"產業/市場/個股集中度",'
        '"allocation":[{"symbol":"","name":"","market":"TW|US","current_weight_pct":數字,'
        '"suggested_weight_pct":數字,"action":"ADD|HOLD|TRIM|EXIT","reason":"理由"}],'
        '"rebalancing_steps":["依序要做的調整"],"watch_points":["接下來要盯的事件與價位"],'
        '"summary":"300 字內"}\n'
        "allocation 必須涵蓋每一檔持股，symbol 與 market 照抄輸入；"
        '現金固定用一列 {"symbol":"CASH","name":"現金","market":"TW"} 表示（action 用 ADD/HOLD/TRIM）。'
    )
    return llm.analyze(prompt)


def run(ctx: Any, state: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    securities = [normalize_security(h) for h in state["holdings"]]
    keys = [(s["market"], s["symbol"]) for s in securities]
    if len(set(keys)) != len(keys):
        raise ValueError("holdings 裡有重複的股票（同市場同代碼），請先合併")

    market = MarketData(ctx)
    budget = _Budget(int(ctx.config.get("EGRESS_BUDGET", 90)))
    needs_fx = any(s["market"] == "US" for s in securities)
    if needs_fx:
        budget.take(1)

    def one(security: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        local: list[str] = []
        given = _match_precomputed(state.get("precomputed"), security, local)
        return _gather_one(ctx, security, given, market, budget, local), local

    results = parallel([lambda s=s: one(s) for s in securities],
                       max_workers=int(ctx.config.get("MAX_PARALLEL", 4)))
    holdings = []
    for security, result in zip(securities, results):
        if isinstance(result, Exception):     # _gather_one 自己會接例外，這裡是保險
            warnings.append(f"{security['symbol']} 整體失敗：{describe(result)}")
            parts: dict[str, Any] = {p: {"data": None, "analysis": None, "source": "missing"} for p in PARTS}
        else:
            parts, local = result
            warnings.extend(local)
        holdings.append({**security, "position": position_stats(security, _last_close(parts)), "parts": parts})

    fx = market.fx_usd_twd() if needs_fx else None
    if needs_fx and fx is None:
        warnings.append("抓不到 USD/TWD 匯率，美股不計入台幣權重")
    profile = state.get("investor_profile")
    stats = portfolio_stats(holdings, fx, warnings, (profile or {}).get("cash_twd"))

    analysis = None
    if state["use_llm"]:
        try:
            analysis = interpret(LLM(ctx), stats, holdings, profile)
        except Exception as exc:              # noqa: BLE001
            warnings.append(f"分析師 LLM 失敗，只回彙整資料：{describe(exc)}")

    data = {
        "portfolio": stats,
        "holdings": holdings,
        "egress_budget": {"limit": budget.limit, "used_estimate": budget.used},
    }
    return envelope(TOOL, data=data, analysis=analysis, warnings=warnings)
