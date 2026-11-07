"""Tool 3：技術分析（stock-technical）。

指標全部在容器裡由日線自己算（不另外打指標 API，少一個會壞的來源）：
MA 5/20/60/120/240、RSI(14)、MACD(12,26,9)、KD(9,3,3)、布林通道(20,2)、
量比、區間報酬、52 週高低。`signals` 是寫死的規則判讀，**不是** LLM 的意見；
LLM 的意見在 `analysis`（use_llm=true 才有）。
"""

from __future__ import annotations

from typing import Any

from .common import LLM, compact_json, envelope, position_stats
from .market import MarketData

TOOL = "stock-technical"


# ── 指標 ──────────────────────────────────────────────────────────────


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out: list[float] = []
    for i, v in enumerate(values):
        out.append(v if i == 0 else v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], n: int = 14) -> list[float | None]:
    """Wilder 平滑的 RSI（看盤軟體通用的那種）。"""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for i in range(n, len(closes)):
        if i > n:
            avg_gain = (avg_gain * (n - 1) + gains[i - 1]) / n
            avg_loss = (avg_loss * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    dif = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    dea = ema(dif, signal)
    hist = [a - b for a, b in zip(dif, dea)]
    return dif, dea, hist


def kd(bars: list[dict[str, float]], n: int = 9):
    """台股常用的 KD（RSV 9、K/D 以 1/3 平滑，起始 50）。"""
    k_vals: list[float | None] = []
    d_vals: list[float | None] = []
    k = d = 50.0
    for i in range(len(bars)):
        if i < n - 1:
            k_vals.append(None)
            d_vals.append(None)
            continue
        window = bars[i - n + 1:i + 1]
        high = max(b["high"] for b in window)
        low = min(b["low"] for b in window)
        rsv = 50.0 if high == low else (bars[i]["close"] - low) / (high - low) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
        k_vals.append(k)
        d_vals.append(d)
    return k_vals, d_vals


def bollinger(closes: list[float], n: int = 20, width: float = 2.0):
    mid = sma(closes, n)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i, m in enumerate(mid):
        if m is None:
            upper.append(None)
            lower.append(None)
            continue
        window = closes[i - n + 1:i + 1]
        std = (sum((x - m) ** 2 for x in window) / n) ** 0.5
        upper.append(m + width * std)
        lower.append(m - width * std)
    return upper, mid, lower


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _ret(closes: list[float], days: int) -> float | None:
    if len(closes) <= days:
        return None
    return round((closes[-1] / closes[-1 - days] - 1) * 100, 2)


def indicators(bars: list[dict[str, float]]) -> dict[str, Any]:
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    last = closes[-1]
    mas = {f"ma{n}": _r(sma(closes, n)[-1]) for n in (5, 20, 60, 120, 240)}
    rsi14 = rsi(closes, 14)
    dif, dea, hist = macd(closes)
    k_vals, d_vals = kd(bars)
    upper, mid, lower = bollinger(closes)
    year = bars[-252:]
    vol5 = sum(volumes[-5:]) / min(5, len(volumes))
    vol20 = sum(volumes[-20:]) / min(20, len(volumes))
    return {
        "last_date": bars[-1]["date"],
        "last_close": _r(last, 4),
        "moving_averages": mas,
        "rsi14": _r(rsi14[-1]),
        "rsi14_prev": _r(rsi14[-2]) if len(rsi14) > 1 else None,
        "macd": {"dif": _r(dif[-1], 4), "dea": _r(dea[-1], 4), "histogram": _r(hist[-1], 4),
                 "histogram_prev": _r(hist[-2], 4) if len(hist) > 1 else None},
        "kd": {"k": _r(k_vals[-1]), "d": _r(d_vals[-1]),
               "k_prev": _r(k_vals[-2]) if len(k_vals) > 1 else None,
               "d_prev": _r(d_vals[-2]) if len(d_vals) > 1 else None},
        "bollinger": {"upper": _r(upper[-1]), "middle": _r(mid[-1]), "lower": _r(lower[-1])},
        "volume": {"last": volumes[-1], "avg5": round(vol5), "avg20": round(vol20),
                   "ratio_5_20": _r(vol5 / vol20 if vol20 else None)},
        "returns_pct": {"1w": _ret(closes, 5), "1m": _ret(closes, 21), "3m": _ret(closes, 63),
                        "6m": _ret(closes, 126), "1y": _ret(closes, 252)},
        "range_52w": {"high": _r(max(b["high"] for b in year)), "low": _r(min(b["low"] for b in year))},
        "bars_used": len(bars),
    }


def rule_signals(ind: dict[str, Any]) -> list[dict[str, str]]:
    """寫死的規則判讀。每條都附上依據的數字，讓人看得出為什麼。"""
    out: list[dict[str, str]] = []

    def add(name: str, bias: str, detail: str) -> None:
        out.append({"name": name, "bias": bias, "detail": detail})

    last = ind["last_close"]
    r = ind["rsi14"]
    if r is not None:
        if r >= 70:
            add("RSI 超買", "BEARISH", f"RSI14={r} ≥ 70")
        elif r <= 30:
            add("RSI 超賣", "BULLISH", f"RSI14={r} ≤ 30")

    m = ind["macd"]
    if m["histogram"] is not None and m["histogram_prev"] is not None:
        if m["histogram_prev"] <= 0 < m["histogram"]:
            add("MACD 黃金交叉", "BULLISH", f"柱狀體由 {m['histogram_prev']} 翻正為 {m['histogram']}")
        elif m["histogram_prev"] >= 0 > m["histogram"]:
            add("MACD 死亡交叉", "BEARISH", f"柱狀體由 {m['histogram_prev']} 翻負為 {m['histogram']}")

    k = ind["kd"]
    if None not in (k["k"], k["d"], k["k_prev"], k["d_prev"]):
        if k["k_prev"] <= k["d_prev"] and k["k"] > k["d"]:
            add("KD 黃金交叉", "BULLISH", f"K={k['k']} 上穿 D={k['d']}" + ("（低檔）" if k["k"] < 30 else ""))
        elif k["k_prev"] >= k["d_prev"] and k["k"] < k["d"]:
            add("KD 死亡交叉", "BEARISH", f"K={k['k']} 下穿 D={k['d']}" + ("（高檔）" if k["k"] > 70 else ""))

    ma = ind["moving_averages"]
    ma20, ma60, ma240 = ma["ma20"], ma["ma60"], ma["ma240"]
    if ma20 and ma60:
        if last > ma20 > ma60:
            add("均線多頭排列", "BULLISH", f"收盤 {last} > MA20 {ma20} > MA60 {ma60}")
        elif last < ma20 < ma60:
            add("均線空頭排列", "BEARISH", f"收盤 {last} < MA20 {ma20} < MA60 {ma60}")
    if ma240:
        add("年線之上" if last >= ma240 else "跌破年線",
            "BULLISH" if last >= ma240 else "BEARISH", f"收盤 {last}，MA240 {ma240}")

    b = ind["bollinger"]
    if b["upper"] and last > b["upper"]:
        add("突破布林上軌", "NEUTRAL", f"收盤 {last} > 上軌 {b['upper']}（強勢或過熱）")
    elif b["lower"] and last < b["lower"]:
        add("跌破布林下軌", "NEUTRAL", f"收盤 {last} < 下軌 {b['lower']}（弱勢或超跌）")

    ratio = ind["volume"]["ratio_5_20"]
    if ratio and ratio >= 1.5:
        add("近期爆量", "NEUTRAL", f"5 日均量是 20 日均量的 {ratio} 倍")
    return out


# ── tool ──────────────────────────────────────────────────────────────

ANALYSIS_SCHEMA = {
    "type": "object",
    "required": ["trend", "signal", "summary"],
    "properties": {
        "trend": {"type": "string", "enum": ["UPTREND", "DOWNTREND", "SIDEWAYS"]},
        "signal": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "support_levels": {"type": "array", "items": {"type": "number"}},
        "resistance_levels": {"type": "array", "items": {"type": "number"}},
        "position_advice": {"type": "string", "description": "有持股時，針對成本與損益的處置建議"},
        "summary": {"type": "string"},
    },
}

DATA_SCHEMA = {
    "type": "object",
    "required": ["indicators", "signals"],
    "properties": {
        "yahoo_symbol": {"type": "string"},
        "currency": {"type": ["string", "null"]},
        "indicators": {"type": "object"},
        "signals": {"type": "array", "items": {"type": "object"}},
        "position": {"type": ["object", "null"]},
    },
}


def collect(ctx: Any, security: dict[str, Any], market: MarketData) -> dict[str, Any]:
    """原始資料（use_llm=false 的內容）。portfolio-advisor 也直接呼叫這個。"""
    daily = market.daily(security, "2y")
    ind = indicators(daily["bars"])
    return {
        "yahoo_symbol": daily["yahoo_symbol"],
        "currency": daily["currency"],
        "indicators": ind,
        "signals": rule_signals(ind),
        "position": position_stats(security, ind["last_close"]),
    }


def interpret(llm: LLM, security: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "你是台股/美股技術分析師。根據下面『已經算好的』技術指標與規則訊號判讀走勢。\n"
        "只能使用這些數字，不要捏造其他指標；資料不足就降低 confidence。\n"
        f"個股：{security['name']}（{security['market']} {security['symbol']}）\n"
        f"資料：{compact_json(data)}\n\n"
        "以繁體中文回傳**一個 JSON 物件**，欄位：\n"
        '{"trend":"UPTREND|DOWNTREND|SIDEWAYS","signal":"BUY|SELL|HOLD","confidence":0-100,'
        '"support_levels":[數字],"resistance_levels":[數字],'
        '"position_advice":"若 position 不是 null，依成本與損益給停利/停損/加碼建議；否則給進場建議",'
        '"summary":"150 字內的判讀"}'
    )
    return llm.analyze(prompt)


def run(ctx: Any, security: dict[str, Any], use_llm: bool) -> dict[str, Any]:
    data = collect(ctx, security, MarketData(ctx))
    warnings: list[str] = []
    analysis = None
    if use_llm:
        try:
            analysis = interpret(LLM(ctx), security, data)
        except Exception as exc:              # noqa: BLE001
            warnings.append(f"LLM 解讀失敗，只回原始資料：{type(exc).__name__}: {exc}"[:300])
    return envelope(TOOL, data=data, analysis=analysis, warnings=warnings, security=security)
