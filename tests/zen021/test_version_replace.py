"""ADR-0016：同一個版號重新註冊 = 取代那個版本。

worker 那一側（驗證通過時把舊的改成 SUPERSEDED、收舊 image）要真的 pull image 才驗得到，
這裡只驗 API 與 DB 的部分。版本列直接寫進 DB，不靠 build 的快慢 ——
POST 之後 worker 會去 pull 一個不存在的 image 然後 FAILED，那不影響這裡斷言的東西。
"""

from __future__ import annotations

import asyncpg
import pytest

from apps.api import rows
from tests.zen019.conftest import insert_agent, make_published_version, new_id, purge_agent, requires_db

pytestmark = requires_db

SEMVER = "1.0.0"
OLD_REF = f"localhost:6000/zen019@sha256:{'a' * 64}"   # make_published_version 用的
NEW_REF = f"localhost:6000/zen019@sha256:{'b' * 64}"


async def _insert(agent_id, status: str, image_ref: str = NEW_REF) -> rows.AgentVersion:
    from apps.api import db

    digest = image_ref.split("@", 1)[1]
    async with db.transaction() as conn:
        rec = await conn.fetchrow(
            f"""INSERT INTO agent_versions (
                    id, agent_id, semver, source_type, image_ref, image_digest, version_id, status
                ) VALUES ($1, $2, $3, 'OCI_IMAGE', $4, $5, $6, $7)
                RETURNING {rows.AgentVersion.returning()}""",
            new_id(), agent_id, SEMVER, image_ref, digest,
            digest.removeprefix("sha256:")[:12], status,
        )
    return rows.AgentVersion.from_row(rec)


async def _statuses(agent_id) -> dict[str, str]:
    from apps.api import db

    async with db.connection() as conn:
        found = await conn.fetch(
            "SELECT id, status FROM agent_versions WHERE agent_id = $1", agent_id)
    return {str(r["id"]): r["status"] for r in found}


def _register(client, agent_id, image_ref: str):
    return client.post(f"/v1/agents/{agent_id}/versions",
                       json={"semver": SEMVER, "image_ref": image_ref})


async def test_same_semver_with_a_new_image_starts_a_replacement(app_client) -> None:
    agent = await insert_agent()
    try:
        live = await make_published_version(agent.id, SEMVER)
        response = await _register(app_client, agent.id, NEW_REF)
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["agent_version_id"] != str(live.id)
        assert body["artifact"]["image_ref"] == NEW_REF
        # 新的驗證通過之前，舊的照常服務 —— 不能在這一步就讓位
        assert (await _statuses(agent.id))[str(live.id)] == "PUBLISHED"
    finally:
        await purge_agent(agent.id)


async def test_a_second_replacement_while_one_is_building_is_409(app_client) -> None:
    agent = await insert_agent()
    try:
        await make_published_version(agent.id, SEMVER)
        await _insert(agent.id, "BUILDING")
        response = await _register(app_client, agent.id, f"localhost:6000/zen019@sha256:{'c' * 64}")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    finally:
        await purge_agent(agent.id)


async def test_registering_the_live_image_again_is_a_noop(app_client) -> None:
    agent = await insert_agent()
    try:
        live = await make_published_version(agent.id, SEMVER)
        response = await _register(app_client, agent.id, OLD_REF)
        assert response.status_code == 202, response.text
        assert response.json()["agent_version_id"] == str(live.id)
        assert response.json()["status"] == "PUBLISHED"
        assert await _statuses(agent.id) == {str(live.id): "PUBLISHED"}
    finally:
        await purge_agent(agent.id)


async def test_a_failed_semver_can_be_registered_again(app_client) -> None:
    """以前 BUILD_FAILED 的版號永遠不能再用，只能換號。"""
    agent = await insert_agent()
    try:
        await _insert(agent.id, "FAILED")
        response = await _register(app_client, agent.id, f"localhost:6000/zen019@sha256:{'d' * 64}")
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "BUILDING"
    finally:
        await purge_agent(agent.id)


async def test_superseded_rows_are_hidden_and_get_returns_the_latest_attempt(app_client) -> None:
    agent = await insert_agent()
    try:
        old = await _insert(agent.id, "SUPERSEDED", f"localhost:6000/zen019@sha256:{'e' * 64}")
        live = await make_published_version(agent.id, SEMVER)
        failed = await _insert(agent.id, "FAILED")

        listed = await app_client.get(f"/v1/agents/{agent.id}/versions")
        ids = {item["agent_version_id"] for item in listed.json()["items"]}
        assert ids == {str(live.id), str(failed.id)}
        assert "SUPERSEDED" not in {item["status"] for item in listed.json()["items"]}

        latest = await app_client.get(f"/v1/agents/{agent.id}/versions/{SEMVER}")
        assert latest.json()["agent_version_id"] == str(failed.id)

        # 呼叫這個版號跑的是現役那筆，不是最近失敗的那次
        from apps.api import db
        from apps.api.services.agents import resolve_published_version

        async with db.connection() as conn:
            resolved = await resolve_published_version(conn, agent.id, SEMVER)
        assert resolved.id == live.id
        assert resolved.id != old.id
    finally:
        await purge_agent(agent.id)


async def test_the_database_allows_only_one_live_row_per_semver() -> None:
    agent = await insert_agent()
    try:
        await make_published_version(agent.id, SEMVER)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert(agent.id, "PUBLISHED")
        # 被取代的紀錄可以有很多筆
        await _insert(agent.id, "SUPERSEDED", f"localhost:6000/zen019@sha256:{'e' * 64}")
        await _insert(agent.id, "SUPERSEDED", f"localhost:6000/zen019@sha256:{'f' * 64}")
    finally:
        await purge_agent(agent.id)
