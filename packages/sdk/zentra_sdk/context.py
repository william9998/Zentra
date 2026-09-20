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

    def llm(self, **kwargs: Any):
        """回傳一個指向 Internal Gateway 的 LangChain chat model。

        **ZEN-002 才實作。** 屆時它只是一個設了
        `base_url=$ZENTRA_GATEWAY/llm/v1` 與 `api_key=$ZENTRA_RUN_TOKEN` 的標準
        ChatOpenAI —— 真金鑰永遠在 api 那一側（P4）。
        """
        raise NotImplementedError(
            "ctx.llm() 在 ZEN-002 才實作。ZEN-000 的骨架刻意不呼叫任何 LLM。"
        )


def agent(name: str, version: str = "0.0.0", **meta: Any):
    """標記 `build()` 是這個 bundle 的 entrypoint。

    平台在發布時（ZEN-001）靠這個 marker 找到並驗證契約。
    """

    def decorator(fn):
        fn.__zentra_agent__ = {"name": name, "version": version, **meta}
        return fn

    return decorator
