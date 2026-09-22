"""Z8-18 / Z8-20 / Z8-21 / Z8-22 / Z8-23 / Z8-24：對**真的 run 容器**做探針。

作法：用 `slow` entrypoint 發一筆 Run 讓沙盒活著，再以 `docker exec --user 65534:65534`
在**平台自己建立的那個容器**裡跑探針。不是另外 `docker run` 一個「設定一樣」的容器——
那只會驗到我自己寫的參數，驗不到平台。

⚠️ 依 ADR-0005，MVP 是 `DockerSandboxDriver`：**沒有 L2 核心隔離（gVisor），
也沒有沙盒與平台的實體隔離**。本檔驗的是 Docker 的四層控制；核心逃逸與實體隔離
在 `test_l2_kernel_isolation_is_not_covered_by_this_architecture` 明確標 skip，
不得計入通過數。
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid

import httpx
import pytest

from tests.zen008.conftest import (
    base_url,
    containers_with_label,
    docker_json,
    require_docker,
    wait_gone,
    wait_run,
)

SANDBOX_ENV = {"ZENTRA_GATEWAY_URL", "ZENTRA_RUN_ID", "ZENTRA_RUN_TOKEN"}
SLEEP_SECONDS = 45


@pytest.fixture(autouse=True)
def _needs_docker() -> None:
    require_docker()


def _exec(container: str, script: str, *, user: str = "65534:65534") -> tuple[int, str]:
    probe = subprocess.run(
        ["docker", "exec", "--user", user, container, "python", "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    return probe.returncode, (probe.stdout + probe.stderr).strip()


@pytest.fixture(scope="module")
def live_sandbox(api: httpx.Client, published_agent: dict) -> dict:
    """一個正在執行的真沙盒 + 它的 container id。module 範圍：起一次夠所有探針用。"""
    created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs", json={
        "entrypoint": "slow",
        "input": {"question": "probe", "sleep_seconds": SLEEP_SECONDS},
        "config": {"timeout_seconds": 300},
    })
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]

    container = ""
    deadline = time.time() + 90
    while time.time() < deadline and not container:
        found = containers_with_label(f"zentra.run_id={run_id}")
        running = [cid for cid in found
                   if docker_json(["inspect", cid])[0]["State"]["Running"]]
        container = running[0] if running else ""
        if not container:
            time.sleep(0.5)
    assert container, f"90 秒內沒有看到 run {run_id} 的執行中容器"
    inspect = docker_json(["inspect", container])[0]
    yield {"run_id": run_id, "container": container, "inspect": inspect}

    # 讓 Run 自己走完，之後的清理斷言才有東西可看
    wait_run(api, run_id, timeout=240)


# ------------------------------------------------------- Z8-18 P4 憑證邊界


def test_run_container_environment_is_exactly_the_three_contract_variables(
    live_sandbox: dict
) -> None:
    """P4：沙盒內拿不到任何長期憑證。多一個 env 就是多一條外洩路徑。"""
    code, out = _exec(live_sandbox["container"],
                      "import os,json;print(json.dumps(dict(os.environ)))")
    assert code == 0, out
    environment = json.loads(out)
    zentra = {key for key in environment if key.startswith("ZENTRA")}
    assert zentra == SANDBOX_ENV, f"沙盒 ZENTRA_* 不是契約的三個：{sorted(zentra)}"
    assert environment["ZENTRA_RUN_ID"] == live_sandbox["run_id"]

    blob = json.dumps(environment).lower()
    for word in ("postgres", "redis://", "password", "openai", "anthropic",
                 "api_key", "apikey", "secret", "docker.sock", "registry_auth",
                 "database_url", "identitytoken"):
        assert word not in blob, f"沙盒 env 出現 {word!r}：{sorted(environment)}"


def test_docker_inspect_config_carries_no_platform_credentials(live_sandbox: dict) -> None:
    """`docker inspect` 看得到的設定也不能有憑證（P4 的另一面）。"""
    config = live_sandbox["inspect"]["Config"]
    zentra = {item.split("=", 1)[0] for item in config["Env"] if item.startswith("ZENTRA")}
    assert zentra == SANDBOX_ENV, sorted(zentra)
    blob = json.dumps(live_sandbox["inspect"]).lower()
    for word in ("password", "identitytoken", "registrytoken",
                 "/run/secrets/zentra_registry_auth", "docker.sock"):
        assert word not in blob, f"容器設定洩漏了 {word!r}"


def test_proc_environ_inside_the_sandbox_matches_the_same_three_variables(
    live_sandbox: dict
) -> None:
    code, out = _exec(live_sandbox["container"], (
        "import glob;import json;found=set()\n"
        "for path in glob.glob('/proc/*/environ'):\n"
        "    try: raw=open(path,'rb').read().decode('utf-8','replace')\n"
        "    except Exception: continue\n"
        "    found.update(i.split('=',1)[0] for i in raw.split('\\x00') if '=' in i)\n"
        "print(json.dumps(sorted(k for k in found if k.startswith('ZENTRA'))))"
    ))
    assert code == 0, out
    assert set(json.loads(out)) == SANDBOX_ENV


# ----------------------------------------------------------- Z8-20 L1 網路


def _service_ip(container_name: str) -> str:
    networks = docker_json(["inspect", container_name])[0]["NetworkSettings"]["Networks"]
    return next(iter(networks.values()))["IPAddress"]


@pytest.mark.parametrize("target, port, label", [
    ("postgres", 5432, "service name"),
    ("redis", 6379, "service name"),
    ("1.1.1.1", 53, "public internet"),
    ("169.254.169.254", 80, "cloud metadata"),
    ("8.8.8.8", 53, "public dns"),
])
def test_sandbox_cannot_reach_anything_but_the_gateway(
    live_sandbox: dict, target: str, port: int, label: str
) -> None:
    code, out = _exec(live_sandbox["container"], (
        f"import socket,sys\n"
        f"try:\n"
        f"    socket.create_connection(({target!r}, {port}), timeout=4).close()\n"
        f"    print('REACHED')\n"
        f"except Exception as exc:\n"
        f"    print('BLOCKED', type(exc).__name__)\n"
    ))
    assert code == 0, out
    assert out.startswith("BLOCKED"), f"沙盒連得到 {label} {target}:{port} → {out}"


def test_sandbox_cannot_reach_the_data_layer_by_ip_either(live_sandbox: dict) -> None:
    """名稱解析失敗不算隔離。直接用 postgres/redis 在 zentra-net 上的 IP 再試一次。"""
    for name, port in (("zentra-postgres-1", 5432), ("zentra-redis-1", 6379)):
        address = _service_ip(name)
        assert address, f"取不到 {name} 的 IP，測試前提不成立"
        code, out = _exec(live_sandbox["container"], (
            f"import socket\n"
            f"try:\n"
            f"    socket.create_connection(({address!r}, {port}), timeout=4).close()\n"
            f"    print('REACHED')\n"
            f"except Exception as exc:\n"
            f"    print('BLOCKED', type(exc).__name__)\n"
        ))
        assert code == 0, out
        assert out.startswith("BLOCKED"), f"沙盒直連得到 {name} 的 IP {address}:{port}"


def test_sandbox_can_reach_the_internal_gateway(live_sandbox: dict) -> None:
    """反向斷言。漏了這條，上面五條「全部連不到」就可能只是容器沒有網路。"""
    code, out = _exec(live_sandbox["container"], (
        "import os,urllib.request\n"
        "url=os.environ['ZENTRA_GATEWAY_URL'].rstrip('/')+'/runs/'+os.environ['ZENTRA_RUN_ID']+'/bootstrap'\n"
        "req=urllib.request.Request(url, headers={'Authorization':'Bearer '+os.environ['ZENTRA_RUN_TOKEN']})\n"
        "print(urllib.request.urlopen(req, timeout=10).status)"
    ))
    assert code == 0, out
    assert out.strip().endswith("200"), out


def test_sandbox_is_only_attached_to_the_internal_network(live_sandbox: dict) -> None:
    settings = live_sandbox["inspect"]["NetworkSettings"]
    networks = set(settings["Networks"])
    assert networks == {"zentra-sandbox-net"}, networks
    assert docker_json(["network", "inspect", "zentra-sandbox-net"])[0]["Internal"] is True
    assert not settings["Ports"], f"沙盒發佈了 port：{settings['Ports']}"
    assert live_sandbox["inspect"]["HostConfig"]["NetworkMode"] == "zentra-sandbox-net"
    assert not live_sandbox["inspect"]["HostConfig"]["PortBindings"]


# ------------------------------------------------ Z8-21 L2（Docker hardening）


def test_sandbox_process_is_not_root(live_sandbox: dict) -> None:
    code, out = _exec(live_sandbox["container"], "import os;print(os.getuid(), os.getgid())")
    assert code == 0, out
    assert out.strip() == "65534 65534", out
    assert live_sandbox["inspect"]["Config"]["User"] == "65534:65534"


def test_sandbox_drops_capabilities_and_forbids_privilege_escalation(
    live_sandbox: dict
) -> None:
    host = live_sandbox["inspect"]["HostConfig"]
    assert host["CapDrop"] == ["ALL"], host["CapDrop"]
    assert not host["CapAdd"]
    assert "no-new-privileges:true" in (host["SecurityOpt"] or []), host["SecurityOpt"]
    assert host["Privileged"] is False


@pytest.mark.parametrize("script, label", [
    ("import os;os.unshare(os.CLONE_NEWUSER);print('REACHED')", "unshare"),
    # PTRACE_ATTACH(16) 到容器內的另一個行程 —— 這才是會跨越邊界的那個操作。
    # （PTRACE_TRACEME 只是把自己標成被追蹤，同 uid 自己追自己，Docker 預設
    #  seccomp 允許，它不是逃逸原語，所以不在這裡斷言。）
    ("import ctypes;libc=ctypes.CDLL('libc.so.6',use_errno=True);"
     "print('REACHED' if libc.ptrace(16,1,0,0)==0 else 'BLOCKED')", "ptrace attach"),
    ("import os;os.mkdir('/tmp/p');os.system('mount -t proc proc /tmp/p');"
     "print('REACHED' if os.path.exists('/tmp/p/1') else 'BLOCKED')", "mount proc"),
])
def test_privileged_kernel_operations_fail_inside_the_sandbox(
    live_sandbox: dict, script: str, label: str
) -> None:
    code, out = _exec(live_sandbox["container"], script)
    assert "REACHED" not in out, f"沙盒內 {label} 成功了：{out}"


def test_effective_capability_set_is_empty(live_sandbox: dict) -> None:
    """`cap_drop: ALL` 的實測面：CapEff 必須是 0，NoNewPrivs 必須是 1。"""
    code, out = _exec(live_sandbox["container"], (
        "status=open('/proc/self/status').read()\n"
        "line=lambda k:[l for l in status.splitlines() if l.startswith(k)][0].split()[1]\n"
        "print(line('CapEff:'), line('NoNewPrivs:'))"
    ))
    assert code == 0, out
    effective, no_new_privs = out.split()
    assert int(effective, 16) == 0, f"沙盒仍持有 capability：CapEff={effective}"
    assert no_new_privs == "1"


def test_docker_socket_is_not_visible_inside_the_sandbox(live_sandbox: dict) -> None:
    code, out = _exec(live_sandbox["container"],
                      "import os;print(os.path.exists('/var/run/docker.sock'))")
    assert code == 0, out
    assert out.strip() == "False"
    binds = live_sandbox["inspect"]["HostConfig"]["Binds"]
    assert not binds, f"沙盒有 bind mount：{binds}"


def test_l2_kernel_isolation_is_not_covered_by_this_architecture() -> None:
    """ADR-0005 的已知取捨，**不是缺陷，也不能算通過**。"""
    from apps.sandbox.drivers.docker_driver import DockerSandboxDriver
    import asyncio

    caps = asyncio.run(DockerSandboxDriver.capabilities(DockerSandboxDriver.__new__(
        DockerSandboxDriver)))
    assert caps.l2_kernel_isolation is False, (
        "driver 宣稱有核心隔離，但 MVP 沒有 gVisor —— 這是假宣稱，比沒有更危險")
    pytest.skip(
        "gVisor／核心逃逸／沙盒與平台實體隔離：ADR-0005 明定不在 MVP 範圍，未覆蓋"
    )


# ----------------------------------------------------------- Z8-22 L3 資源


def test_resource_limits_are_applied_to_the_real_container(live_sandbox: dict) -> None:
    host = live_sandbox["inspect"]["HostConfig"]
    assert host["Memory"] == 2 * 1024 ** 3, host["Memory"]
    assert host["MemorySwap"] == host["Memory"], "swap 沒有被關掉"
    assert host["PidsLimit"] == 256, host["PidsLimit"]
    assert host["NanoCpus"] == 2_000_000_000, host["NanoCpus"]
    assert host["OomKillDisable"] in (False, None)
    assert host["LogConfig"]["Config"].get("max-size") == "10m"


def test_fork_bomb_is_contained_by_the_pids_limit(live_sandbox: dict) -> None:
    """pids 上限必須真的擋得住，而且擋完容器還活著（不影響同一 Run 的其他行為）。"""
    code, out = _exec(live_sandbox["container"], (
        "import os\n"
        "spawned=0\n"
        "try:\n"
        "    while spawned < 2000:\n"
        "        if os.fork()==0: os._exit(0)\n"
        "        spawned+=1\n"
        "except Exception as exc:\n"
        "    print('LIMITED', spawned, type(exc).__name__)\n"
        "else:\n"
        "    print('UNLIMITED', spawned)\n"
    ))
    assert "UNLIMITED" not in out, f"fork bomb 沒有被 pids 上限擋下：{out}"
    assert "LIMITED" in out, out
    assert int(out.split()[1]) < 256, out
    assert docker_json(["inspect", live_sandbox["container"]])[0]["State"]["Running"] is True


# ----------------------------------------------------------- Z8-23 L4 檔案


@pytest.mark.parametrize("path", ["/zentra-probe", "/app/zentra-probe",
                                  "/opt/zentra/zentra-probe", "/etc/zentra-probe",
                                  "/usr/local/bin/zentra-probe"])
def test_sandbox_cannot_write_outside_workspace(live_sandbox: dict, path: str) -> None:
    code, out = _exec(live_sandbox["container"], (
        f"try:\n"
        f"    open({path!r},'w').write('x')\n"
        f"    print('WROTE')\n"
        f"except Exception as exc:\n"
        f"    print('READONLY', type(exc).__name__)\n"
    ))
    assert code == 0, out
    assert out.startswith("READONLY"), f"沙盒寫得進 {path} → {out}"


def test_workspace_is_the_only_writable_mount(live_sandbox: dict) -> None:
    code, out = _exec(live_sandbox["container"], (
        "open('/workspace/probe','w').write('x');print(open('/workspace/probe').read())"
    ))
    assert code == 0 and out.strip() == "x", out

    host = live_sandbox["inspect"]["HostConfig"]
    assert host["ReadonlyRootfs"] is True
    assert set(host["Tmpfs"] or {}) == {"/workspace"}, host["Tmpfs"]
    assert "size=512m" in host["Tmpfs"]["/workspace"]
    assert "noexec" in host["Tmpfs"]["/workspace"]
    mounts = live_sandbox["inspect"]["Mounts"]
    assert not [m for m in mounts if m.get("Type") == "bind"], mounts
    assert not [m for m in mounts if m.get("Type") == "volume"], (
        "image 宣告的 VOLUME 變成了 writable 匿名 volume", mounts)


def test_workspace_content_does_not_leak_into_the_next_sandbox(
    api: httpx.Client, published_agent: dict, live_sandbox: dict
) -> None:
    """一 Run 一盒：前一盒寫進 tmpfs 的東西，下一盒讀不到。"""
    marker = uuid.uuid4().hex
    code, out = _exec(live_sandbox["container"],
                      f"open('/workspace/secret','w').write({marker!r});print('ok')")
    assert code == 0, out

    created = api.post(f"/v1/agents/{published_agent['agent_id']}/runs", json={
        "entrypoint": "slow",
        "input": {"question": "second", "sleep_seconds": 20},
        "config": {"timeout_seconds": 120},
    })
    run_id = created.json()["run_id"]
    second = ""
    deadline = time.time() + 90
    while time.time() < deadline and not second:
        running = [cid for cid in containers_with_label(f"zentra.run_id={run_id}")
                   if docker_json(["inspect", cid])[0]["State"]["Running"]]
        second = running[0] if running else ""
        time.sleep(0.5)
    assert second, "第二個沙盒沒有起來"
    assert second != live_sandbox["container"], "兩筆 Run 共用了同一個容器"

    code, out = _exec(second, (
        "import os;print(sorted(os.listdir('/workspace')))"
    ))
    assert code == 0, out
    assert marker not in out and "secret" not in out, f"前一盒的 /workspace 外洩：{out}"
    wait_run(api, run_id, timeout=180)


# ------------------------------------------------------- Z8-24 token 交叉權限


def _token(container: str) -> str:
    code, out = _exec(container, "import os;print(os.environ['ZENTRA_RUN_TOKEN'])")
    assert code == 0, out
    return out.strip()


def test_run_token_cannot_touch_another_run(api: httpx.Client, live_sandbox: dict) -> None:
    """`/internal/*` 每一條路由都要驗，漏一條就是一個越權入口。"""
    token = _token(live_sandbox["container"])
    other = api.get("/v1/runs", params={"limit": 20}).json()["items"]
    victim = next(item["run_id"] for item in other
                  if item["run_id"] != live_sandbox["run_id"])

    with httpx.Client(base_url=base_url(), timeout=20) as client:
        headers = {"Authorization": f"Bearer {token}"}
        cases = [
            ("GET", f"/internal/runs/{victim}/bootstrap", None),
            ("POST", f"/internal/runs/{victim}/events", [{
                "event_id": str(uuid.uuid4()), "type": "log",
                "payload": {"level": "info", "message": "x"}}]),
            ("POST", f"/internal/runs/{victim}/result",
             {"output": {"echo": "x"}, "error": None, "usage": {}}),
        ]
        for method, path, body in cases:
            response = client.request(method, path, json=body, headers=headers)
            assert response.status_code == 403, (path, response.status_code, response.text)
            assert response.json()["error"]["code"] == "PERMISSION_DENIED"

        # 同一個 run token 也不能去驗證版本（scope 不可互換）
        version_id = str(uuid.uuid4())
        response = client.post(f"/internal/versions/{version_id}/validation",
                               json={"valid": True, "runtime_contract": "zentra.image/v1",
                                     "manifest": {}, "entrypoints": [], "error": None},
                               headers=headers)
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    victim_after = api.get(f"/v1/runs/{victim}").json()
    assert victim_after["output"] != {"echo": "x"}, "越權請求改到了別的 Run 的資料"


def test_missing_and_bogus_tokens_are_unauthenticated(live_sandbox: dict) -> None:
    with httpx.Client(base_url=base_url(), timeout=20) as client:
        path = f"/internal/runs/{live_sandbox['run_id']}/bootstrap"
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "nope"}):
            response = client.get(path, headers=headers)
            assert response.status_code == 401, (headers, response.text)
            assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_run_token_stops_working_and_container_is_gone_after_the_run_ends(
    api: httpx.Client, live_sandbox: dict
) -> None:
    token = _token(live_sandbox["container"])
    run = wait_run(api, live_sandbox["run_id"], timeout=240)
    assert run["status"] in {"SUCCEEDED", "FAILED"}, run

    with httpx.Client(base_url=base_url(), timeout=20) as client:
        response = client.get(f"/internal/runs/{live_sandbox['run_id']}/bootstrap",
                              headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    remaining = wait_gone(f"zentra.run_id={live_sandbox['run_id']}", timeout=60)
    assert not remaining, f"終態後沙盒仍存在：{remaining}"
    probe = subprocess.run(["docker", "inspect", live_sandbox["container"]],
                           capture_output=True, text=True)
    assert probe.returncode != 0, "docker inspect 仍找得到已終態 Run 的容器"
