"""併發 Run：一次丟出超過上限的 Run，看平台守不守得住 SPEC §10 的併發上限（預設 10）。

對 builtin-echo（或 --agent-id 指定的 Agent）同時 POST N 筆 Run，期間每 0.5 秒取樣 Sandbox Manager 的
inflight 計數，最後等全部到終態再統計。

判定（全部成立才算過）：
  1. 取樣到的 inflight 峰值 ≤ 上限 —— 上限有守住
  2. 每一筆都到了終態，沒有卡在 QUEUED / SCHEDULING / RUNNING
  3. 沒成功的只能是 FAILED + QUOTA_EXCEEDED —— 超量是被拒，不是把平台壓壞

**會建立 Run**。跑完用 `make clean-test-data` 收掉（builtin-echo 的 Run 在清除範圍內）。

    python3 tests/load/concurrent_runs.py
    python3 tests/load/concurrent_runs.py --runs 20 --limit 10
"""

import argparse
import asyncio
import json
import sys
import time
from collections import Counter

import httpx

from _common import BASE_URL, BUILTIN_AGENT_ID, sandbox_inflight, summarize_ms
from apps.api.enums import ErrorCode, RunStatus

#: 單筆 Run 等到終態的上限。echo 正常幾秒就結束；排隊的會等前面的讓出位置。
TERMINAL_TIMEOUT_S = 300


async def sample_inflight(stop: asyncio.Event, samples: list[int]) -> None:
    while not stop.is_set():
        value = await asyncio.to_thread(sandbox_inflight)
        if value is not None:
            samples.append(value)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.5)
        except TimeoutError:
            pass


async def create_run(client: httpx.AsyncClient, agent_id: str, index: int) -> tuple[str | None, float, int]:
    started = time.perf_counter()
    response = await client.post(
        f"/v1/agents/{agent_id}/runs",
        json={"input": {"question": f"load-{index}"}},
    )
    elapsed = time.perf_counter() - started
    run_id = response.json()["run_id"] if response.status_code == 202 else None
    return run_id, elapsed, response.status_code


async def wait_terminal(client: httpx.AsyncClient, run_id: str) -> dict:
    deadline = time.monotonic() + TERMINAL_TIMEOUT_S
    while True:
        body = (await client.get(f"/v1/runs/{run_id}")).json()
        if RunStatus(body["status"]).is_terminal or time.monotonic() > deadline:
            return body
        await asyncio.sleep(0.5)


async def main(agent_id: str, runs: int, limit: int) -> int:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        if (await client.get(f"/v1/agents/{agent_id}")).status_code == 404:
            print(f"Agent {agent_id} 不存在。builtin-echo 被刪了的話要先補回來，"
                  "或用 --agent-id 指定別的 Agent（見 README）", file=sys.stderr)
            return 2

    samples: list[int] = []
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample_inflight(stop, samples))

    started = time.perf_counter()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        created = await asyncio.gather(*(create_run(client, agent_id, i) for i in range(runs)))
        run_ids = [run_id for run_id, _, _ in created if run_id]
        finals = await asyncio.gather(*(wait_terminal(client, run_id) for run_id in run_ids))
    wall = time.perf_counter() - started

    stop.set()
    await sampler

    outcomes = Counter(
        f"{body['status']}:{(body.get('error') or {}).get('code')}"
        if body["status"] == RunStatus.FAILED else body["status"]
        for body in finals
    )
    stuck = [body["run_id"] for body in finals if not RunStatus(body["status"]).is_terminal]
    unexpected = [
        body["run_id"] for body in finals
        if body["status"] != RunStatus.SUCCEEDED
        and (body["status"] != RunStatus.FAILED
             or (body.get("error") or {}).get("code") != ErrorCode.QUOTA_EXCEEDED)
    ]
    rejected_at_post = Counter(code for run_id, _, code in created if run_id is None)
    peak = max(samples) if samples else None

    checks = {
        "peak_inflight_within_limit": peak is not None and peak <= limit,
        "all_terminal": not stuck,
        "only_quota_failures": not unexpected,
        "all_posts_accepted": not rejected_at_post,
    }
    report = {
        "runs": runs,
        "limit": limit,
        "wall_seconds": round(wall, 2),
        "post_latency": summarize_ms([elapsed for _, elapsed, _ in created]),
        "post_rejected_by_status": dict(rejected_at_post),
        "outcomes": dict(outcomes),
        "inflight_peak": peak,
        "inflight_samples": len(samples),
        "stuck_run_ids": stuck,
        "unexpected_run_ids": unexpected[:10],
        "checks": checks,
        "passed": all(checks.values()),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("\n提醒：這次建立了 Run，收尾請跑 make clean-test-data", file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=15, help="同時丟出的 Run 數（預設 15，超過上限才測得到）")
    parser.add_argument("--limit", type=int, default=10, help="預期的併發上限，對應 SANDBOX_MAX_CONCURRENT（預設 10）")
    parser.add_argument("--agent-id", default=BUILTIN_AGENT_ID, help="對哪個 Agent 發 Run（預設 builtin-echo）")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.agent_id, args.runs, args.limit)))
