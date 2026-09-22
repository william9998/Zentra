"""Z8-06 / Z8-07 / Z8-09 / Z8-10 / Z8-13 / Z8-14：`q.build` 的真 Docker + 真 Registry 證據。

這一檔全部要真 daemon 與本機 Registry。沒有 Docker 就 `skip`，**不是 pass**
（acceptance §3 判定規則：因環境缺失 skipped 不得計入通過）。
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.models import AgentVersion
from apps.sandbox.drivers.base import OCI_RUNTIME_CONTRACT
from tests.zen008.conftest import (
    containers_with_label,
    db_url,
    docker_json,
    new_agent,
    register_version,
    require_registry,
    wait_gone,
    wait_run,
    wait_version,
)
from tests.zen008.fixtures import images


@pytest.fixture(autouse=True)
def _needs_registry() -> None:
    require_registry()


def _image_present(ref: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", ref],
                          capture_output=True, text=True).returncode == 0


def _forget_local(ref: str) -> None:
    """只移除本機對該 digest 的 reference，Registry 的 manifest 不動。"""
    subprocess.run(["docker", "image", "rm", "-f", ref], capture_output=True, text=True)


async def _version_row(agent_id: str, semver: str) -> AgentVersion:
    engine = create_async_engine(db_url())
    try:
        async with async_sessionmaker(engine)() as session:
            return (await session.execute(select(AgentVersion).where(
                AgentVersion.agent_id == uuid.UUID(agent_id),
                AgentVersion.semver == semver))).scalar_one()
    finally:
        await engine.dispose()


# --------------------------------------------------------------------- Z8-06


async def test_publish_records_inspected_metadata_from_the_exact_digest(
    api: httpx.Client, published_agent: dict
) -> None:
    """發布成功後，DB 的 artifact 必須來自真 inspect，不是請求裡抄來的。"""
    version = published_agent["version"]
    assert version["status"] == "PUBLISHED"
    assert version["artifact"]["image_ref"] == published_agent["image_ref"]
    assert version["artifact"]["digest"] == published_agent["image_ref"].split("@", 1)[1]
    assert version["artifact"]["runtime_contract"] == OCI_RUNTIME_CONTRACT
    assert version["artifact"]["size_bytes"] and version["artifact"]["size_bytes"] > 0
    assert version["artifact"]["platform"].startswith("linux/")
    assert version["error"] is None

    local = docker_json(["image", "inspect", published_agent["image_ref"]])[0]
    assert published_agent["image_ref"] in local["RepoDigests"]
    assert local["Config"]["Labels"]["io.zentra.image-contract"] == OCI_RUNTIME_CONTRACT
    host = docker_json(["system", "info", "--format", "{{json .}}"])
    expected = f"{host['OSType']}/{host['Architecture']}".replace("aarch64", "arm64")
    assert version["artifact"]["platform"].startswith(expected), (
        version["artifact"]["platform"], expected)

    row = await _version_row(published_agent["agent_id"], published_agent["semver"])
    assert row.published_at is not None
    assert row.manifest["runtime"]["entry"] == "agent:build"
    assert [item["name"] for item in row.entrypoints] == ["default", "slow"]


def test_cache_miss_pulls_the_exact_digest_from_the_registry(
    api: httpx.Client, image_refs: dict
) -> None:
    """先把本機 cache 的那個 digest 忘掉，再發布。

    擋住的錯：worker 只認本機 cache，或用 tag 去 resolve。cache miss 時它必須
    按 DB 的 exact digest 重拉，拉完 RepoDigests 要含同一個 reference。
    """
    ref = image_refs["cache_probe"]
    _forget_local(ref)
    assert not _image_present(ref), "測試前置失敗：本機仍有這個 digest"

    agent_id = new_agent(api, "zen008-cachemiss")
    assert register_version(api, agent_id, "1.0.0", ref).status_code == 202
    version = wait_version(api, agent_id, "1.0.0")
    assert version["status"] == "PUBLISHED", version
    assert _image_present(ref), "發布成功但本機沒有該 digest，代表沒有真的 pull"
    assert ref in docker_json(["image", "inspect", ref])[0]["RepoDigests"]


# --------------------------------------------------------------------- Z8-07


def test_tag_repoint_does_not_change_what_a_published_version_runs(
    api: httpx.Client, image_refs: dict
) -> None:
    """P5 的核心：`zentra-qa/agent:stable` 這個 tag 現在指向 v2（fixture build 時
    最後 push 的是 v2），但用 v1 digest 發布的版本必須永遠跑 v1。

    擋住的錯：任何一處把 digest 換成 repository:tag 去 resolve——
    那會讓「已發布版本的行為」被 Registry 端的 tag 移動偷偷改掉。
    """
    v1, v2 = image_refs["valid_v1"], image_refs["valid_v2"]
    assert v1.split("@")[0] == v2.split("@")[0], "fixture 前提：同一個 repository"
    assert v1 != v2

    tag = f"{images.REGISTRY}/zentra-qa/agent:stable"
    current = docker_json(["image", "inspect", tag])[0]
    assert v2 in current["RepoDigests"], ("tag 應該已經被重指到 v2", current["RepoDigests"])

    agent_id = new_agent(api, "zen008-tagpin")
    assert register_version(api, agent_id, "1.0.0", v1).status_code == 202
    assert wait_version(api, agent_id, "1.0.0")["status"] == "PUBLISHED"

    for _ in range(2):
        created = api.post(f"/v1/agents/{agent_id}/runs",
                           json={"input": {"question": "pinned"}}).json()
        run = wait_run(api, created["run_id"])
        assert run["status"] == "SUCCEEDED", run
        assert run["output"]["mark"] == "v1", (
            "tag 被重指之後跑出了 v2 的輸出 —— digest 沒有被當成權威身份")


# ------------------------------------------------------------ Z8-09 / Z8-10


@pytest.mark.parametrize("fixture_name, stage, reason", [
    ("no_label", "INSPECT", "缺 io.zentra.image-contract label"),
    ("compiled_graph", "VALIDATE", "build() 回傳已 compile 的 graph（違反 P2）"),
])
async def test_invalid_images_fail_at_the_right_stage(
    api: httpx.Client, image_refs: dict, fixture_name: str, stage: str, reason: str
) -> None:
    agent_id = new_agent(api, f"zen008-{fixture_name.replace('_', '-')}")
    assert register_version(api, agent_id, "1.0.0", image_refs[fixture_name]).status_code == 202
    version = wait_version(api, agent_id, "1.0.0")

    assert version["status"] == "FAILED", (reason, version)
    assert version["error"]["code"] == "BUILD_FAILED"
    assert version["error"]["details"]["stage"] == stage, (reason, version["error"])
    assert version["manifest"] is None
    assert version["entrypoints"] is None
    assert version["artifact"]["runtime_contract"] is None

    row = await _version_row(agent_id, "1.0.0")
    assert row.published_at is None

    # 失敗的版本不得可呼叫
    call = api.post(f"/v1/agents/{agent_id}/runs", json={"input": {"question": "x"}})
    assert call.status_code == 409
    assert call.json()["error"]["code"] == "AGENT_NOT_PUBLISHED"


def test_platform_overrides_image_declared_entrypoint_port_volume_and_user(
    published_agent: dict
) -> None:
    """fixture image 刻意宣告了 `ENTRYPOINT /bin/false`、`CMD not-a-zentra-mode`、
    `USER 0:0`、`EXPOSE 9999`、`VOLUME /app/declared-volume`、失敗的 HEALTHCHECK。

    它能成功發布並執行，本身就證明平台覆寫了這些宣告：照 image 的 ENTRYPOINT 跑
    只會拿到 exit 1。這裡再把 image config 印出來釘住前提，避免 fixture 哪天被改掉
    讓這條測試變成空砲。
    """
    config = docker_json(["image", "inspect", published_agent["image_ref"]])[0]["Config"]
    assert config["Entrypoint"] == ["/bin/false"]
    assert config["Cmd"] == ["not-a-zentra-mode"]
    assert config["User"] == "0:0"
    assert "9999/tcp" in (config.get("ExposedPorts") or {})
    assert "/app/declared-volume" in (config.get("Volumes") or {})
    assert published_agent["version"]["status"] == "PUBLISHED"


# --------------------------------------------------------------------- Z8-13


def test_validation_containers_do_not_survive_success_or_failure(
    api: httpx.Client, image_refs: dict
) -> None:
    """成功與三種失敗路徑跑完之後，都不能留下 validation 容器。"""
    leftovers = containers_with_label("zentra.role=validation")
    assert not leftovers, f"開跑前就有殘留的 validation 容器：{leftovers}"

    agent_id = new_agent(api, "zen008-cleanup")
    cases = [
        ("1.0.0", image_refs["valid_v1"]),
        ("1.0.1", image_refs["no_label"]),
        ("1.0.2", image_refs["compiled_graph"]),
        ("1.0.3", "registry.example.com/nope/nope@sha256:" + "ab" * 32),
    ]
    for semver, ref in cases:
        assert register_version(api, agent_id, semver, ref).status_code == 202
    for semver, _ in cases:
        wait_version(api, agent_id, semver)

    remaining = wait_gone("zentra.role=validation", timeout=60)
    assert not remaining, f"validation 容器沒有被銷毀：{remaining}"


async def test_validation_token_is_revoked_after_the_build_finishes(
    published_agent: dict
) -> None:
    """Z8-12 的可驗證部分：build 結束後該版本的 validation token 必須不存在。

    （token 本身只活在 worker 行程與容器 env 裡，QA 拿不到明文，
    所以這裡驗的是 Redis 索引已清空 —— 那是撤銷的落點。）"""
    from redis.asyncio import Redis

    from apps.api.services import validationtoken
    from tests.zen008.conftest import redis_url

    redis = Redis.from_url(redis_url(), decode_responses=True)
    try:
        version_id = published_agent["version"]["agent_version_id"]
        assert await redis.smembers(validationtoken.version_key(version_id)) == set()
        leftover = [key async for key in redis.scan_iter("validation_token:*")]
        assert not leftover, f"Redis 仍有未撤銷的 validation token：{len(leftover)} 個"
    finally:
        await redis.aclose()


# --------------------------------------------------------------------- Z8-14


def test_registry_outage_fails_the_build_without_falling_back_to_another_image(
    api: httpx.Client, image_refs: dict
) -> None:
    """Registry 不可用 + 本機沒有該 digest → 必須 FAILED(PULL)。

    擋住的錯：cache miss 時退而求其次用同 repository 的其他 image、或用任一本地
    image 硬跑。那會讓「發布的是哪一份程式碼」變成不確定的。
    """
    ref = image_refs["cache_probe"]
    _forget_local(ref)
    assert not _image_present(ref)

    subprocess.run(["docker", "stop", images.REGISTRY_CONTAINER], check=True,
                   capture_output=True, text=True)
    try:
        agent_id = new_agent(api, "zen008-outage")
        assert register_version(api, agent_id, "1.0.0", ref).status_code == 202
        version = wait_version(api, agent_id, "1.0.0")
        assert version["status"] == "FAILED", version
        assert version["error"]["code"] == "BUILD_FAILED"
        assert version["error"]["details"]["stage"] == "PULL", version["error"]
        assert not _image_present(ref), "Registry 掛著卻生出了本地 image"
    finally:
        subprocess.run(["docker", "start", images.REGISTRY_CONTAINER],
                       check=True, capture_output=True, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            probe = subprocess.run(["docker", "run", "--rm", "--network", "host",
                                    "curlimages/curl:latest", "-sf",
                                    f"http://{images.REGISTRY}/v2/"],
                                   capture_output=True, text=True)
            if probe.returncode == 0:
                break
            time.sleep(1)

    # Registry 回來以後，同一個 digest 必須能用同一條路發布成功
    agent_id = new_agent(api, "zen008-recovered")
    assert register_version(api, agent_id, "1.0.0", ref).status_code == 202
    assert wait_version(api, agent_id, "1.0.0")["status"] == "PUBLISHED"


def test_registry_failure_message_carries_no_credential_material(
    api: httpx.Client
) -> None:
    """Z8-19 的可驗證部分：pull 失敗訊息不得反射憑證。"""
    agent_id = new_agent(api, "zen008-redact")
    ref = "registry.example.com/private/agent@sha256:" + "cd" * 32
    assert register_version(api, agent_id, "1.0.0", ref).status_code == 202
    version = wait_version(api, agent_id, "1.0.0")
    assert version["status"] == "FAILED"
    message = json.dumps(version["error"]).lower()
    for word in ("basic ", "bearer ", "password", "identitytoken",
                 "registrytoken", "auths", "/run/secrets"):
        assert word not in message, f"build 錯誤訊息洩漏了 {word!r}：{version['error']}"
