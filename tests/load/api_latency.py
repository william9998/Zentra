"""API 延遲：對唯讀端點打固定併發的請求，看 P95 是否 < 100ms（SPEC §10）。

只打讀取端點 —— 不建立 Run、不開沙盒，所以跑完不會留下資料。
量到的是「主機上的 client → app → postgres」整段來回，比 SPEC 說的
「額外延遲」多算了網路與序列化，是保守的上界。

    python3 tests/load/api_latency.py
    python3 tests/load/api_latency.py --requests 500 --concurrency 20
"""

import argparse
import asyncio
import json
import sys
import time

import httpx

from _common import BASE_URL, summarize_ms

TARGET_P95_MS = 100

LIST_ENDPOINTS = ["/v1/agents", "/v1/runs?limit=20"]


async def hammer(client: httpx.AsyncClient, path: str, total: int, concurrency: int) -> tuple[list[float], int]:
    durations: list[float] = []
    errors = 0
    gate = asyncio.Semaphore(concurrency)

    async def one() -> None:
        nonlocal errors
        async with gate:
            started = time.perf_counter()
            response = await client.get(path)
            elapsed = time.perf_counter() - started
            if response.status_code == 200:
                durations.append(elapsed)
            else:
                errors += 1

    await asyncio.gather(*(one() for _ in range(total)))
    return durations, errors


async def main(total: int, concurrency: int) -> int:
    report = {"target_p95_ms": TARGET_P95_MS, "concurrency": concurrency, "endpoints": {}}
    passed = True
    limits = httpx.Limits(max_connections=concurrency)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0, limits=limits) as client:
        # 單一 Agent 的詳情頁拿列表裡的第一個來打，不綁特定 fixture。
        endpoints = list(LIST_ENDPOINTS)
        agents = (await client.get("/v1/agents")).json()["items"]
        if agents:
            endpoints.insert(1, f"/v1/agents/{agents[0]['agent_id']}")

        # 暖機：第一次連線與 DB 連線池建立不算進去。
        for path in endpoints:
            (await client.get(path)).raise_for_status()

        for path in endpoints:
            durations, errors = await hammer(client, path, total, concurrency)
            if not durations:
                report["endpoints"][path] = {"errors": errors}
                passed = False
                continue
            stats = summarize_ms(durations)
            stats["errors"] = errors
            stats["p95_ok"] = stats["p95_ms"] < TARGET_P95_MS and errors == 0
            passed &= stats["p95_ok"]
            report["endpoints"][path] = stats

    report["passed"] = passed
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--requests", type=int, default=200, help="每個端點打幾次（預設 200）")
    parser.add_argument("--concurrency", type=int, default=10, help="同時在飛的請求數（預設 10）")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.requests, args.concurrency)))
