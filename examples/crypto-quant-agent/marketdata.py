"""行情資料 —— 把各交易所的 K 線格式正規化成同一種列。

## 沙盒連不出去，所以 `fetch` 是注入進來的

沙盒網段 `internal: true`，沒有對外路由（P4）。這個模組**不自己開連線**，
而是收一個 `fetch(url) -> dict|list` 的 callable，由 agent 那層傳平台的
出口能力進來。這樣做的附帶好處是：測試時傳一個回固定資料的假函式就行，
不用起網路。

## 三家交易所的格式差異（每一條都會咬人）

|  | Binance | OKX | Bybit |
|---|---|---|---|
| 時間順序 | **舊 → 新** | **新 → 舊** | **新 → 舊** |
| 未收盤的那根 | 在最後 | 在**最前面** | 在最前面 |
| 怎麼知道收盤了 | 自己比 `closeTime` 與現在 | 有 `confirm` 欄位（"1"=已收） | 自己比 |
| 數字型別 | 字串 | 字串 | 字串 |
| 交易對寫法 | `BTCUSDT` | `BTC-USDT` | `BTCUSDT` |
| 週期寫法 | `1h` `4h` `1d` | `1H` `4H` `1D` | `60` `240` `D` |

⚠️ **順序是最容易錯、而且錯了最難發現的一項。** 把 OKX 的資料直接餵進指標，
每一條線都會算出數字、圖也畫得出來，只是**整個時間軸是反的** ——
RSI 看起來在超賣其實是超買。不會有任何錯誤訊息。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

#: 正規化後的一列：`[open_time_ms, open, high, low, close, volume, is_closed]`
Row = list


class Fetcher(Protocol):
    """由 agent 注入的 HTTP GET。回傳已解析的 JSON。"""
    def __call__(self, url: str) -> Any: ...


# ── 每家交易所一個轉接器 ────────────────────────────────────────────

_BINANCE_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
                      "6h", "8h", "12h", "1d", "3d", "1w"}
_OKX_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
                  "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H",
                  "12h": "12H", "1d": "1D", "1w": "1W"}


def _ms_per_bar(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    return n * {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}[unit]


class Binance:
    """主要資料源。公開 K 線端點不需要 API key。

    地區限制要知道：部分地區的 IP 會拿到 `451`，這時要換 OKX。
    """
    name = "binance"
    host = "api.binance.com"

    def url(self, symbol: str, interval: str, limit: int) -> str:
        if interval not in _BINANCE_INTERVALS:
            raise ValueError(f"binance does not support interval {interval}")
        return (f"https://{self.host}/api/v3/klines"
                f"?symbol={symbol}&interval={interval}&limit={min(limit, 1000)}")

    def parse(self, payload: Any, interval: str) -> list[Row]:
        if not isinstance(payload, list):
            raise ValueError(f"binance returned unexpected payload: {type(payload).__name__}")
        now = time.time() * 1000
        rows: list[Row] = []
        for k in payload:
            open_time = int(k[0])
            # Binance 的 k[6] 是 closeTime。它是「這根涵蓋到的最後一毫秒」，
            # 所以 closeTime < 現在 才算收完。用 >= 會把正在跑的那根當成已收。
            rows.append([open_time, float(k[1]), float(k[2]), float(k[3]),
                         float(k[4]), float(k[5]), int(k[6]) < now])
        return rows


class OKX:
    """備援資料源。地區限制比 Binance 鬆。

    ⚠️ 回傳是**新 → 舊**，而且有 `confirm` 欄位。兩件事都要處理。
    """
    name = "okx"
    host = "www.okx.com"

    def url(self, symbol: str, interval: str, limit: int) -> str:
        bar = _OKX_INTERVALS.get(interval)
        if bar is None:
            raise ValueError(f"okx does not support interval {interval}")
        inst = symbol if "-" in symbol else _split_symbol(symbol)
        return (f"https://{self.host}/api/v5/market/candles"
                f"?instId={inst}&bar={bar}&limit={min(limit, 300)}")

    def parse(self, payload: Any, interval: str) -> list[Row]:
        if not isinstance(payload, dict) or payload.get("code") != "0":
            code = payload.get("code") if isinstance(payload, dict) else "?"
            msg = payload.get("msg") if isinstance(payload, dict) else ""
            raise ValueError(f"okx error code={code} msg={msg}")
        rows: list[Row] = []
        for k in payload.get("data", []):
            # k = [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            rows.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                         float(k[4]), float(k[5]), str(k[8]) == "1"])
        rows.reverse()          # ← 少了這行，整個時間軸是反的
        return rows


def _split_symbol(symbol: str) -> str:
    """`BTCUSDT` → `BTC-USDT`，讓兩家用同一個設定值。

    從已知的計價幣尾綴切，不是從長度猜 —— `ETHBTC` 這種以 BTC 計價的
    交易對用長度切會切錯。長的計價幣先試，否則 `USDT` 會被 `USD` 先吃掉。
    已經有分隔線的直接回傳，否則 `BTC-USDT` 會被切成 `BTC--USDT`。
    """
    if "-" in symbol:
        return symbol
    for quote in ("USDT", "USDC", "TUSD", "BUSD", "USD", "BTC", "ETH", "EUR"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[:-len(quote)]}-{quote}"
    return symbol


SOURCES = {"binance": Binance(), "okx": OKX()}


# ── 取資料（含備援）────────────────────────────────────────────────

class MarketDataError(RuntimeError):
    """所有資料源都失敗。`attempts` 記下每一家的失敗原因。"""

    def __init__(self, symbol: str, attempts: list[tuple[str, str]]) -> None:
        detail = "; ".join(f"{name}: {err}" for name, err in attempts)
        super().__init__(f"取不到 {symbol} 的 K 線 —— {detail}")
        self.symbol = symbol
        self.attempts = attempts


def fetch_klines(
    fetch: Fetcher, symbol: str, interval: str, limit: int,
    *, order: list[str], on_fallback: Callable[[str, str, str], None] | None = None,
) -> tuple[list[Row], str]:
    """依序試 `order` 裡的資料源，回 (正規化的列, 實際用了哪家)。

    **備援要出聲。** 靜默切換到備援源，會讓「主源掛了三天」看起來跟
    一切正常一模一樣；等到備援也掛了才發現，而那時你已經沒有備援了。
    所以每次退回都呼叫 `on_fallback(失敗的源, 錯誤, 接手的源)`。
    """
    attempts: list[tuple[str, str]] = []
    for i, name in enumerate(order):
        source = SOURCES.get(name)
        if source is None:
            attempts.append((name, "未知的資料源"))
            continue
        try:
            rows = source.parse(fetch(source.url(symbol, interval, limit)), interval)
            if not rows:
                raise ValueError("回傳空的 K 線陣列")
            _assert_sane(rows, symbol, name)
            if attempts and on_fallback:
                on_fallback(attempts[-1][0], attempts[-1][1], name)
            return rows, name
        except Exception as exc:                      # noqa: BLE001 - 逐家退化
            attempts.append((name, f"{type(exc).__name__}: {exc}"[:200]))
            if i == len(order) - 1:
                raise MarketDataError(symbol, attempts) from exc
    raise MarketDataError(symbol, attempts)


def _assert_sane(rows: list[Row], symbol: str, source: str) -> None:
    """正規化後的健全性檢查。

    這幾條是**便宜的保險**：格式弄錯時（尤其是順序反了）在這裡就炸掉，
    而不是讓錯的資料一路流進指標算出看似合理的數字。
    順序反了是最惡性的一種錯 —— 它不會報錯，只會讓每個訊號都是反的。
    """
    times = [r[0] for r in rows]
    if times != sorted(times):
        raise ValueError(f"{source} 的 {symbol} K 線時間非遞增 —— 順序沒正規化")
    if len(set(times)) != len(times):
        raise ValueError(f"{source} 的 {symbol} K 線有重複時間戳")
    for r in rows:
        o, h, l, c = r[1], r[2], r[3], r[4]
        if not (h >= max(o, c) and l <= min(o, c) and h >= l):
            raise ValueError(f"{source} 的 {symbol} 有不合理的 OHLC：{r[:5]}")
        if r[5] < 0:
            raise ValueError(f"{source} 的 {symbol} 出現負成交量")
