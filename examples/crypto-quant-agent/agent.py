"""加密貨幣量化指標 agent —— ZEN-019 三層能力的示範與驗收載體。

一張圖，五個節點：

    preflight ──▶ fetch ──▶ analyse ──▶ decide ──┬──▶ notify ──▶ END
                                                 └──▶ END（沒觸發就不寄信）

它同時示範三件平台在 ZEN-019 之前做不到的事：

  · `ctx.config`       改參數不用重發版本（追哪些交易對、RSI 門檻…）
  · `ctx.secret_names` 存得了 SMTP 密碼，而且**拿不到值**，只看得到名字
  · `ctx.http` / `ctx.send_email()`  連得到交易所、寄得出信

## 三條寫這支 agent 時踩得到的線

1. **`build()` 裡讀 config 一律 `.get()` 帶預設值，而且不要在 `build()` 裡
   檢查機密。** 版本驗證（`--mode=validate`）時 `ctx.config` 是 `{}`、
   `ctx.secret_names` 是 `[]`（契約 §4.1 第 4 點）。在 `build()` 裡硬性要求
   任何一個，這支 agent 會**在發布階段就失敗**。啟動自檢要放在**節點裡**。
2. **備援邏輯不寫在這裡。** `marketdata.fetch_klines()` 已經做完主源／備援／
   正規化／健全性檢查，它缺的只是一個 `fetch(url) -> JSON`。
   在 agent 裡再寫一層 try/except 換源，會出現兩套互相打架的重試。
3. **`ctx.http` 是同步阻塞的，所以節點寫成同步函式**（`def`，不是 `async def`）。
   LangGraph 會把同步節點丟到 worker thread 跑，不會卡住 event loop；
   寫成 `async def` 再在裡面做阻塞 I/O，同一個常駐容器裡的其他 Run 會一起卡住。

## 白名單

平台**預設拒絕**：沒有出口規則，這支 agent 一根 K 棒都抓不到。
要兩條：`api.binance.com` 與 `www.okx.com`（見 README）。
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from zentra_sdk import agent

import marketdata
import signals

#: 沒設定時追這些。`ctx.config["symbols"]` 可以換掉。
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DEFAULT_INTERVAL = "1h"
DEFAULT_LIMIT = 300
#: 主源、備援。順序可以用 config 的 `sources` 換（例如 OKX 優先）。
DEFAULT_SOURCES = ["binance", "okx"]
#: 寄信要用到的機密名字。**只檢查名字在不在，拿不到值。**
SMTP_SECRET = "SMTP_PASSWORD"


class State(TypedDict, total=False):
    symbols: list[str]
    interval: str
    limit: int
    sources: list[str]
    warnings: list[str]
    candles: dict[str, Any]
    snapshots: dict[str, Any]
    decisions: list[dict[str, Any]]
    triggered: list[dict[str, Any]]
    notified: bool
    summary: str


def _decision_view(decision) -> dict[str, Any]:
    """把 `signals.Decision` 攤平成可序列化的輸出。"""
    return {
        "symbol": decision.symbol,
        "interval": decision.interval,
        "direction": decision.direction,
        "score": decision.score,
        "triggered": decision.triggered,
        "regime": decision.regime,
        "price": decision.price,
        "entry": decision.entry,
        "stop": decision.stop,
        "targets": list(decision.targets or []),
        "risk_pct": decision.risk_pct,
        "position_size": decision.position_size,
        "reasons": list(decision.reasons or []),
        "warnings": list(decision.warnings or []),
        "factors": [
            {"name": f.name, "score": round(f.score, 4), "why": f.why}
            for f in (decision.factors or [])
        ],
    }


def _email_body(triggered: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in triggered:
        lines.append(f"{item['symbol']} {item['interval']} {item['direction']} "
                     f"（分數 {item['score']:+.3f}，{item['regime']}）")
        lines.append(f"  價格 {item['price']}")
        if item.get("entry") is not None:
            lines.append(f"  進場 {item['entry']}　停損 {item['stop']}"
                         f"　目標 {', '.join(str(t) for t in item['targets'])}")
        for factor in item["factors"][:5]:
            lines.append(f"  · {factor['name']} {factor['score']:+.2f} {factor['why']}")
        lines.append("")
    lines.append("—— 這封信由 Zentra 上的 crypto-quant-agent 寄出。")
    lines.append("訊號是研究輸出，不是投資建議。")
    return "\n".join(lines)


@agent(
    name="crypto-quant-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "抓 K 棒 → 算指標與諧波型態 → 訊號成立時寄信",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array", "items": {"type": "string"},
                    "description": "這一次要看哪些交易對。省略則用設定裡的 symbols。",
                },
                "interval": {"type": "string", "description": "K 棒週期，例如 1h。"},
                "dry_run": {
                    "type": "boolean",
                    "description": "true 時只分析不寄信。預設 false。",
                },
            },
            "required": [],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "decisions": {"type": "array", "items": {"type": "object"}},
                "triggered": {"type": "array", "items": {"type": "object"}},
                "notified": {"type": "boolean"},
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "decisions", "triggered", "notified", "warnings"],
        },
    }],
)
def build(ctx):
    """建圖。

    **這裡只讀 config，而且全部 `.get()` 帶預設值。**
    驗證沙盒的 `ctx.config` 是空的（契約 §4.1 第 4 點），所以任何
    `ctx.config["x"]` 都會讓這支 agent 發布失敗 —— 那正是那條規則要防的事。
    """
    raw_config = ctx.config or {}
    # `merge_config` 疊到 `signals.DEFAULTS` 上，並回報不認得的 key。
    # **打錯字要出聲**：使用者在 Console 上把 `rsi_period` 打成 `rsi_perio`
    # 而沒有任何提示的話，他會以為設定生效了。
    cfg = signals.merge_config(raw_config)
    unknown_keys = cfg.pop("_unknown_config_keys", [])

    default_symbols = raw_config.get("symbols") or DEFAULT_SYMBOLS
    default_interval = raw_config.get("interval") or DEFAULT_INTERVAL
    default_limit = int(raw_config.get("limit") or DEFAULT_LIMIT)
    sources = raw_config.get("sources") or DEFAULT_SOURCES
    notify_to = raw_config.get("notify_to")
    notify_on_flat = bool(raw_config.get("notify_on_flat", False))

    def http_fetch(url: str):
        """`marketdata` 要的那個注入點：`fetch(url) -> 已解析的 JSON`。

        非 2xx 直接拋例外，讓 `fetch_klines` 去換備援源 ——
        它已經處理好「哪一家失敗了、換誰接手」，這裡不要再做一次。
        """
        return ctx.http.get(url, headers={"Accept": "application/json"}).raise_for_status().json()

    def preflight(state: State) -> State:
        """啟動自檢。**放在節點裡，不能放在 `build()`。**

        驗證沙盒的 `ctx.secret_names` 是空的，寫在 `build()` 裡會讓這支 agent
        自己發布失敗（契約 §4.1 第 4 點）。

        缺 `SMTP_PASSWORD` 時**在第一個節點就說出來**，不要跑完整段分析
        才在寄信那一步失敗 —— 那時使用者已經等了幾十秒，而錯誤看起來像網路問題。
        """
        warnings: list[str] = []
        if unknown_keys:
            message = "設定裡有不認得的鍵（打錯字？）：" + ", ".join(sorted(unknown_keys))
            warnings.append(message)
            ctx.logger.warning(message)
        if SMTP_SECRET not in ctx.secret_names:
            message = (
                f"缺機密 {SMTP_SECRET}：訊號成立時寄不出信。"
                f"在 Console 的「機密」卡存一個，再到「寄件者」設定引用它。"
                f"目前有的機密：{', '.join(ctx.secret_names) or '（一個都沒有）'}"
            )
            warnings.append(message)
            ctx.logger.error(message)
        ctx.emit("preflight", {
            "symbols": state.get("symbols") or default_symbols,
            "interval": state.get("interval") or default_interval,
            "sources": sources,
            "has_smtp_secret": SMTP_SECRET in ctx.secret_names,
            "unknown_config_keys": sorted(unknown_keys),
        })
        return {
            "symbols": state.get("symbols") or default_symbols,
            "interval": state.get("interval") or default_interval,
            "limit": default_limit,
            "sources": list(sources),
            "warnings": warnings,
        }

    def fetch(state: State) -> State:
        """每個 symbol 抓一次 K 棒。備援由 `marketdata` 負責。"""
        interval = state["interval"]
        limit = state["limit"]
        warnings = list(state.get("warnings") or [])
        candles: dict[str, Any] = {}

        def on_fallback(failed: str, error: str, took_over: str) -> None:
            # **備援要出聲。** 靜默切換會讓「主源掛了三天」看起來跟正常一樣。
            message = f"{failed} 失敗（{error}），改用 {took_over}"
            warnings.append(message)
            ctx.logger.warning(message)

        for symbol in state["symbols"]:
            try:
                rows, source = marketdata.fetch_klines(
                    http_fetch, symbol, interval, limit,
                    order=state["sources"], on_fallback=on_fallback,
                )
            except Exception as exc:               # noqa: BLE001 - 一個 symbol 掛掉不該拖垮其他的
                message = f"{symbol} 取不到 K 棒：{type(exc).__name__}: {exc}"
                warnings.append(message)
                ctx.logger.error(message)
                continue
            candles[symbol] = {"rows": rows, "source": source}
            ctx.emit("fetched", {"symbol": symbol, "source": source, "bars": len(rows)})
        return {"candles": candles, "warnings": warnings}

    def analyse(state: State) -> State:
        """算指標與型態。`prepare()` 會砍掉未收盤的那根，**不要繞過它** ——
        用形成中的 K 棒算訊號會在盤中閃爍。"""
        snapshots: dict[str, Any] = {}
        warnings = list(state.get("warnings") or [])
        for symbol, payload in (state.get("candles") or {}).items():
            try:
                candles = signals.prepare(payload["rows"])
                snapshots[symbol] = signals.analyse(candles, cfg)
            except Exception as exc:               # noqa: BLE001
                message = f"{symbol} 分析失敗：{type(exc).__name__}: {exc}"
                warnings.append(message)
                ctx.logger.error(message)
        return {"snapshots": snapshots, "warnings": warnings}

    def decide(state: State) -> State:
        interval = state["interval"]
        decisions: list[dict[str, Any]] = []
        triggered: list[dict[str, Any]] = []
        for symbol, snapshot in (state.get("snapshots") or {}).items():
            view = _decision_view(signals.decide(symbol, interval, snapshot, cfg))
            decisions.append(view)
            if view["triggered"] or (notify_on_flat and view["direction"] != "FLAT"):
                triggered.append(view)
            ctx.emit("decided", {
                "symbol": symbol, "direction": view["direction"],
                "score": view["score"], "triggered": view["triggered"],
            })
        return {"decisions": decisions, "triggered": triggered}

    def notify(state: State) -> State:
        """訊號成立才走這裡。收件人優先用 config 的 `notify_to`。"""
        triggered = state.get("triggered") or []
        warnings = list(state.get("warnings") or [])
        recipients = [notify_to] if notify_to else []
        if not recipients:
            message = ("設定裡沒有 notify_to，改用寄件者設定的第一個收件人 —— "
                       "平台仍會用收件人白名單擋不在清單上的地址。")
            warnings.append(message)
            ctx.logger.warning(message)

        subject = "、".join(
            f"{item['symbol']} {item['direction']}" for item in triggered[:3])
        if len(triggered) > 3:
            subject += f" 等 {len(triggered)} 筆"
        try:
            message_id = ctx.send_email(
                to=recipients or [],
                subject=f"[Zentra] {subject}"[:200],
                text=_email_body(triggered),
            )
            ctx.emit("notified", {"message_id": message_id, "count": len(triggered)})
            return {"notified": True, "warnings": warnings}
        except Exception as exc:                   # noqa: BLE001 - 寄不出去不該讓分析結果消失
            message = f"寄信失敗：{type(exc).__name__}: {exc}"
            warnings.append(message)
            ctx.logger.error(message)
            return {"notified": False, "warnings": warnings}

    def summarise(state: State) -> State:
        decisions = state.get("decisions") or []
        triggered = state.get("triggered") or []
        parts = [f"{len(decisions)} 個交易對，{len(triggered)} 個觸發"]
        for item in triggered:
            parts.append(f"{item['symbol']} {item['direction']} {item['score']:+.3f}")
        return {
            "summary": "；".join(parts),
            "notified": bool(state.get("notified")),
            "warnings": list(state.get("warnings") or []),
        }

    def should_notify(state: State) -> str:
        """**條件分支**：沒觸發就直接收尾，不要寄一封「今天沒事」的信。"""
        if not state.get("triggered"):
            return "summarise"
        return "notify"

    graph = StateGraph(State)
    graph.add_node("preflight", preflight)
    graph.add_node("fetch", fetch)
    graph.add_node("analyse", analyse)
    graph.add_node("decide", decide)
    graph.add_node("notify", notify)
    graph.add_node("summarise", summarise)

    graph.set_entry_point("preflight")
    graph.add_edge("preflight", "fetch")
    graph.add_edge("fetch", "analyse")
    graph.add_edge("analyse", "decide")
    graph.add_conditional_edges("decide", should_notify,
                                {"notify": "notify", "summarise": "summarise"})
    graph.add_edge("notify", "summarise")
    graph.add_edge("summarise", END)
    return graph
