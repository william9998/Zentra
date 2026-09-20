"""Disposable sandbox process: bootstrap, compile, emit events, submit result."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from .builtin_agent import build

log = logging.getLogger("zentra.runtime")


def _event_id() -> str:
    """RFC 9562 UUIDv7; created once per event and reused on retries."""
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


async def _post_with_retry(
    client: httpx.AsyncClient, url: str, payload: Any, *, attempts: int = 5
) -> None:
    """Retry transient uncertainty with the identical payload/event_id."""
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
        await asyncio.sleep(min(0.2 * 2**attempt, 2.0))


async def _execute(client: httpx.AsyncClient, gateway: str, run_id: str) -> None:
    base = f"{gateway.rstrip('/')}/runs/{run_id}"
    response = await client.get(f"{base}/bootstrap")
    response.raise_for_status()
    bootstrap = response.json()
    if bootstrap.get("run_id") != run_id or bootstrap.get("entrypoint") != "default":
        raise ValueError("bootstrap did not match this built-in Run")
    run_input = bootstrap.get("input")
    if not isinstance(run_input, dict) or not isinstance(run_input.get("question"), str):
        raise ValueError("built-in Run requires a string question")

    # Only the sandbox platform runtime compiles the Agent's StateGraph.
    compiled = build().compile()
    output: dict[str, Any] | None = None
    node_names = {"echo_node", "finalize_node"}
    async for event in compiled.astream_events(run_input, version="v2"):
        name = event.get("name")
        kind = event.get("event")
        if name in node_names and kind in {"on_chain_start", "on_chain_end"}:
            body = [{
                "event_id": _event_id(),
                "type": "node.start" if kind == "on_chain_start" else "node.end",
                "payload": {"node": name, "ts": _timestamp()},
            }]
            await _post_with_retry(client, f"{base}/events", body)
        elif name == "LangGraph" and kind == "on_chain_end":
            output = event.get("data", {}).get("output")

    if not isinstance(output, dict) or not isinstance(output.get("echo"), str):
        raise RuntimeError("compiled graph returned no echo")
    await _post_with_retry(client, f"{base}/result", {
        "output": {"echo": output["echo"]},
        "error": None,
        "usage": {
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": "0",
        },
    })


async def main() -> None:
    run_id = os.environ["ZENTRA_RUN_ID"]
    gateway = os.environ["ZENTRA_GATEWAY"]
    token = os.environ["ZENTRA_RUN_TOKEN"]
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
        trust_env=False,
    ) as client:
        await _execute(client, gateway, run_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except Exception:
        log.exception("sandbox runtime failed")
        raise
