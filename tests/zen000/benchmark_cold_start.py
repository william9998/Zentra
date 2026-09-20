"""Opt-in 40-Run cold-start upper-bound measurement for ZEN-000 A1.

Run inside a Compose worker container, where Docker and Postgres are reachable.
The POST-to-first-node.start duration includes queueing, container creation,
bootstrap, graph startup, Gateway persistence, and SSE delivery. Therefore a
P95 below 2 seconds is a conservative bound for create/start-to-bootstrap.
"""

import json
import math
import os
import time
from uuid import UUID

import docker
import httpx
from docker.errors import NotFound

from apps.api.models import Run
from apps.worker.db import SessionSync


AGENT_ID = "01930000-0000-7000-8000-000000000001"


def wait_destroyed(client: docker.DockerClient, container_id: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            client.containers.get(container_id)
        except NotFound:
            return
        time.sleep(0.1)
    raise AssertionError(f"sandbox {container_id} remains after terminal Run")


def main() -> None:
    base_url = os.environ["ZENTRA_TEST_BASE_URL"]
    durations: list[float] = []
    sandbox_ids: list[str] = []
    docker_client = docker.from_env()
    try:
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(45.0, connect=3.0)) as client:
            for index in range(40):
                started = time.perf_counter()
                response = client.post(
                    f"/v1/agents/{AGENT_ID}/runs",
                    json={"input": {"question": f"cold-start-{index}"}},
                )
                assert response.status_code == 202, response.text
                run_id = response.json()["run_id"]
                first_node = None
                terminal = False
                with client.stream("GET", f"/v1/runs/{run_id}/events") as stream:
                    assert stream.status_code == 200, stream.text
                    for line in stream.iter_lines():
                        if line == "event: node.start" and first_node is None:
                            first_node = time.perf_counter()
                        if line == "event: run.status":
                            # Terminal status is confirmed by GET after stream end.
                            terminal = True
                assert first_node is not None, f"Run {run_id} had no node.start"
                assert terminal, f"Run {run_id} had no run.status SSE"
                final = client.get(f"/v1/runs/{run_id}")
                assert final.status_code == 200, final.text
                assert final.json()["status"] == "SUCCEEDED", final.text
                assert final.json()["output"] == {"echo": f"cold-start-{index}"}

                with SessionSync() as session:
                    run = session.get(Run, UUID(run_id))
                    assert run is not None and run.sandbox_id
                    container_id = run.sandbox_id
                wait_destroyed(docker_client, container_id)
                durations.append(first_node - started)
                sandbox_ids.append(container_id)

        assert len(set(sandbox_ids)) == 40, "sandbox container was reused"
        ordered = sorted(durations)
        p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
        print(json.dumps({
            "count": len(durations),
            "unique_containers": len(set(sandbox_ids)),
            "destroyed_containers": len(sandbox_ids),
            "metric": "POST-to-first-node.start SSE upper bound (seconds)",
            "min": round(ordered[0], 3),
            "median": round(ordered[len(ordered) // 2], 3),
            "p95_nearest_rank": round(p95, 3),
            "max": round(ordered[-1], 3),
            "p95_under_2s": p95 < 2,
        }, sort_keys=True))
        assert p95 < 2, "A1 P95 conservative upper bound exceeded 2 seconds"
    finally:
        docker_client.close()


if __name__ == "__main__":
    main()
