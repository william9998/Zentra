"""Z8-23 的關鍵反例：image 宣告的 `VOLUME` 會不會變成 `/workspace` 以外的可寫路徑。

@platform-eng 在交接單 §6/§8 自己標了這個風險：
「若 SDK/daemon 把 image VOLUME 合併回來，可能多出 writable volume …
  Z8-23 必須 inspect Mounts；失敗不得放行」。

這一檔就是去證實它。fixture image 宣告 `VOLUME /escape` 並且**把目錄 chown 給 65534**
（沙盒執行身分）—— 只有這樣才問得出真正的問題：
沙盒裡的程式碼寫得進去嗎？容器銷毀後資料還在宿主機上嗎？
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid

import httpx
import pytest

from tests.zen008.conftest import (
    containers_with_label,
    docker_json,
    new_agent,
    register_version,
    require_registry,
    wait_gone,
    wait_run,
    wait_version,
)


@pytest.fixture(autouse=True)
def _needs_registry() -> None:
    require_registry()


@pytest.fixture(scope="module")
def escaping_sandbox(api: httpx.Client, image_refs: dict) -> dict:
    agent_id = new_agent(api, "zen008-volume")
    assert register_version(api, agent_id, "1.0.0", image_refs["volume_escape"]).status_code == 202
    assert wait_version(api, agent_id, "1.0.0")["status"] == "PUBLISHED"

    created = api.post(f"/v1/agents/{agent_id}/runs", json={
        "entrypoint": "slow",
        "input": {"question": "volume", "sleep_seconds": 40},
        "config": {"timeout_seconds": 180},
    })
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]

    container = ""
    deadline = time.time() + 90
    while time.time() < deadline and not container:
        running = [cid for cid in containers_with_label(f"zentra.run_id={run_id}")
                   if docker_json(["inspect", cid])[0]["State"]["Running"]]
        container = running[0] if running else ""
        time.sleep(0.5)
    assert container, "宣告 VOLUME 的 image 沒有起出容器"
    return {"agent_id": agent_id, "run_id": run_id, "container": container,
            "inspect": docker_json(["inspect", container])[0]}


def test_image_declared_volume_must_not_become_a_writable_mount(
    api: httpx.Client, escaping_sandbox: dict
) -> None:
    """唯一可寫的地方必須是 512 MiB 的 `/workspace` tmpfs。

    擋住的錯：使用者 image 只要寫一行 `VOLUME /x`，就多出一條
    平台沒有審過、沒有容量上限、而且**寫在宿主磁碟上**的可寫路徑。
    """
    mounts = escaping_sandbox["inspect"]["Mounts"]
    volumes = [m for m in mounts if m.get("Type") == "volume"]

    marker = uuid.uuid4().hex
    probe = subprocess.run(
        ["docker", "exec", "--user", "65534:65534", escaping_sandbox["container"],
         "python", "-c",
         f"open('/escape/leak.txt','w').write({marker!r});print('WROTE')"],
        capture_output=True, text=True, timeout=60)
    wrote = "WROTE" in probe.stdout

    detail = {
        "volume_mounts": [{k: m.get(k) for k in ("Destination", "RW", "Source")}
                          for m in volumes],
        "sandbox_write": probe.stdout.strip() or probe.stderr.strip(),
    }
    assert not volumes, (
        "image 宣告的 VOLUME 被合併成匿名 volume；沙盒可寫路徑不只 /workspace tmpfs: "
        + json.dumps(detail, ensure_ascii=False))
    assert not wrote, detail


def test_anonymous_volume_data_survives_the_destroyed_sandbox(
    api: httpx.Client, escaping_sandbox: dict
) -> None:
    """如果上一條已經紅了，這條說明它有多糟：資料留在宿主機上。

    Run 終態後容器被銷毀，但匿名 volume 不會跟著消失 ——
    「終態後 tmpfs 不殘留」這個保證只涵蓋 `/workspace`。
    """
    volumes = [m["Name"] for m in escaping_sandbox["inspect"]["Mounts"]
               if m.get("Type") == "volume" and m.get("Destination") == "/escape"]
    if not volumes:
        pytest.skip("沒有匿名 volume（前一條測試若為綠，這條本來就不適用）")

    marker = uuid.uuid4().hex
    subprocess.run(
        ["docker", "exec", "--user", "65534:65534", escaping_sandbox["container"],
         "python", "-c", f"open('/escape/leak.txt','w').write({marker!r})"],
        capture_output=True, text=True, timeout=60)

    wait_run(api, escaping_sandbox["run_id"], timeout=240)
    assert not wait_gone(f"zentra.run_id={escaping_sandbox['run_id']}", timeout=60)

    name = volumes[0]
    still_there = subprocess.run(["docker", "volume", "inspect", name],
                                 capture_output=True, text=True)
    content = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{name}:/v", "alpine:latest",
         "cat", "/v/leak.txt"], capture_output=True, text=True, timeout=120)
    assert still_there.returncode != 0 or marker not in content.stdout, (
        f"沙盒銷毀後匿名 volume {name} 仍在宿主機上，而且還留著沙盒寫的資料："
        f"{content.stdout.strip()[:80]}")
