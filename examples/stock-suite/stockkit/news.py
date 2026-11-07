"""Tool 1：個股新聞（stock-news）。

## 資料從哪來

沙盒不能上網，新聞由**模型的網路搜尋**取得（Gateway 代理 Responses API 的
`web_search` tool，ZEN-013）。不需要 egress 規則。

每次呼叫換 **三組關鍵字**，各打一次搜尋（三次同時進行）：

| # | 查什麼 | 進到 data 的哪裡 |
|---|---|---|
| 1 | 公司本身近一年的新聞 | `company_news.last_1m / last_3m / last_12m`（依日期由程式分桶，不信模型的分類） |
| 2 | 即將到來的大事：法說會、除權息、股東會、財報公布 | `upcoming_events` |
| 3 | 所屬產業動態 + 總體經濟 | `industry`、`macro` |

某一組搜尋失敗只寫 warning，不讓整筆 Run 失敗；**三組全失敗才失敗** ——
那代表 model 不支援搜尋，回空資料會被誤讀成「這檔股票沒新聞」。

## use_llm

搜尋本身一定用到 LLM（沒有別的辦法上網）。`use_llm` 控制的是之後**要不要再請 LLM
統整**成 `analysis`（情緒、重點、催化劑、風險）。false 時只回搜到的條目。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .common import LLM, compact_json, describe, envelope, json_from, parallel

TOOL = "stock-news"

_ITEM_FIELDS = '{"title":"標題","date":"YYYY-MM-DD","source":"媒體","url":"網址","summary":"一兩句重點","sentiment":"POSITIVE|NEGATIVE|NEUTRAL"}'


def queries(security: dict[str, Any], today: date) -> dict[str, str]:
    """三組關鍵字的 prompt。台股用中文關鍵字、美股用英文關鍵字（搜得到的來源差很多）。"""
    name, symbol = security["name"], security["symbol"]
    year = today.year
    since = (today - timedelta(days=365)).isoformat()
    if security["market"] == "TW":
        company_kw = f"「{name} {symbol} 新聞」「{name} 營收」「{name} 法人 目標價」"
        events_kw = f"「{name} 法說會 {year}」「{name} 除權息 {year}」「{name} 股東會」「{name} 財報 公布日」"
        industry_kw = f"「{name} 所屬產業 趨勢」「台灣 總體經濟 央行 利率 匯率 出口」「美國 聯準會 利率 通膨」"
        lang = "繁體中文"
    else:
        company_kw = f'"{symbol} stock news", "{name} earnings", "{symbol} analyst price target"'
        events_kw = f'"{symbol} earnings date {year}", "{symbol} ex-dividend date", "{name} investor day"'
        industry_kw = f'"{name} industry outlook", "US economy Fed rates inflation jobs", "S&P 500 market outlook"'
        lang = "繁體中文（專有名詞可保留英文）"
    rule = (f"今天是 {today.isoformat()}。只收錄有明確日期、你在搜尋結果裡真的看到的資料，"
            "不要憑記憶補，找不到就回空陣列。只回傳一個 JSON 物件，不要其他文字。"
            f"summary 用{lang}。")
    return {
        "company": (
            f"上網搜尋 {name}（{security['market']} {symbol}）在 {since} 之後的新聞，"
            f"至少用這些關鍵字各搜一次：{company_kw}。盡量涵蓋近一個月、近一季、近一年的重要事件。\n"
            f"{rule}\n格式：{{\"items\":[{_ITEM_FIELDS}]}}，最多 20 則，新的在前。"
        ),
        "events": (
            f"上網搜尋 {name}（{security['market']} {symbol}）**今天以後**即將發生的大事，"
            f"關鍵字：{events_kw}。\n{rule}\n"
            '格式：{"events":[{"event":"事件","date":"YYYY-MM-DD 或 YYYY-MM（只知道月份時）",'
            '"type":"EARNINGS_CALL|EARNINGS_RELEASE|DIVIDEND|SHAREHOLDER_MEETING|PRODUCT|OTHER",'
            '"confirmed":true/false,"detail":"金額、地點等細節","source":"媒體","url":"網址"}]}'
        ),
        "industry": (
            f"上網搜尋 {name}（{security['market']} {symbol}）所屬產業的近期動態，以及影響它的總體經濟情勢，"
            f"關鍵字：{industry_kw}。\n{rule}\n"
            '格式：{"industry":{"name":"產業名稱","summary":"產業近況 100 字內","items":[' + _ITEM_FIELDS + ']},'
            '"macro":{"summary":"總經近況 100 字內","items":[' + _ITEM_FIELDS + ']}}'
        ),
    }


def _date_of(value: Any) -> date | None:
    text = str(value or "")[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _clean_items(items: Any) -> list[dict[str, Any]]:
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        out.append({k: item.get(k) for k in ("title", "date", "source", "url", "summary", "sentiment")})
    return out


def bucket(items: list[dict[str, Any]], today: date) -> dict[str, list[dict[str, Any]]]:
    """依日期分到近一個月 / 一季 / 一年（不重疊；沒日期或日期不合理的放 undated）。"""
    out: dict[str, list[dict[str, Any]]] = {"last_1m": [], "last_3m": [], "last_12m": [], "undated": []}
    for item in items:
        day = _date_of(item.get("date"))
        if day is None or day > today + timedelta(days=1):
            out["undated"].append(item)
            continue
        age = (today - day).days
        if age <= 31:
            out["last_1m"].append(item)
        elif age <= 92:
            out["last_3m"].append(item)
        elif age <= 366:
            out["last_12m"].append(item)
        # 超過一年的直接丟掉：問的是近一年
    for key in out:
        out[key].sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    return out


def collect(ctx: Any, security: dict[str, Any], warnings: list[str], llm: LLM | None = None) -> dict[str, Any]:
    llm = llm or LLM(ctx)
    today = date.today()
    prompts = queries(security, today)
    names = list(prompts)
    results = parallel([lambda p=prompts[n]: llm.search(p) for n in names], max_workers=3)

    parsed: dict[str, dict[str, Any]] = {}
    searches = []
    sources: list[str] = []
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            warnings.append(f"搜尋「{name}」失敗：{describe(result)}")
            searches.append({"topic": name, "ok": False, "error": describe(result)})
            parsed[name] = {}
            continue
        text, urls = result
        body = json_from(text)
        if not body:
            warnings.append(f"搜尋「{name}」的回應不是 JSON，已略過")
        parsed[name] = body
        sources.extend(urls)
        searches.append({"topic": name, "ok": bool(body), "citations": len(urls)})

    if not any(s["ok"] for s in searches):
        raise RuntimeError("三組新聞搜尋都失敗（model 可能不支援網路搜尋）：" + "；".join(warnings))

    industry = parsed["industry"].get("industry") or {}
    macro = parsed["industry"].get("macro") or {}
    events = [e for e in parsed["events"].get("events") or [] if isinstance(e, dict) and e.get("event")]
    # 「即將到來」：日期已過的事件剔掉（只有月份的保留到該月結束）。
    upcoming = []
    for event in events:
        raw = str(event.get("date") or "")
        day = _date_of(raw) or (_date_of(raw + "-28") if len(raw) == 7 else None)
        if day is None or day >= today:
            upcoming.append({k: event.get(k) for k in ("event", "date", "type", "confirmed", "detail", "source", "url")})
    upcoming.sort(key=lambda e: str(e.get("date") or "9999"))

    for item_list in (parsed["company"].get("items"), industry.get("items"), macro.get("items")):
        for item in item_list or []:
            if isinstance(item, dict) and item.get("url"):
                sources.append(str(item["url"]))

    return {
        "company_news": bucket(_clean_items(parsed["company"].get("items")), today),
        "upcoming_events": upcoming,
        "industry": {"name": industry.get("name"), "summary": industry.get("summary"),
                     "items": _clean_items(industry.get("items"))},
        "macro": {"summary": macro.get("summary"), "items": _clean_items(macro.get("items"))},
        "searches": searches,
        "sources": list(dict.fromkeys(sources))[:60],
    }


ANALYSIS_SCHEMA = {
    "type": "object",
    "required": ["sentiment", "summary"],
    "properties": {
        "sentiment": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL", "MIXED"]},
        "sentiment_score": {"type": "integer", "minimum": -100, "maximum": 100},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "catalysts": {"type": "array", "items": {"type": "string"}, "description": "即將到來、可能推動股價的事件"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "industry_view": {"type": "string"},
        "macro_view": {"type": "string"},
        "summary": {"type": "string"},
    },
}

DATA_SCHEMA = {
    "type": "object",
    "required": ["company_news", "upcoming_events", "industry", "macro", "searches"],
    "properties": {
        "company_news": {
            "type": "object", "required": ["last_1m", "last_3m", "last_12m"],
            "properties": {k: {"type": "array", "items": {"type": "object"}}
                           for k in ("last_1m", "last_3m", "last_12m", "undated")},
        },
        "upcoming_events": {"type": "array", "items": {"type": "object"}},
        "industry": {"type": "object"},
        "macro": {"type": "object"},
        "searches": {"type": "array", "items": {"type": "object"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
}


def interpret(llm: LLM, security: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "你是證券研究員。根據下面已搜集的新聞、即將到來的事件、產業與總經資訊，整理這檔股票的消息面。"
        "只能使用給定內容，不要補充沒看到的消息。\n"
        f"個股：{security['name']}（{security['market']} {security['symbol']}）\n"
        f"資料：{compact_json({k: v for k, v in data.items() if k != 'sources'})}\n\n"
        "以繁體中文回傳**一個 JSON 物件**：\n"
        '{"sentiment":"BULLISH|BEARISH|NEUTRAL|MIXED","sentiment_score":-100~100,'
        '"key_points":["近期最重要的 3–5 件事"],"catalysts":["即將到來的催化劑（含日期）"],'
        '"risks":["風險"],"industry_view":"產業面一兩句","macro_view":"總經面一兩句","summary":"200 字內"}'
    )
    return llm.analyze(prompt)


def run(ctx: Any, security: dict[str, Any], use_llm: bool) -> dict[str, Any]:
    warnings: list[str] = []
    llm = LLM(ctx)
    data = collect(ctx, security, warnings, llm)
    analysis = None
    if use_llm:
        try:
            analysis = interpret(llm, security, data)
        except Exception as exc:              # noqa: BLE001
            warnings.append(f"LLM 統整失敗，只回原始資料：{describe(exc)}")
    return envelope(TOOL, data=data, analysis=analysis, warnings=warnings, security=security)
