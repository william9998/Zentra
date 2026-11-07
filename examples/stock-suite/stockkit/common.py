"""五個股票 Agent 共用的基礎：輸入正規化、輸出信封、LLM 呼叫。

## 輸出信封（五個 tool 都一樣）

    {
      "tool": "stock-news",          # 自報身分：呼叫端用它確認「拿到的是我叫的那個」
      "schema_version": "1",
      "as_of": "2026-11-07T06:00:00+00:00",
      "security": {...},             # 正規化後的個股（portfolio-advisor 沒有這欄）
      "data": {...},                 # tool 的原始內容 —— use_llm 開關都會有
      "analysis": {...} | null,      # use_llm=false 一律 null
      "warnings": ["..."]            # 部分資料抓不到時寫在這，不讓整筆 Run 失敗
    }

`use_llm=false` 只代表「不另外請 LLM 解讀」。stock-news 的原始資料本身就是
模型網路搜尋的結果（沙盒不能上網，搜尋只能交給模型），所以它在兩種模式都會用 LLM。

## 這裡的函式都是同步的

`ctx.http` 是同步阻塞的，所以 Agent 的節點一律寫成 `def`（LangGraph 會丟到
worker thread）。需要平行就用 `parallel()`，**不要**在 `async def` 裡呼叫這些函式，
會卡住同一個常駐容器裡的其他 Run。
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

SCHEMA_VERSION = "1"

DISCLAIMER = "本內容由程式與模型整理公開資訊產生，僅供參考，不構成投資建議。"

_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-]{1,15}$")


class InputError(ValueError):
    """輸入過了 JSON Schema 但語意上不合理（例如美股代碼帶 .TW）。"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── 輸入 ──────────────────────────────────────────────────────────────


def security_input_schema(*, with_position: bool) -> dict[str, Any]:
    """個股型 tool（1–4）的 input_schema。

    `additionalProperties: false`：多帶一個欄位就是 422，
    呼叫端拼錯欄位名會立刻失敗，而不是被安靜地忽略。
    `use_llm` 是**必填**，沒有預設值 —— 呼叫端一定要明講要不要 LLM 解讀。
    """
    props: dict[str, Any] = {
        "symbol": {
            "type": "string", "pattern": _SYMBOL_RE.pattern,
            "description": "股票代碼。台股填數字代碼（例如 2330、6488），美股填 ticker（例如 AAPL）",
        },
        "name": {"type": "string", "minLength": 1, "maxLength": 60,
                 "description": "股票名稱，例如「台積電」「Apple」"},
        "market": {"type": "string", "enum": ["TW", "US"], "description": "TW = 台股（上市/上櫃），US = 美股"},
        "use_llm": {"type": "boolean",
                    "description": "true = 由這個 tool 的 LLM 解讀後回傳 analysis；false = 只回原始資料，analysis 為 null"},
    }
    if with_position:
        props["cost"] = {"type": "number", "minimum": 0,
                         "description": "每股成本價（原幣）。觀察清單可省略"}
        props["shares"] = {"type": "number", "minimum": 0,
                           "description": "持有股數。觀察清單可省略"}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["symbol", "name", "market", "use_llm"],
        "properties": props,
    }


def normalize_security(state: dict[str, Any]) -> dict[str, Any]:
    """把輸入整理成 `security`。schema 已經擋掉大部分錯，這裡只處理語意。"""
    market = state["market"]
    symbol = str(state["symbol"]).strip().upper()
    if market == "TW":
        # 使用者常會貼 2330.TW；統一去掉後綴，由 market 模組決定 .TW / .TWO。
        symbol = re.sub(r"\.(TW|TWO)$", "", symbol)
        if not re.fullmatch(r"[0-9A-Z]{4,6}", symbol):
            raise InputError(f"台股代碼看起來不對：{state['symbol']!r}（例如 2330、00878）")
    elif "." in symbol and not re.fullmatch(r"[A-Z]+\.[A-Z]", symbol):   # BRK.B 這種可以
        raise InputError(f"美股代碼看起來不對：{state['symbol']!r}（例如 AAPL、BRK.B）")
    out = {"symbol": symbol, "name": str(state["name"]).strip(), "market": market}
    for key in ("cost", "shares"):
        if state.get(key) is not None:
            out[key] = float(state[key])
    return out


