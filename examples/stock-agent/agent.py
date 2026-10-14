"""股市分析 Agent —— 帶 retry 迴圈的多節點 LangGraph。

使用者問任何股票或大盤問題，它：

    classify ─▶ research ─▶ assess ─┬─(資訊不足且還有次數)─▶ research（換個問法）
                                    └─(夠了 或 用完次數)────▶ advise ─▶ END

**retry 迴圈是這支的重點。** 網路搜尋回來的東西不一定回答得了使用者的問題 ——
`assess` 節點讓**模型自己判斷**夠不夠，不夠就帶著「缺什麼」回去重搜。
這是用 graph 而不是用一串函式呼叫的實際理由：條件分支會回頭。

## 網路搜尋走哪條路

模型自己上網搜，**不是沙盒上網** —— 沙盒沒有對外路由（P4）。
路徑是：沙盒 → Internal Gateway → 供應商（供應商那邊去搜）。

兩種 API 形狀都試，因為能不能用取決於你的 model：

  * Responses API + `web_search` tool  ← 模型自己決定何時搜，較靈活
  * chat/completions + `web_search_options`  ← 只有 *-search 系列模型吃這個

平台的 Gateway 兩條都代理（ZEN-013）。搜不到就靠 retry 迴圈與最後的
`data_quality` 欄位誠實回報，不會假裝有資料。

## ⚠️ 這不是投資建議

輸出帶 `disclaimer` 欄位，而且模型看得到的資料就是它搜到的那些。
把它當成「把公開資訊整理成一個格式」的工具，不是判斷依據。
"""

import json
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from zentra_sdk import agent

#: retry 上限。**一定要有** —— 沙盒有 15 分鐘 wall-clock 硬上限（SPEC §10），
#: 而每一輪都是一次帶網路搜尋的 LLM 呼叫，很慢也很貴。
MAX_RESEARCH_ROUNDS = 3


class State(TypedDict, total=False):
    """節點之間傳遞的狀態。

    ⚠️ **沒宣告在這裡的 key，LangGraph 會直接丟掉。** 節點回傳了也沒用 ——
    不會報錯，只是安靜消失，然後輸出驗證才說少了某個必填欄位。
    output_schema 的每個 required 欄位都必須在這裡有對應宣告。
    """
    question: str
    locale: str
    # classify 產出
    subject_type: str            # "STOCK" | "MARKET"
    subject: str                 # 股票代號/名稱，或大盤名稱
    focus: str                   # 使用者真正想知道的點
    # research 產出（每輪覆寫）
    findings: str
    sources: list[str]
    # assess 產出
    rounds: int
    sufficient: bool
    missing: str
    # advise 產出
    summary: str
    bullish_percent: int
    bearish_percent: int
    recommendation: str
    rationale: str
    data_quality: str
    disclaimer: str


def _json_from(text: str) -> dict[str, Any]:
    """從模型回應裡取出 JSON。

    即使要求了 JSON，模型仍可能包一層 ```json 或前後加話。
    抓第一個 `{` 到最後一個 `}` 是這裡最耐用的做法 ——
    解析失敗回空 dict，讓呼叫端走自己的預設值，不要整個 Run 炸掉。
    """
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return {}


