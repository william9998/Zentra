"""Tool 4：財報分析（stock-fundamental）。

| 市場 | 來源 | egress 規則 |
|---|---|---|
| TW | FinMind v4（匿名可用；有 token 額度較高） | `api.finmindtrade.com`，GET，`/api/v4/data` |
| US | Yahoo fundamentals-timeseries（不需 key） | `query2.finance.yahoo.com`，GET，`/ws/fundamentals-timeseries/` |

FinMind token（選用）：在 Agent 機密存 `FINMIND_TOKEN`，**值要包含 `Bearer ` 前綴**
（平台把機密原值塞進 header），規則設 `auth_secret_name=FINMIND_TOKEN`、`auth_header=Authorization`。

不用 SEC EDGAR：companyfacts 單檔 3.8 MB 超過 egress 1 MiB 上限，
查 CIK 的 www.sec.gov 又會擋平台固定的 User-Agent。

一筆台股約 6 次 egress、美股 1 次（portfolio-advisor 估算上限時用到）。
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from .common import LLM, compact_json, describe, envelope, parallel

TOOL = "stock-fundamental"

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
YAHOO_TS_URL = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"

EGRESS_CALLS = {"TW": 6, "US": 1}


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round((a / b - 1) * 100, 2)


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round(a / b * 100, 2)


# ── 台股：FinMind ─────────────────────────────────────────────────────


def _finmind(ctx: Any, dataset: str, stock_id: str, start: date) -> list[dict[str, Any]]:
    query = urlencode({"dataset": dataset, "data_id": stock_id, "start_date": start.isoformat()})
    response = ctx.http.get(f"{FINMIND_URL}?{query}", headers={"Accept": "application/json"},
                            timeout_seconds=15)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") not in (200, None):
        raise RuntimeError(f"FinMind {dataset}: {payload.get('msg')}")
    return payload.get("data") or []


def _pivot(rows: list[dict[str, Any]], wanted: set[str]) -> dict[str, dict[str, float]]:
    """FinMind 的長表（date, type, value）→ {date: {type: value}}。"""
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        if row.get("type") in wanted:
            out.setdefault(row["date"], {})[row["type"]] = row.get("value")
    return out


def _tw_quarters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = _pivot(rows, {"Revenue", "GrossProfit", "OperatingIncome", "IncomeAfterTaxes", "EPS"})
    periods = sorted(table)
    quarters = []
    for i, period in enumerate(periods):
        q = table[period]
        prev = table[periods[i - 4]] if i >= 4 else {}
        revenue = q.get("Revenue")
        quarters.append({
            "period_end": period,
            "revenue": revenue,
            "gross_profit": q.get("GrossProfit"),
            "operating_income": q.get("OperatingIncome"),
            "net_income": q.get("IncomeAfterTaxes"),
            "eps": q.get("EPS"),
            "gross_margin_pct": _ratio(q.get("GrossProfit"), revenue),
            "operating_margin_pct": _ratio(q.get("OperatingIncome"), revenue),
            "net_margin_pct": _ratio(q.get("IncomeAfterTaxes"), revenue),
            "revenue_yoy_pct": _pct(revenue, prev.get("Revenue")),
            "eps_yoy_pct": _pct(q.get("EPS"), prev.get("EPS")),
        })
    return quarters[-8:]


def _tw_monthly(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_month = {(r["revenue_year"], r["revenue_month"]): r["revenue"] for r in rows if r.get("revenue")}
    out = []
    for (year, month) in sorted(by_month):
        revenue = by_month[(year, month)]
        prev_month = by_month.get((year, month - 1) if month > 1 else (year - 1, 12))
        out.append({"month": f"{year}-{month:02d}", "revenue": revenue,
                    "mom_pct": _pct(revenue, prev_month),
                    "yoy_pct": _pct(revenue, by_month.get((year - 1, month)))})
    return out[-13:]


def _tw_balance(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    table = _pivot(rows, {"TotalAssets", "Liabilities", "Equity", "CurrentAssets",
                          "CurrentLiabilities", "CashAndCashEquivalents"})
    if not table:
        return None
    period = max(table)
    b = table[period]
    return {
        "period_end": period,
        "total_assets": b.get("TotalAssets"), "total_liabilities": b.get("Liabilities"),
        "equity": b.get("Equity"), "cash": b.get("CashAndCashEquivalents"),
        "debt_ratio_pct": _ratio(b.get("Liabilities"), b.get("TotalAssets")),
        "current_ratio_pct": _ratio(b.get("CurrentAssets"), b.get("CurrentLiabilities")),
    }


def _tw_cashflow(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = _pivot(rows, {"CashFlowsFromOperatingActivities", "PropertyAndPlantAndEquipment"})
    out = []
    for period in sorted(table)[-4:]:
        c = table[period]
        ocf, capex = c.get("CashFlowsFromOperatingActivities"), c.get("PropertyAndPlantAndEquipment")
        out.append({"period_end": period, "operating_cash_flow_ytd": ocf, "capex_ytd": capex,
                    "free_cash_flow_ytd": (ocf + capex) if ocf is not None and capex is not None else None})
    return out


def _tw(ctx: Any, security: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    sid, today = security["symbol"], date.today()
    two_years = today - timedelta(days=760)
    jobs = {
        "statements": lambda: _finmind(ctx, "TaiwanStockFinancialStatements", sid, two_years),
        # 資產負債表每季上千列（~170 KB/年），只抓最近約三季就夠拿最新一期。
        "balance": lambda: _finmind(ctx, "TaiwanStockBalanceSheet", sid, today - timedelta(days=300)),
        "cashflow": lambda: _finmind(ctx, "TaiwanStockCashFlowsStatement", sid, today - timedelta(days=400)),
        "monthly": lambda: _finmind(ctx, "TaiwanStockMonthRevenue", sid, today - timedelta(days=800)),
        "per": lambda: _finmind(ctx, "TaiwanStockPER", sid, today - timedelta(days=14)),
        "dividend": lambda: _finmind(ctx, "TaiwanStockDividend", sid, today - timedelta(days=1100)),
    }
    results = dict(zip(jobs, parallel(list(jobs.values()), max_workers=3)))
    for name, value in results.items():
        if isinstance(value, Exception):
            warnings.append(f"FinMind {name} 抓取失敗：{describe(value)}")
            results[name] = []

    per = results["per"][-1] if results["per"] else {}
    dividends = [{
        "year": d.get("year"), "cash_dividend": d.get("CashEarningsDistribution"),
        "stock_dividend": d.get("StockEarningsDistribution"),
        "ex_dividend_date": d.get("CashExDividendTradingDate") or None,
        "payment_date": d.get("CashDividendPaymentDate") or None,
    } for d in results["dividend"][-8:]]
    data = {
        "source": "FinMind",
        "currency": "TWD",
        "quarterly": _tw_quarters(results["statements"]),
        "monthly_revenue": _tw_monthly(results["monthly"]),
        "balance_sheet": _tw_balance(results["balance"]),
        "cash_flow_ytd": _tw_cashflow(results["cashflow"]),
        "valuation": {"pe": per.get("PER"), "pb": per.get("PBR"),
                      "dividend_yield_pct": per.get("dividend_yield"), "as_of": per.get("date")},
        "dividends": dividends,
        "notes": ["台股季報的現金流量是年初至今累計（_ytd），不是單季"],
    }
    if not data["quarterly"]:
        warnings.append("FinMind 查無季報（ETF、新上市或代碼錯誤）")
    return data


# ── 美股：Yahoo fundamentals-timeseries ───────────────────────────────

_US_QUARTERLY = ["TotalRevenue", "GrossProfit", "OperatingIncome", "NetIncome", "DilutedEPS",
                 "FreeCashFlow", "OperatingCashFlow", "TotalDebt", "StockholdersEquity",
                 "CashAndCashEquivalents", "TotalAssets"]
_US_TRAILING = ["trailingPeRatio", "trailingForwardPeRatio", "trailingPbRatio",
                "trailingPsRatio", "trailingMarketCap"]
_SNAKE = {"TotalRevenue": "revenue", "GrossProfit": "gross_profit", "OperatingIncome": "operating_income",
          "NetIncome": "net_income", "DilutedEPS": "eps", "FreeCashFlow": "free_cash_flow",
          "OperatingCashFlow": "operating_cash_flow", "TotalDebt": "total_debt",
          "StockholdersEquity": "equity", "CashAndCashEquivalents": "cash", "TotalAssets": "total_assets"}


def _us(ctx: Any, security: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    types = [f"quarterly{t}" for t in _US_QUARTERLY] + [f"annual{t}" for t in
                                                         ("TotalRevenue", "NetIncome", "DilutedEPS")] + _US_TRAILING
    now = int(time.time())
    query = urlencode({"type": ",".join(types), "period1": now - 3 * 366 * 86400, "period2": now})
    symbol = security["symbol"].replace(".", "-")
    response = ctx.http.get(YAHOO_TS_URL.format(symbol=symbol) + "?" + query,
                            headers={"Accept": "application/json"}, timeout_seconds=15)
    response.raise_for_status()
    series: dict[str, list[tuple[str, float]]] = {}
    for result in (response.json().get("timeseries") or {}).get("result") or []:
        name = (result.get("meta") or {}).get("type", [None])[0]
        points = []
        for point in result.get(name) or []:
            if point and point.get("reportedValue"):
                points.append((point["asOfDate"], point["reportedValue"]["raw"]))
        if name:
            series[name] = sorted(points)

    periods = sorted({d for t in _US_QUARTERLY for d, _ in series.get(f"quarterly{t}", [])})
    quarterly = []
    for period in periods[-8:]:
        row: dict[str, Any] = {"period_end": period}
        for t in _US_QUARTERLY:
            row[_SNAKE[t]] = dict(series.get(f"quarterly{t}", [])).get(period)
        row["gross_margin_pct"] = _ratio(row["gross_profit"], row["revenue"])
        row["operating_margin_pct"] = _ratio(row["operating_income"], row["revenue"])
        row["net_margin_pct"] = _ratio(row["net_income"], row["revenue"])
        quarterly.append(row)
    annual = []
    for period in sorted({d for d, _ in series.get("annualTotalRevenue", [])}):
        annual.append({"period_end": period,
                       "revenue": dict(series.get("annualTotalRevenue", [])).get(period),
                       "net_income": dict(series.get("annualNetIncome", [])).get(period),
                       "eps": dict(series.get("annualDilutedEPS", [])).get(period)})
    for i in range(1, len(annual)):
        annual[i]["revenue_yoy_pct"] = _pct(annual[i]["revenue"], annual[i - 1]["revenue"])
        annual[i]["eps_yoy_pct"] = _pct(annual[i]["eps"], annual[i - 1]["eps"])

    def latest(name: str) -> float | None:
        points = series.get(name) or []
        return round(points[-1][1], 4) if points else None

    latest_q = quarterly[-1] if quarterly else {}
    data = {
        "source": "Yahoo Finance fundamentals-timeseries",
        "currency": "USD",
        "quarterly": quarterly,
        "annual": annual,
        "balance_sheet": {
            "period_end": latest_q.get("period_end"), "total_assets": latest_q.get("total_assets"),
            "total_debt": latest_q.get("total_debt"), "equity": latest_q.get("equity"),
            "cash": latest_q.get("cash"),
            "debt_to_equity_pct": _ratio(latest_q.get("total_debt"), latest_q.get("equity")),
        } if quarterly else None,
        "valuation": {"pe": latest("trailingPeRatio"), "forward_pe": latest("trailingForwardPeRatio"),
                      "pb": latest("trailingPbRatio"), "ps": latest("trailingPsRatio"),
                      "market_cap": latest("trailingMarketCap")},
        "notes": ["Yahoo 只提供最近約 5 季的季報；EPS 偶爾比營收晚一季更新"],
    }
    if not quarterly:
        warnings.append("Yahoo 查無季報（ETF、ADR 或代碼錯誤）")
    return data


# ── tool ──────────────────────────────────────────────────────────────

ANALYSIS_SCHEMA = {
    "type": "object",
    "required": ["rating", "summary"],
    "properties": {
        "rating": {"type": "string", "enum": ["STRONG", "SOLID", "MIXED", "WEAK", "INSUFFICIENT_DATA"]},
        "growth": {"type": "string"},
        "profitability": {"type": "string"},
        "financial_health": {"type": "string"},
        "valuation": {"type": "string"},
        "dividend": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "position_advice": {"type": "string"},
        "summary": {"type": "string"},
    },
}

DATA_SCHEMA = {
    "type": "object",
    "required": ["source", "quarterly", "valuation"],
    "properties": {
        "source": {"type": "string"},
        "currency": {"type": "string"},
        "quarterly": {"type": "array", "items": {"type": "object"}},
        "annual": {"type": "array", "items": {"type": "object"}},
        "monthly_revenue": {"type": "array", "items": {"type": "object"}},
        "balance_sheet": {"type": ["object", "null"]},
        "cash_flow_ytd": {"type": "array", "items": {"type": "object"}},
        "valuation": {"type": "object"},
        "dividends": {"type": "array", "items": {"type": "object"}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


def collect(ctx: Any, security: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    return _tw(ctx, security, warnings) if security["market"] == "TW" else _us(ctx, security, warnings)


def interpret(llm: LLM, security: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    position = {k: security[k] for k in ("cost", "shares") if k in security}
    prompt = (
        "你是基本面分析師。根據下面的財報數字評估這家公司。只能使用給定數字，不足就說不足。\n"
        f"個股：{security['name']}（{security['market']} {security['symbol']}），持倉：{position or '無（觀察清單）'}\n"
        f"財報資料：{compact_json(data)}\n\n"
        "以繁體中文回傳**一個 JSON 物件**：\n"
        '{"rating":"STRONG|SOLID|MIXED|WEAK|INSUFFICIENT_DATA","growth":"營收與獲利成長",'
        '"profitability":"毛利率/營益率/淨利率趨勢","financial_health":"負債、現金、現金流",'
        '"valuation":"本益比、股價淨值比是否偏貴","dividend":"配息狀況（沒有就寫無資料）",'
        '"risks":["風險"],"position_advice":"有持倉時從基本面角度的續抱/加碼/減碼看法",'
        '"summary":"200 字內總結"}'
    )
    return llm.analyze(prompt)


def run(ctx: Any, security: dict[str, Any], use_llm: bool) -> dict[str, Any]:
    warnings: list[str] = []
    data = collect(ctx, security, warnings)
    analysis = None
    if use_llm:
        try:
            analysis = interpret(LLM(ctx), security, data)
        except Exception as exc:              # noqa: BLE001
            warnings.append(f"LLM 解讀失敗，只回原始資料：{describe(exc)}")
    return envelope(TOOL, data=data, analysis=analysis, warnings=warnings, security=security)