def position_stats(security: dict[str, Any], last_close: float | None) -> dict[str, Any] | None:
    """有成本與股數才算損益；觀察清單回 None。"""
    cost, shares = security.get("cost"), security.get("shares")
    if last_close is None or not shares:
        return None
    market_value = last_close * shares
    stats: dict[str, Any] = {"shares": shares, "last_close": round(last_close, 4),
                             "market_value": round(market_value, 2)}
    if cost:
        cost_basis = cost * shares
        stats.update({
            "cost": cost,
            "cost_basis": round(cost_basis, 2),
            "unrealized_pnl": round(market_value - cost_basis, 2),
            "unrealized_pnl_pct": round((last_close / cost - 1) * 100, 2),
        })
    return stats


# ── 輸出 ──────────────────────────────────────────────────────────────


def envelope_output_schema(tool: str, data_schema: dict[str, Any], analysis_schema: dict[str, Any],
                           *, with_security: bool = True) -> dict[str, Any]:
    """五個 tool 共用的 output_schema。

    只對「呼叫端依賴的骨架」設 required；data / analysis 內部寬鬆 ——
    output 驗證失敗會讓整筆 Run 失敗，資料源多一個少一個欄位不值得這樣。
    """
    required = ["tool", "schema_version", "as_of", "data", "analysis", "warnings", "disclaimer"]
    props: dict[str, Any] = {
        "tool": {"type": "string", "const": tool},
        "schema_version": {"type": "string"},
        "as_of": {"type": "string", "description": "產生時間（UTC ISO 8601）"},
        "data": data_schema,
        "analysis": {"anyOf": [{"type": "null"}, analysis_schema],
                     "description": "use_llm=false 時為 null"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "disclaimer": {"type": "string"},
    }
    if with_security:
        required.append("security")
        props["security"] = {
            "type": "object", "required": ["symbol", "name", "market"],
            "properties": {
                "symbol": {"type": "string"}, "name": {"type": "string"},
                "market": {"type": "string", "enum": ["TW", "US"]},
                "cost": {"type": "number"}, "shares": {"type": "number"},
            },
        }
    return {"type": "object", "required": required, "properties": props}


def envelope(tool: str, *, data: dict[str, Any], analysis: dict[str, Any] | None,
             warnings: Iterable[str], security: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "tool": tool, "schema_version": SCHEMA_VERSION, "as_of": now_iso(),
        "data": data, "analysis": analysis, "warnings": list(warnings), "disclaimer": DISCLAIMER,
    }
    if security is not None:
        out["security"] = security
    return out


# ── 平行 ──────────────────────────────────────────────────────────────


def parallel(fns: list[Callable[[], Any]], *, max_workers: int = 4) -> list[Any]:
    """同時跑幾個同步函式，回傳值或例外（例外**不往外拋**，由呼叫端決定怎麼處理）。

    執行緒是這筆 Run 自己的，跑完就收，不會留到下一筆。
    """
    if not fns:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(fns)))) as pool:
        futures = [pool.submit(fn) for fn in fns]
        results: list[Any] = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:          # noqa: BLE001 - 交給呼叫端
                results.append(exc)
        return results


def describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


# ── LLM ───────────────────────────────────────────────────────────────


