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

    def __init__(self, run_id: str, manifest: dict[str, Any], emit: Emitter) -> None:
        self._run_id = run_id
        self._manifest = manifest
        self._emit = emit
        self._logger = AgentLogger(emit)

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def config(self) -> dict[str, Any]:
        return dict(self._manifest.get("config", {}))

    @property
    def logger(self) -> AgentLogger:
        return self._logger

    def emit(self, name: str, payload: dict[str, Any] | None = None) -> None:
        """推一個自訂進度事件到 Run 事件流（SSE 的 `custom` 事件）。"""
        self._emit("custom", {"name": name, "payload": payload or {}})

    def llm(self, model: str | None = None, **kwargs: Any):
        """回傳一個指向 Internal Gateway 的 LangChain chat model。

            llm = ctx.llm()                      # 用平台預設的 model
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
        token = os.environ.get("ZENTRA_RUN_TOKEN")
        if not gateway or not token:
            raise RuntimeError(
                "ctx.llm() 只能在 Run 的沙盒裡呼叫 —— 驗證沙盒沒有 run token，"
                "Gateway 也會擋下它（契約 §4）。"
            )

        return ChatOpenAI(
            base_url=gateway.rstrip("/") + "/llm/v1",
            api_key=token,
            model=model or self._manifest.get("default_model") or "gpt-4o-mini",
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
