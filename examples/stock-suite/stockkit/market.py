"""行情：Yahoo Finance chart API（台股、美股同一個來源）。

    GET https://query1.finance.yahoo.com/v8/finance/chart/2330.TW?range=2y&interval=1d

- 不需要 key，吃平台固定的 `User-Agent: Zentra-Egress/1`（2026-11 實測）。
- 2 年日線約 30–60 KB，遠低於 egress 單次 1 MiB 的上限。
- 台股：上市是 `.TW`、上櫃是 `.TWO`。使用者通常不知道是哪個，所以先試 `.TW`，
  查無資料再試 `.TWO`（多花一次 egress）。

需要的 egress 規則：`query1.finance.yahoo.com`，GET，path_prefix `/v8/finance/chart/`。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"


class NoMarketData(RuntimeError):
    """查無行情：代碼錯了、或市場選錯。重試沒有用。"""


def yahoo_candidates(security: dict[str, Any]) -> list[str]:
    symbol = security["symbol"]
    if security["market"] == "TW":
        return [f"{symbol}.TW", f"{symbol}.TWO"]
    return [symbol.replace(".", "-")]          # BRK.B → BRK-B（Yahoo 的寫法）


def _parse(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return {}, []
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    quote_ = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    offset = int(meta.get("gmtoffset") or 0)
    bars = []
    for i, ts in enumerate(stamps):
        try:
            o, h, l, c = (quote_[k][i] for k in ("open", "high", "low", "close"))
            v = quote_.get("volume", [None] * len(stamps))[i]
        except (KeyError, IndexError, TypeError):
            continue
        if None in (o, h, l, c):               # 停牌、或盤中尚未成交的那一根
            continue
        day = datetime.fromtimestamp(ts + offset, tz=timezone.utc).date().isoformat()
        bars.append({"date": day, "open": float(o), "high": float(h), "low": float(l),
                     "close": float(c), "volume": float(v or 0)})
    # 同一天出現兩根（盤中即時那根 + 收盤）時留最後一根。
    dedup: dict[str, dict[str, Any]] = {}
    for bar in bars:
        dedup[bar["date"]] = bar
    return meta, list(dedup.values())


class MarketData:
    """一筆 Run 內的行情快取。

    **每筆 Run 建一個新的**，不要放在模組層級 —— 常駐容器裡多筆 Run 共用 process，
    全域快取會讓 A 使用者的請求吃到 B 的資料，也會讓日線永遠停在第一次抓的那天。
    chart 與 technical 在 portfolio-advisor 裡會抓同一檔，用它省掉重複的 egress。
    """

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._cache: dict[tuple[str, str, str], dict[str, Any]] = {}

    def daily(self, security: dict[str, Any], range_: str = "2y") -> dict[str, Any]:
        """回 {yahoo_symbol, currency, exchange, bars:[...]}，查無資料拋 NoMarketData。"""
        key = (security["market"], security["symbol"], range_)
        if key in self._cache:
            return self._cache[key]
        last_error = ""
        for candidate in yahoo_candidates(security):
            url = CHART_URL.format(symbol=quote(candidate, safe=""), range=range_)
            response = self._ctx.http.get(url, headers={"Accept": "application/json"}, timeout_seconds=15)
            if response.status == 404:
                last_error = f"{candidate}: 404"
                continue
            response.raise_for_status()
            meta, bars = _parse(response.json())
            if bars:
                value = {
                    "yahoo_symbol": candidate,
                    "currency": meta.get("currency"),
                    "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
                    "bars": bars,
                }
                self._cache[key] = value
                return value
            last_error = f"{candidate}: 沒有任何 K 棒"
        raise NoMarketData(
            f"查不到 {security['market']} {security['symbol']} 的行情（{last_error}）。"
            "請確認代碼與市場（TW/US）是否正確")

    def fx_usd_twd(self) -> float | None:
        """美元兌台幣，portfolio-advisor 換算權重用。抓不到回 None（呼叫端會寫 warning）。"""
        url = CHART_URL.format(symbol=quote("TWD=X", safe=""), range="5d")
        try:
            response = self._ctx.http.get(url, headers={"Accept": "application/json"}, timeout_seconds=15)
            response.raise_for_status()
            _, bars = _parse(response.json())
        except Exception:                      # noqa: BLE001 - 由呼叫端降級
            return None
        return bars[-1]["close"] if bars else None
