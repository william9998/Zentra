import os
from collections.abc import Callable
from typing import Any

#: 平台注入的事件送出函式。使用者不會看到它。
Emitter = Callable[[str, dict[str, Any]], None]


class AgentLogger:
    """`ctx.logger` —— 寫出去的東西會變成 Run 事件流裡的 `log` 事件。

    刻意不給 `print`：stdout 只進容器日誌，使用者在 Console 上看不到。
    """

    def __init__(self, emit: Emitter) -> None:
        self._emit = emit

    def _log(self, level: str, message: str, **fields: Any) -> None:
        self._emit("log", {"level": level, "message": str(message), **fields})

    def debug(self, message: str, **f: Any) -> None: self._log("debug", message, **f)
    def info(self, message: str, **f: Any) -> None: self._log("info", message, **f)
    def warning(self, message: str, **f: Any) -> None: self._log("warning", message, **f)
    def error(self, message: str, **f: Any) -> None: self._log("error", message, **f)


class ZentraContext:
    """平台在沙盒內建立並交給 `build()` 的執行脈絡。"""

    def __init__(self, run_id: str, manifest: dict[str, Any], emit: Emitter,
                 default_model: str = "", run_token: str = "",
                 config: dict[str, Any] | None = None,
                 secret_names: list[str] | None = None) -> None:
        self._run_id = run_id
        self._manifest = manifest
        self._emit = emit
        self._logger = AgentLogger(emit)
        # ZEN-019：平台送來的可變設定與機密名字。
        # **config 是頂層欄位，不是 manifest 的一部分**（契約 §4.1 第 1 點）：
        # manifest 是版本的不可變內容，把可變設定攪進去會讓 P5 在 runtime
        # 這一側失去實體。
        self._config = dict(config or {})
        # **只有名字。** 值永遠不進沙盒（P4）—— 它們由 Gateway 在代打 HTTP
        # 或寄信時當場解密注入。沒有 `ctx.secrets`，那不是還沒做，是契約禁止。
        self._secret_names = sorted(secret_names or [])
        # 平台解析好的預設 model（這個 Agent 的憑證設定 → 平台預設）。
        # **不要在這裡寫死一個值** —— 寫死的話這個 Agent 在 Console 上設的
        # 預設 model 永遠到不了，而且「沒生效」在畫面上看起來跟「生效了」一樣。
        self._default_model = default_model
        # 這個 Run 的 token。**常駐模式下 env 裡沒有它** —— 一個容器同時服務多個 Run，
        # token 是跟著工作項進來的，寫進 env 就會串到別人身上。
        # 所以它掛在 context 上：context 本來就是一個 Run 一個。
        self._run_token = run_token

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def config(self) -> dict[str, Any]:
        """這個 Agent 在 Console 上設的那份 JSON（api-contract §2.6）。

        **改設定不用發新版本**：下一筆 Run 就是新值，已經在跑的不受影響。

        ⚠️ **在 `build()` 裡一律用 `.get()` 帶預設值，不要硬性要求某個 key。**
        版本驗證通過之後設定還會繼續變 —— 哪天有人刪掉一個 key，
        每一筆 Run 都會在 build 階段死掉，而版本狀態仍然是 `PUBLISHED`
        （ADR-0014 §7）。要嚴格檢查就在節點裡做，那樣至少看得到錯誤事件。

        **這裡不是放密碼的地方。** 它是明文，Console 上看得到，也完整進沙盒。
        密碼與 API key 放機密（`ctx.secret_names`），值由平台代持。
        """
        return dict(self._config)

    @property
    def secret_names(self) -> list[str]:
        """這個 Agent 存了哪些機密的**名字**（排序過）。

            if "SMTP_PASSWORD" not in ctx.secret_names:
                raise RuntimeError("請先在 Console 存 SMTP_PASSWORD")

        **拿不到值，也拿不到長度或尾四碼。** 給名字的唯一目的是 fail fast：
        讓 agent 在第一個節點就說出「缺什麼」，而不是跑完整段分析才在最後
        一步失敗。`ctx.secrets` 不存在 —— 那不是還沒做，是契約禁止（§2.7）。
        """
        return list(self._secret_names)

    @property
    def http(self):
        """對外 HTTP，由平台代打（api-contract §4.2）。

            def fetch(url):
                return ctx.http.get(url).raise_for_status().json()

        沙盒網段沒有對外路由，所以這不是一般的 HTTP client：請求送到平台，
        平台比對白名單（Console 的「對外出口」）、注入認證、寫稽核，再把結果
        帶回來。**沒有白名單規則就一個網站都連不到**，這是預設拒絕。

        `status` 是**上游的**狀態碼 —— 上游回 429／500 不會拋例外，
        那是「主源失敗換備援」的前提。被平台擋下才會拋 `EgressBlocked`，
        而那種錯誤重試沒用，要去改設定。

        **同步阻塞。** 在同步節點（`def node(state)`）裡呼叫；
        寫在 `async def` 節點裡會卡住同一個容器裡其他 Run 的 event loop。
        """
        from zentra_sdk.egress import EgressClient

        gateway, token = self._gateway_and_token("ctx.http")
        return EgressClient(gateway, token)

    def send_email(self, *, to, subject: str, text: str | None = None,
                   html: str | None = None) -> str:
        """寄一封信，回 `message_id`（api-contract §4.4）。

            ctx.send_email(to="me@example.com", subject="BTCUSDT 看跌背離",
                           text="RSI 72.4 / D 點 0.886 回撤")

        寄件者、帳號、密碼都由平台決定（Console 的「寄件者」設定），
        密碼以名字引用一個機密 —— **它不會進到這個行程裡**。

        收件人必須在 `allowed_recipients` 裡；**有一個不在，整封信都不寄**。
        每筆 Run 與每小時都有上限（預設 5 / 60）：訊號型 agent 最典型的 bug
        就是重試迴圈裡寄信，而被鎖的是使用者本人的信箱帳號。

        **回傳表示 SMTP 伺服器收下了，不表示對方讀到了。不冪等。**
        """
        from zentra_sdk.egress import send_email as _send

        gateway, token = self._gateway_and_token("ctx.send_email()")
        return _send(gateway, token, to=to, subject=subject, text=text, html=html)

    def _gateway_and_token(self, what: str) -> tuple[str, str]:
        """Gateway 位址與這次 Run 的 token。驗證沙盒沒有 token，直接說清楚。

        兩套命名並存的理由見 `llm()`：OCI 路徑注入 `ZENTRA_GATEWAY_URL`，
        ZEN-000 的舊路徑注入 `ZENTRA_GATEWAY`。
        """
        gateway = os.environ.get("ZENTRA_GATEWAY_URL") or os.environ.get("ZENTRA_GATEWAY")
        token = self._run_token or os.environ.get("ZENTRA_RUN_TOKEN")
        if not gateway or not token:
            raise RuntimeError(
                f"{what} 只能在 Run 的沙盒裡呼叫 —— 驗證沙盒沒有 run token，"
                "Gateway 也會擋下它（契約 §4）。build() 裡不要呼叫它，"
                "把它放進節點。"
            )
        return gateway, token

    @property
    def logger(self) -> AgentLogger:
        return self._logger

    def emit(self, name: str, payload: dict[str, Any] | None = None) -> None:
        """推一個自訂進度事件到 Run 事件流（SSE 的 `custom` 事件）。"""
        self._emit("custom", {"name": name, "payload": payload or {}})

    def llm(self, model: str | None = None, **kwargs: Any):
        """回傳一個指向 Internal Gateway 的 LangChain chat model。

            llm = ctx.llm()                      # 用這個 Agent 設定的預設 model
            llm = ctx.llm("gpt-4o", temperature=0)
            reply = await llm.ainvoke("一句話解釋沙盒隔離")

        它只是一個標準的 `ChatOpenAI`，唯二的不同是：

          * `base_url` 指向 `$ZENTRA_GATEWAY_URL/llm/v1`（沙盒唯一連得到的地方）
          * `api_key` 是這次 Run 的 run token，**不是供應商金鑰**

        真金鑰永遠在 `app` 那一側（P4）。Gateway 收到請求後才換上它、呼叫供應商、
        計量 token、寫 `llm_calls`，然後把回應原樣送回來。所以：

          * 你**拿不到**金鑰，被攻陷也洩漏不了
          * 每一次呼叫都有紀錄，`usage` 是平台算的不是你報的
          * 換供應商是維運改 `app` 的設定，你的程式碼一行都不用動

        不要自建 LLM client 或自己 `requests` 打外網 —— 沙盒網段沒有對外路由，
        那會直接失敗（SPEC §7.3）。
        """
        try:
            from langchain_openai import ChatOpenAI
        except ModuleNotFoundError as exc:      # pragma: no cover - image 缺套件
            raise RuntimeError(
                "ctx.llm() 需要 langchain-openai。zentra/agent-base:1.0 已經裝好了；"
                "自己寫 Dockerfile 的話要 pip install langchain-openai。"
            ) from exc

        # 程式碼裡有兩套命名並存：OCI 路徑注入 `ZENTRA_GATEWAY_URL`（drivers/base.py
        # 的 for_run），ZEN-000 的舊路徑注入 `ZENTRA_GATEWAY`（manager.create）。
        # 兩個都接受，免得 SDK 綁死在其中一條路上。
        gateway = os.environ.get("ZENTRA_GATEWAY_URL") or os.environ.get("ZENTRA_GATEWAY")
        # 一次性沙盒的 token 在 env；常駐容器的在 context 上（見 __init__）。
        # **env 要放在後面** —— 常駐容器的 env 裡只有 container token，
        # 拿它去打 LLM 會被 Gateway 擋掉，而錯誤訊息會指向完全錯的方向。
        token = self._run_token or os.environ.get("ZENTRA_RUN_TOKEN")
        if not gateway or not token:
            raise RuntimeError(
                "ctx.llm() 只能在 Run 的沙盒裡呼叫 —— 驗證沙盒沒有 run token，"
                "Gateway 也會擋下它（契約 §4）。"
            )

        # 優先序：呼叫時明寫的 → 平台解析好的預設。
        #
        # **不能留一個寫死的保底值。** `ChatOpenAI` 不接受 `model=None`，
        # 不給它又會默默用 `gpt-3.5-turbo` —— 兩種都是「使用者設定的值被無聲蓋掉」。
        # 所以拿不到預設時直接說清楚，不要猜一個。
        chosen = model or self._default_model
        if not chosen:
            raise RuntimeError(
                "平台沒有提供預設 model。這通常表示 image 裡的 zentra_sdk 比平台舊 —— "
                "用新的 zentra/agent-base 重新 build 這個 image，或呼叫時明寫 "
                'ctx.llm("gpt-4o-mini")。'
            )
        return ChatOpenAI(
            base_url=gateway.rstrip("/") + "/llm/v1",
            api_key=token,
            model=chosen,
            **kwargs,
        )


def agent(name: str, version: str = "0.0.0", **meta: Any):
    """Mark ``build()`` as the image Agent factory.

    The image runtime imports ``agent:build`` and reads this marker. Image
    authors may provide ``entrypoints=[...]`` in the decorator metadata; that
    exact JSON Schema list becomes the REST/OpenAPI/MCP source of truth.
    """

    def decorator(fn):
        fn.__zentra_agent__ = {"name": name, "version": version, **meta}
        return fn

    return decorator
