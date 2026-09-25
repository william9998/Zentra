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


async def _post(client: httpx.AsyncClient, url: str, payload: Any, attempts: int = 5) -> None:
    for attempt in range(attempts):
        try:
            response = await client.post(url, json=payload)
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


async def run() -> None:
    gateway = os.environ["ZENTRA_GATEWAY_URL"].rstrip("/")
    run_id = os.environ["ZENTRA_RUN_ID"]
    token = os.environ["ZENTRA_RUN_TOKEN"]
    base = f"{gateway}/runs/{run_id}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=30, trust_env=False) as client:
        response = await client.get(f"{base}/bootstrap")
        response.raise_for_status()
        bootstrap = response.json()
        build, marker = _load_build()
        _, entrypoints = _manifest(marker)
        entrypoint = next(
            (item for item in entrypoints if item["name"] == bootstrap["entrypoint"]), None
        )
        if entrypoint is None:
            raise ValueError("bootstrap selected an unknown entrypoint")
        Draft202012Validator(entrypoint["input_schema"]).validate(bootstrap["input"])

        async def emit(kind: str, payload: dict[str, Any]) -> None:
            await _post(client, f"{base}/events", [{
                "event_id": _uuid7(), "type": kind, "payload": payload,
            }])

        # User ctx methods are synchronous. Queue custom/log emissions and
        # flush them before the final result without exposing network clients.
        pending: list[tuple[str, dict[str, Any]]] = []
        context = ZentraContext(run_id, bootstrap["manifest"], lambda kind, payload: pending.append((kind, payload)))
        graph = build(context)
        if not _is_uncompiled_state_graph(graph):
            raise TypeError("build(ctx) must return an uncompiled StateGraph")
        compiled = graph.compile()
        output: dict[str, Any] | None = None
        async for event in compiled.astream_events(bootstrap["input"], version="v2"):
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
        })


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="zentra-agent")
    parser.add_argument("mode", choices=("validate", "run"))
    mode = parser.parse_args(argv).mode
    _check_runtime_dependencies()
    asyncio.run(validate() if mode == "validate" else run())


if __name__ == "__main__":
    main()