def json_from(text: str) -> dict[str, Any]:
    """從模型回應取出 JSON 物件。抓第一個 `{` 到最後一個 `}`；失敗回 {}。"""
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def text_of(reply: Any) -> str:
    """把模型回應轉成純文字。

    Responses API 開了 web_search 之後 `content` 是一串區塊
    （reasoning / web_search_call / output_text），`str()` 會把好幾 KB 的
    加密 reasoning 也印出來；`AIMessage.text` 又不認 `output_text`。
    細節看 examples/stock-agent/agent.py 的 `_text_of`。
    """
    content = getattr(reply, "content", reply)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in ("text", "output_text"):
                parts.append(str(block.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return str(content)


def urls_of(reply: Any) -> list[str]:
    """Responses API 的 url_citation 註記裡的網址（有就收，沒有就算了）。"""
    content = getattr(reply, "content", None)
    found: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            for note in block.get("annotations") or []:
                if isinstance(note, dict) and note.get("url"):
                    found.append(str(note["url"]))
    return list(dict.fromkeys(found))


class LLM:
    """包一層 `ctx.llm()`，把「用哪個 model」交給 Console 的設定。

    `ctx.config` 可設（改了不用發版，下一筆 Run 生效）：
      * `SEARCH_MODEL`   —— 網路搜尋用（stock-news）
      * `ANALYSIS_MODEL` —— 解讀用（use_llm=true）
      * `VISION_MODEL`   —— 看 K 線圖用（stock-chart）
    都沒設就用這個 Agent 在 Console 設的預設 model。
    """

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def _model(self, key: str, **kwargs: Any):
        name = self._ctx.config.get(key) or None
        return self._ctx.llm(name, **kwargs) if name else self._ctx.llm(**kwargs)

    def search(self, prompt: str) -> tuple[str, list[str]]:
        """帶網路搜尋問一次。回 (文字, 引用網址)。

        先試 Responses API 的 `web_search` tool，再退回 chat/completions 的
        `web_search_options`（只有 *-search 系列吃）。兩種都失敗就拋例外 ——
        **不退回不搜尋硬問**：新聞 tool 沒搜到就是沒搜到，不能讓模型憑記憶編。
        """
        errors: list[str] = []
        for kwargs in (
            {"use_responses_api": True, "model_kwargs": {"tools": [{"type": "web_search"}]}},
            {"model_kwargs": {"web_search_options": {}}},
        ):
            try:
                reply = self._model("SEARCH_MODEL", **kwargs).invoke(prompt)
            except Exception as exc:          # noqa: BLE001 - 供應商差異，逐一退化
                errors.append(describe(exc))
                continue
            text = text_of(reply)
            if text.strip():
                return text, urls_of(reply)
            errors.append("回應裡沒有任何文字區塊")
        raise RuntimeError("網路搜尋失敗：" + " | ".join(errors))

    def analyze(self, prompt: str) -> dict[str, Any]:
        """不搜尋、要求回 JSON 物件。解析失敗拋例外（由呼叫端寫進 warnings）。"""
        try:
            reply = self._model("ANALYSIS_MODEL",
                                model_kwargs={"response_format": {"type": "json_object"}}).invoke(prompt)
        except Exception:                      # noqa: BLE001 - 有的 model 不吃 json mode
            reply = self._model("ANALYSIS_MODEL").invoke(prompt)
        parsed = json_from(text_of(reply))
        if not parsed:
            raise ValueError("模型沒有回傳可解析的 JSON")
        return parsed

    def look(self, prompt: str, png_base64: str) -> dict[str, Any]:
        """看圖（K 線）並回 JSON。"""
        from langchain_core.messages import HumanMessage

        message = HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_base64}"}},
        ])
        reply = self._model("VISION_MODEL").invoke([message])
        parsed = json_from(text_of(reply))
        if not parsed:
            raise ValueError("模型沒有回傳可解析的 JSON")
        return parsed


def compact_json(value: Any, limit: int = 60_000) -> str:
    """塞進 prompt 的 JSON。超過上限就截斷並註明（不要讓 prompt 無聲變形）。"""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > limit:
        return text[:limit] + f"…（已截斷，原長 {len(text)} 字元）"
    return text