def _text_of(reply) -> str:
    """把模型回應轉成**純文字**。

    ## 為什麼不能直接 str(reply.content)

    開了 Responses API 的網路搜尋之後，`content` 不是字串，是**一串內容區塊**：

        [{"type": "reasoning", "encrypted_content": "gAAAAA…（好幾 KB 的 base64）"},
         {"type": "web_search_call", "action": {...}},
         {"type": "output_text", "text": "← 真正的答案在這裡"}]

    `str()` 會把整個 list 的 Python repr 印出來。結果不只是難看：
    那幾段加密的 reasoning 有好幾 KB，會把真正的答案**擠到截斷長度之外**，
    於是下游拿到一堆亂碼，判定「資料不足」，而模型其實有好好回答。實際踩過。

    ## 為什麼不用 `AIMessage.text`

    它只認 `type == "text"`，**不認 `output_text`** —— 而 Responses API 用的
    正是後者，所以那條路會回空字串，失敗得同樣安靜。
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


@agent(
    name="stock-agent",
    version="1.0.0",
    entrypoints=[{
        "name": "default",
        "description": "問任何股票或大盤問題，回新聞面整理、看多/看空比例與買賣建議",
        "input_schema": {
            "type": "object",
            "required": ["question"],
            "properties": {
                "question": {
                    "type": "string",
                    "minLength": 2,
                    "description": "例如「台積電最近怎麼樣」「美股大盤現在能買嗎」",
                },
                "locale": {
                    "type": "string",
                    "description": "回答語言",
                    "default": "zh-TW",
                },
            },
        },
        "output_schema": {
            "type": "object",
            "required": [
                "subject_type", "subject", "summary",
                "bullish_percent", "bearish_percent", "recommendation",
                "rationale", "sources", "rounds", "data_quality", "disclaimer",
            ],
            "properties": {
                "subject_type": {
                    "type": "string", "enum": ["STOCK", "MARKET"],
                    "description": "模型判斷問的是個股還是大盤",
                },
                "subject": {"type": "string", "description": "個股代號/名稱，或大盤名稱"},
                "summary": {"type": "string", "description": "新聞面與基本資訊整理"},
                "bullish_percent": {"type": "integer", "minimum": 0, "maximum": 100},
                "bearish_percent": {"type": "integer", "minimum": 0, "maximum": 100},
                "recommendation": {
                    "type": "string", "enum": ["BUY", "SELL", "HOLD"],
                    "description": "HOLD 表示資料不足以給出方向，不是「持平看好」",
                },
                "rationale": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}},
                "rounds": {
                    "type": "integer",
                    "description": "實際搜了幾輪。等於上限表示最後一輪仍不滿意",
                },
                "data_quality": {
                    "type": "string", "enum": ["GOOD", "PARTIAL", "INSUFFICIENT"],
                    "description": "誠實回報找到的資料夠不夠支撐上面的結論",
                },
                "disclaimer": {"type": "string"},
            },
        },
    }],
)
def build(ctx) -> StateGraph:
    graph = StateGraph(State)

    def llm(**kwargs):
        """取得 chat model。金鑰、端點、計量都在平台那一側（P4）。"""
        return ctx.llm(**kwargs)

    async def ask(prompt: str, *, search: bool) -> str:
        """問一次模型。`search=True` 時試著開啟網路搜尋。

        兩種 API 形狀依序試，因為支援哪一種取決於 model：
        先試 Responses API 的 `web_search` tool，失敗再退回
        chat/completions 的 `web_search_options`，再失敗就不帶搜尋硬問一次。
        **三層都失敗不讓 Run 死** —— 讓 assess 判定資料不足，
        最後由 `data_quality` 誠實回報。
        """
        if search:
            for attempt in (
                {"use_responses_api": True, "model_kwargs": {"tools": [{"type": "web_search"}]}},
                {"model_kwargs": {"web_search_options": {}}},
            ):
                try:
                    reply = await llm(**attempt).ainvoke(prompt)
                    text = _text_of(reply)
                    if not text.strip():
                        # 只回了 reasoning 與搜尋紀錄、沒有文字 —— 換下一種形狀再試。
                        # 這裡不能當成「成功但沒內容」，那會讓 assess 誤判成查不到。
                        raise ValueError("回應裡沒有任何文字區塊")
                    return text
                except Exception as exc:       # noqa: BLE001 - 供應商差異，逐一退化
                    ctx.logger.warning("帶搜尋的呼叫失敗，換一種形狀再試",
                                       detail=f"{type(exc).__name__}: {exc}"[:300])
        reply = await llm().ainvoke(prompt)
        return _text_of(reply)

    # ── 節點 1：判斷在問什麼 ────────────────────────────────────────

    async def classify(state: State) -> dict:
        ctx.logger.info("判斷問題類型", question=state["question"][:120])
        raw = await ask(
            "你是股市助理。判斷下面這個問題問的是「個股」還是「大盤」，"
            "並抽出標的與使用者真正想知道的重點。\n"
            '只回 JSON：{"subject_type":"STOCK"或"MARKET","subject":"…","focus":"…"}\n\n'
            f"問題：{state['question']}",
            search=False,
        )
        parsed = _json_from(raw)
        subject_type = parsed.get("subject_type")
        if subject_type not in ("STOCK", "MARKET"):
            subject_type = "MARKET"        # 判斷不出來時當大盤問題處理
        result = {
            "subject_type": subject_type,
            "subject": str(parsed.get("subject") or state["question"])[:120],
            "focus": str(parsed.get("focus") or state["question"])[:300],
            "rounds": 0,
        }
        ctx.emit("classified", result)
        return result

    # ── 節點 2：搜集資訊（retry 迴圈會回到這裡）──────────────────────

    async def research(state: State) -> dict:
        rounds = state.get("rounds", 0) + 1
        missing = state.get("missing", "")
        ctx.emit("research_round", {"round": rounds, "missing": missing})
        ctx.logger.info("開始搜集資訊", round=rounds, subject=state["subject"])

        retry_hint = (
            f"\n\n上一輪缺的是：{missing}\n這一輪請針對這些缺口找。"
            if missing else ""
        )
        raw = await ask(
            "用網路搜尋找最新公開資訊，回答下面的需求。"
            "只根據你實際查到的內容回答，查不到就說查不到，不要編造數字或新聞。\n"
            f"標的：{state['subject']}（{state['subject_type']}）\n"
            f"使用者想知道：{state['focus']}\n"
            "需要：近期重要新聞、影響股價/指數的事件、法人或市場情緒、"
            "以及你引用的來源網址。\n"
            '回 JSON：{"findings":"整理後的內容","sources":["url", …]}'
            + retry_hint,
            search=True,
        )
        parsed = _json_from(raw)
        findings = str(parsed.get("findings") or raw)[:6000]
        sources = [str(s)[:500] for s in (parsed.get("sources") or []) if s][:12]
        ctx.logger.info("這一輪找到的內容", chars=len(findings), sources=len(sources))
        return {"rounds": rounds, "findings": findings, "sources": sources}

    # ── 節點 3：夠不夠？（這是 retry 的判斷點）──────────────────────

    async def assess(state: State) -> dict:
        raw = await ask(
            "判斷下面查到的資訊，夠不夠回答使用者的問題。"
            "標準嚴格一點：沒有具體事件或數據、或內容與標的無關，都算不夠。\n"
            f"使用者想知道：{state['focus']}\n"
            f"標的：{state['subject']}\n"
            f"查到的內容：\n{state.get('findings', '')[:4000]}\n\n"
            '只回 JSON：{"sufficient":true或false,"missing":"還缺什麼，具體一點"}',
            search=False,
        )
        parsed = _json_from(raw)
        sufficient = bool(parsed.get("sufficient"))
        missing = str(parsed.get("missing") or "")[:500]
        ctx.emit("assessed", {
            "round": state.get("rounds", 0), "sufficient": sufficient, "missing": missing,
        })
        return {"sufficient": sufficient, "missing": missing}

    def route_after_assess(state: State) -> str:
        """retry 的條件分支。**上限一定要在這裡擋住**。"""
        if state.get("sufficient"):
            return "advise"
        if state.get("rounds", 0) >= MAX_RESEARCH_ROUNDS:
            ctx.logger.warning("已達搜尋上限，用手上的資料作答",
                               rounds=state.get("rounds"), missing=state.get("missing", ""))
            return "advise"
        return "research"

    # ── 節點 4：給結論 ──────────────────────────────────────────────

    async def advise(state: State) -> dict:
        is_stock = state["subject_type"] == "STOCK"
        subject_word = "這檔個股" if is_stock else "這個大盤"
        raw = await ask(
            f"根據下面查到的資訊，對{subject_word}給出判斷。"
            "看多與看空的百分比必須相加為 100。"
            "資料不足以判斷方向時，recommendation 用 HOLD 並在 rationale 說明原因，"
            "**不要**為了給答案而編造理由。\n"
            f"標的：{state['subject']}\n"
            f"使用者想知道：{state['focus']}\n"
            f"查到的內容：\n{state.get('findings', '')[:6000]}\n\n"
            "只回 JSON："
            '{"summary":"新聞面與基本資訊整理","bullish_percent":0-100,'
            '"bearish_percent":0-100,"recommendation":"BUY"或"SELL"或"HOLD",'
            '"rationale":"理由","data_quality":"GOOD"或"PARTIAL"或"INSUFFICIENT"}\n'
            f"用 {state.get('locale') or 'zh-TW'} 回答。",
            search=False,
        )
        parsed = _json_from(raw)

        bullish = parsed.get("bullish_percent")
        bearish = parsed.get("bearish_percent")
        try:
            bullish = max(0, min(100, int(bullish)))
            bearish = max(0, min(100, int(bearish)))
        except (TypeError, ValueError):
            bullish, bearish = 50, 50
        if bullish + bearish != 100:            # 模型常常加起來不是 100
            bearish = 100 - bullish

        recommendation = parsed.get("recommendation")
        if recommendation not in ("BUY", "SELL", "HOLD"):
            recommendation = "HOLD"

        quality = parsed.get("data_quality")
        if quality not in ("GOOD", "PARTIAL", "INSUFFICIENT"):
            # 模型沒說就用 assess 的判斷推：最後一輪還不滿意就是不足
            quality = "GOOD" if state.get("sufficient") else "PARTIAL"

        result = {
            "summary": str(parsed.get("summary") or state.get("findings", ""))[:4000],
            "bullish_percent": bullish,
            "bearish_percent": bearish,
            "recommendation": recommendation,
            "rationale": str(parsed.get("rationale") or "")[:2000],
            "data_quality": quality,
            "disclaimer": (
                "本結果由語言模型整理公開網路資訊自動產生，可能過時、不完整或有誤，"
                "且未考慮你的持倉、成本與風險承受度。**這不是投資建議**，"
                "請自行查證並為自己的決定負責。"
            ),
        }
        ctx.emit("advised", {
            "recommendation": recommendation,
            "bullish_percent": bullish,
            "data_quality": quality,
        })
        return result

    # ── 接線：注意 assess 會回頭到 research ────────────────────────

    graph.add_node("classify", classify)
    graph.add_node("research", research)
    graph.add_node("assess", assess)
    graph.add_node("advise", advise)

    graph.add_edge(START, "classify")
    graph.add_edge("classify", "research")
    graph.add_edge("research", "assess")
    graph.add_conditional_edges(
        "assess", route_after_assess,
        {"research": "research", "advise": "advise"},   # ← 回頭的那一條就是 retry
    )
    graph.add_edge("advise", END)

    return graph
