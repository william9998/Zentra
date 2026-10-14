"""Implementation of the ``zentra.image/v1`` validate/run executable.

This module is installed inside the user-owned image. It is never imported by
the Zentra API or Celery worker, preserving P1.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import inspect
import os
import secrets
import time
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any, Callable

import httpx
from jsonschema import Draft202012Validator

from zentra_sdk.context import ZentraContext


def _uuid7() -> str:
    milliseconds = time.time_ns() // 1_000_000
    value = (
        ((milliseconds & ((1 << 48) - 1)) << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return str(uuid.UUID(int=value))


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _check_runtime_dependencies() -> None:
    """Fail with a readable message when the image is missing an SDK dependency.

    The image contract does not enumerate the SDK's own transitive dependencies
    (Q-009): the SDK declares them in its ``pyproject.toml`` and checks them
    here. Without this, a missing ``jsonschema`` surfaces as a bare
    ``ModuleNotFoundError`` raised at import time -- before any handler is
    installed -- and the user only ever sees it in the validation container's
    traceback.
    """

    missing = [
        name for name in ("httpx", "jsonschema", "langgraph", "langchain_openai")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise RuntimeError(
            f"the agent image is missing SDK dependencies: {', '.join(missing)}. "
            f"Install them in the image (pip install {' '.join(missing)}), or base "
            f"it on zentra/agent-base:1.0, which already has them."
        )


def _load_build() -> tuple[Callable[..., Any], dict[str, Any]]:
    # ADR-0003 defines runtime.entry as ``agent:build``. The v1 image contract
    # fixes that convention so the platform never executes an arbitrary
    # client-supplied command.
    module = importlib.import_module("agent")
    build = getattr(module, "build", None)
    marker = getattr(build, "__zentra_agent__", None)
    if not callable(build) or not isinstance(marker, dict):
        raise TypeError("agent:build must be decorated with zentra_sdk.agent")
    _check_build_arity(build)
    return build, marker


def _check_build_arity(build: Callable[..., Any]) -> None:
    """``build`` takes exactly one positional parameter: the context (Q-008).

    Checked before the call so that the common mistake -- writing ``def build()``
    -- reports what the contract requires instead of Python's
    ``build() takes 0 positional arguments but 1 was given``, which does not
    tell the user what the missing argument is or where it comes from.
    """

    try:
        signature = inspect.signature(build)
    except (TypeError, ValueError):       # builtins and C callables have none
        return

    positional = [
        parameter for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    accepts_var_positional = any(
        parameter.kind is parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )
    if accepts_var_positional:
        return
    required = [parameter for parameter in positional if parameter.default is parameter.empty]
    if len(positional) < 1 or len(required) > 1:
        raise TypeError(
            "build must accept exactly one positional argument, the context: "
            "`def build(ctx: ZentraContext) -> StateGraph`. "
            f"Got `def build{signature}`."
        )


def _manifest(marker: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entrypoints = marker.get("entrypoints")
    if not isinstance(entrypoints, list) or not entrypoints:
        raise ValueError("@agent must declare a non-empty entrypoints list")
    names: set[str] = set()
    for item in entrypoints:
        required = {"name", "description", "input_schema", "output_schema"}
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("each entrypoint must contain name, description, input_schema, output_schema")
        if item["name"] in names:
            raise ValueError("entrypoint names must be unique")
        names.add(item["name"])
        Draft202012Validator.check_schema(item["input_schema"])
        Draft202012Validator.check_schema(item["output_schema"])
        if item["input_schema"].get("type") != "object" or item["output_schema"].get("type") != "object":
            raise ValueError("entrypoint input/output schemas must describe objects")
    manifest = {
        "apiVersion": "zentra/v1",
        "name": marker["name"],
        "version": marker["version"],
        "runtime": {"entry": "agent:build"},
    }
    return manifest, entrypoints


def _is_uncompiled_state_graph(value: Any) -> bool:
    from langgraph.graph import StateGraph

    return isinstance(value, StateGraph)


async def _post(
    client: httpx.AsyncClient, url: str, payload: Any, attempts: int = 5,
    headers: dict[str, str] | None = None,
) -> None:
    for attempt in range(attempts):
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or attempt == attempts - 1:
                raise
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
        await asyncio.sleep(min(0.2 * (2 ** attempt), 2.0))


async def validate() -> None:
    gateway = os.environ["ZENTRA_GATEWAY_URL"].rstrip("/")
    version_id = os.environ["ZENTRA_AGENT_VERSION_ID"]
    token = os.environ["ZENTRA_VALIDATION_TOKEN"]
    url = f"{gateway}/versions/{version_id}/validation"
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}, timeout=30, trust_env=False,
    ) as client:
        try:
            build, marker = _load_build()
            manifest, entrypoints = _manifest(marker)
            # ZEN-019 / 契約 §4.1 第 4 點（v0.12.1 U2）：**validation 一律用空的
            # config 與 secret_names**，而且不去跟平台要。
            #
            # 這不是將就，它比帶當下設定更嚴格：契約要求 `build()` 不得對 config
            # 做硬性要求（`.get()` 帶預設值，或在節點裡讀），而用空 config 去 build
            # 正好就是在驗這條。一個空 config 就 build 不起來的 agent **應該**
            # 發布失敗 —— 否則等使用者哪天清掉設定，它的每一筆 Run 都會死在
            # build 階段，而版本狀態仍是 `PUBLISHED`。
            context = ZentraContext(version_id, manifest, lambda *_: None)
            graph = build(context)
            if not _is_uncompiled_state_graph(graph):
                raise TypeError("build(ctx) must return an uncompiled StateGraph")
            await _post(client, url, {
                "valid": True,
                "runtime_contract": "zentra.image/v1",
                "manifest": manifest,
                "entrypoints": entrypoints,
                "error": None,
            })
        except Exception as exc:
            await _post(client, url, {
                "valid": False,
                "runtime_contract": "zentra.image/v1",
                "manifest": None,
                "entrypoints": None,
                "error": {
                    "code": "BUILD_FAILED", "message": str(exc), "run_id": None,
                    "retryable": False, "details": {"stage": "VALIDATE"},
                },
            })
            raise


async def _execute(
    client: httpx.AsyncClient,
    gateway: str,
    run_id: str,
    token: str,
    entrypoint_name: str,
    run_input: dict[str, Any],
    manifest: dict[str, Any],
    default_model: str = "",
    config: dict[str, Any] | None = None,
    secret_names: list[str] | None = None,
) -> None:
    """跑完一個 Run：驗輸入 → compile → 串事件 → 回寫結果。

    一次性沙盒與常駐容器共用這個函式，差別只在憑證從哪來 ——
    前者從 env，後者跟著工作項。**兩條路走同一段程式碼**，
    不然常駐模式的行為會慢慢漂離一次性模式。
    """
    base = f"{gateway}/runs/{run_id}"
    # 每個 Run 帶自己的 Authorization。**不能設在 client 上** ——
    # 常駐容器的同一個 client 同時服務多個 Run，設在 client 上會串到別人身上。
    auth = {"Authorization": f"Bearer {token}"}

    build, marker = _load_build()
    _, entrypoints = _manifest(marker)
    entrypoint = next(
        (item for item in entrypoints if item["name"] == entrypoint_name), None
    )
    if entrypoint is None:
        raise ValueError(f"unknown entrypoint {entrypoint_name!r}")
    Draft202012Validator(entrypoint["input_schema"]).validate(run_input)

    async def emit(kind: str, payload: dict[str, Any]) -> None:
        await _post(client, f"{base}/events", [{
            "event_id": _uuid7(), "type": kind, "payload": payload,
        }], headers=auth)

    pending: list[tuple[str, dict[str, Any]]] = []
    context = ZentraContext(
        run_id, manifest, lambda kind, payload: pending.append((kind, payload)),
        default_model=default_model, run_token=token,
        # ZEN-019：**頂層欄位，不是從 manifest 挖出來的**（契約 §4.1 第 1 點）。
        # 兩條執行路徑（一次性沙盒／常駐容器）都要送進來 —— 漏掉哪一條，
        # 那條路徑的 ctx.config 就永遠是空的，而畫面上看起來跟「設定沒存進去」
        # 一模一樣。
        config=config or {}, secret_names=secret_names or [],
    )
    graph = build(context)
    if not _is_uncompiled_state_graph(graph):
        raise TypeError("build(ctx) must return an uncompiled StateGraph")
    compiled = graph.compile()

    output: dict[str, Any] | None = None
    async for event in compiled.astream_events(run_input, version="v2"):
        kind = event.get("event")
        name = event.get("name")
        if kind in {"on_chain_start", "on_chain_end"} and name != "LangGraph":
            await emit(
                "node.start" if kind == "on_chain_start" else "node.end",
                {"node": name, "ts": _timestamp()},
            )
        elif kind == "on_chain_end" and name == "LangGraph":
            candidate = event.get("data", {}).get("output")
            if isinstance(candidate, dict):
                output = candidate
        while pending:
            custom_kind, payload = pending.pop(0)
            await emit(custom_kind, payload)

    if output is None:
        raise RuntimeError("compiled StateGraph returned no object output")
    Draft202012Validator(entrypoint["output_schema"]).validate(output)
    await _post(client, f"{base}/result", {
        "output": output,
        "error": None,
        "usage": {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0"},
    }, headers=auth)


async def run() -> None:
    """一次性沙盒：跑一個 Run 就退出。憑證從 env 來。"""
    gateway = os.environ["ZENTRA_GATEWAY_URL"].rstrip("/")
    run_id = os.environ["ZENTRA_RUN_ID"]
    token = os.environ["ZENTRA_RUN_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=30, trust_env=False) as client:
        response = await client.get(f"{gateway}/runs/{run_id}/bootstrap")
        response.raise_for_status()
        bootstrap = response.json()
        await _execute(
            client, gateway, run_id, token,
            bootstrap["entrypoint"], bootstrap["input"], bootstrap["manifest"],
            # 平台解析好的預設 model。舊平台沒有這個欄位 → 空字串，
            # 那時 ctx.llm() 會要求呼叫端明寫，而不是默默用別的 model。
            bootstrap.get("default_model") or "",
            bootstrap.get("config") or {},
            bootstrap.get("secret_names") or [],
        )


async def serve() -> None:
    """常駐模式（ZEN-015 / ADR-0012）：long-poll 取工作，同時處理多個 Run。

    ## 為什麼這樣寫

    **容器只會主動打 Gateway，不監聽任何 port。** 這讓 L1 的
    「沙盒只有一個對外方向」在常駐模式下仍然成立 —— 網路拓撲一個字都沒改。

    **每個 Run 的 token 跟著工作項進來**，不在 env 裡（那裡只有 container token，
    它唯一能做的事是取工作）。token 傳進 `_execute` 的參數，
    **不設在共用的 httpx client 上** —— 設在 client 上會讓同時跑的 Run 互相串到。

    **一個 Run 失敗不能停掉迴圈。** 使用者的程式碼會炸，那是預期內的；
    炸掉整個容器會讓同容器其他 Run 一起死，而且下一個請求也沒人接。
    """
    gateway = os.environ["ZENTRA_GATEWAY_URL"].rstrip("/")
    agent_id = os.environ["ZENTRA_AGENT_ID"]
    container_token = os.environ["ZENTRA_CONTAINER_TOKEN"]
    max_concurrent = max(1, int(os.environ.get("ZENTRA_MAX_CONCURRENT_RUNS") or 1))

    work_url = f"{gateway}/agents/{agent_id}/work"
    poll_auth = {"Authorization": f"Bearer {container_token}"}
    # 同時最多幾個 Run。這是 ADR-0012 的 N —— 滿了就不再取工作，
    # 讓工作留在佇列裡由別的容器（或稍後的自己）處理。
    gate = asyncio.Semaphore(max_concurrent)
    inflight: set[asyncio.Task] = set()

    async def handle(client: httpx.AsyncClient, item: dict[str, Any]) -> None:
        run_id = item["run_id"]
        token = item["run_token"]
        try:
            await _execute(
                client, gateway, run_id, token,
                item.get("entrypoint") or "default", item.get("input") or {},
                item.get("manifest") or {},
                item.get("default_model") or "",
                # **常駐容器是預設路徑。** 這兩行漏掉的話，絕大多數 Run 的
                # ctx.config 都是空的 —— 而症狀跟「設定沒存進去」長得一樣。
                item.get("config") or {},
                item.get("secret_names") or [],
            )
        except Exception as exc:                  # noqa: BLE001
            # **一定要印出來。** 常駐容器不會退出，所以「容器的 exit code 與輸出」
            # 這個線索不存在 —— 靜默吞掉例外會讓 Run 永遠卡在 RUNNING
            # 而容器看起來一切正常。實測踩過這個，查了很久才發現。
            print(f"[zentra] run {run_id} failed: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            # 回報失敗也要用**這個 Run 的** token，而且不能讓回報失敗再炸一次。
            try:
                await _post(client, f"{gateway}/runs/{run_id}/result", {
                    "output": None,
                    "error": {
                        "code": "USER_CODE_ERROR",
                        "message": f"{type(exc).__name__}: {exc}"[:4000],
                        "run_id": run_id, "retryable": False,
                        "details": {"stage": "RUN"},
                    },
                    "usage": {"llm_calls": 0, "input_tokens": 0,
                              "output_tokens": 0, "cost_usd": "0"},
                }, headers={"Authorization": f"Bearer {token}"})
            except Exception as report_error:      # noqa: BLE001
                # 回報也失敗：那個 Run 會卡在 RUNNING 直到逾時被掃掉。
                # 至少要在容器日誌留下痕跡，否則沒有任何地方看得到原因。
                print(f"[zentra] reporting the failure of run {run_id} also failed: "
                      f"{type(report_error).__name__}: {report_error}", flush=True)
        finally:
            gate.release()

    # timeout=None：long-poll 會掛在那裡等，不能讓 client 自己先逾時。
    async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
        while True:
            # 滿載時先等一個位置再去取，不然取到了也沒人跑。
            await gate.acquire()
            try:
                response = await client.get(work_url, headers=poll_auth)
            except Exception as exc:              # noqa: BLE001
                gate.release()
                # 取工作失敗通常是 Gateway 重啟或網路抖動 —— 退避後重試，
                # **不要退出**：退出的容器沒有人會自動拉回來（restart_policy=no）。
                print(f"[zentra] taking work failed: {type(exc).__name__}: {exc}", flush=True)
                await asyncio.sleep(2)
                continue

            if response.status_code == 204:       # 逾時，沒有工作
                gate.release()
                continue
            if response.status_code >= 400:
                gate.release()
                print(f"[zentra] taking work returned {response.status_code}", flush=True)
                await asyncio.sleep(2)
                continue

            item = response.json()
            print(f"[zentra] picked up run {item.get('run_id')}", flush=True)
            task = asyncio.create_task(handle(client, item))
            inflight.add(task)
            task.add_done_callback(inflight.discard)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="zentra-agent")
    parser.add_argument("mode", choices=("validate", "run", "serve"))
    mode = parser.parse_args(argv).mode
    _check_runtime_dependencies()
    asyncio.run({"validate": validate, "run": run, "serve": serve}[mode]())


if __name__ == "__main__":
    main()
