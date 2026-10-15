"""Z8-01 / Z8-02 / Z8-03 / Z8-05：Agent metadata 與 OCI 版本註冊的公開契約。

對 **live API** 跑（`api-contract.md v0.3.1` §2）。每一條都指名它擋住哪一種錯：
欄位漏掉、digest 被放寬成 tag、重複 semver 覆寫既有版本、容器控制欄位被偷渡進請求。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from apps.api import rows
from tests.zen008.conftest import new_agent, pg_connect, register_version, wait_version

DIGEST = "sha256:" + "ab" * 32
REPO = "registry.example.com/acme/agent"
CANONICAL = f"{REPO}@{DIGEST}"


# --------------------------------------------------------------- Z8-01 metadata


def test_create_agent_returns_contract_shape(api: httpx.Client) -> None:
    name = f"zen008-shape-{uuid.uuid4().hex[:10]}"
    response = api.post("/v1/agents", json={"name": name, "description": "d"})
    assert response.status_code == 201, response.text
    body = response.json()
    # 欄位集合鎖死：多一個欄位代表後端自創格式，少一個代表前端會拿到 undefined
    assert set(body) == {"agent_id", "name", "description",
                         "latest_published_version", "created_at"}
    assert uuid.UUID(body["agent_id"]).version == 7
    assert body["latest_published_version"] is None
    assert body["created_at"].endswith("Z")


def test_agent_detail_exposes_three_endpoints(api: httpx.Client) -> None:
    agent_id = new_agent(api)
    body = api.get(f"/v1/agents/{agent_id}").json()
    assert set(body["endpoints"]) == {"rest", "mcp", "openapi"}
    assert body["endpoints"]["rest"].endswith(f"/v1/agents/{agent_id}/runs")
    assert body["endpoints"]["mcp"].endswith(f"/mcp/agents/{agent_id}")
    assert body["endpoints"]["openapi"].endswith(f"/v1/agents/{agent_id}/openapi.json")


def test_list_hides_soft_deleted_agents_and_paginates(api: httpx.Client) -> None:
    keep, drop = new_agent(api, "zen008-keep"), new_agent(api, "zen008-drop")
    assert api.delete(f"/v1/agents/{drop}").status_code == 204

    page = api.get("/v1/agents", params={"limit": 1}).json()
    assert len(page["items"]) == 1
    assert page.get("next_cursor"), "cursor 分頁沒有回 next_cursor，第二頁就拿不到了"

    seen, cursor, pages = set(), page["next_cursor"], 0
    seen.update(item["agent_id"] for item in page["items"])
    while cursor and pages < 200:
        body = api.get("/v1/agents", params={"limit": 25, "cursor": cursor}).json()
        seen.update(item["agent_id"] for item in body["items"])
        cursor, pages = body.get("next_cursor"), pages + 1
    assert keep in seen
    assert drop not in seen, "soft-delete 的 Agent 仍然被列出來了"


def test_agent_list_always_carries_next_cursor_key(api: httpx.Client) -> None:
    """契約 §0：「回應含 `next_cursor`（無下一頁則 `null`）」。

    擋住的錯：最後一頁把 key 整個拿掉，客戶端拿到 `undefined` 而不是 `null`，
    「還有沒有下一頁」變成要靠 key 存在與否去猜。
    """
    body = api.get("/v1/agents", params={"limit": 100}).json()
    cursor, pages = body.get("next_cursor"), 0
    while cursor and pages < 200:          # 走到真正的最後一頁，不要靠資料量碰運氣
        body = api.get("/v1/agents", params={"limit": 100, "cursor": cursor}).json()
        cursor, pages = body.get("next_cursor"), pages + 1
    assert "next_cursor" in body, f"最後一頁少了 next_cursor：keys={sorted(body)}"


def test_agent_summary_keeps_nullable_contract_fields(api: httpx.Client) -> None:
    """契約 §2 的 Agent 物件固定有 `description` 與 `latest_published_version`。

    擋住的錯：還沒發布版本的 Agent 在 detail/list 裡整個少掉
    `latest_published_version`，Console 讀到 `undefined` 而不是 `null`。
    """
    agent_id = new_agent(api, "zen008-null")
    api.patch(f"/v1/agents/{agent_id}", json={"description": None})
    detail = api.get(f"/v1/agents/{agent_id}").json()
    missing = {"description", "latest_published_version"} - set(detail)
    assert not missing, f"GET /v1/agents/{{id}} 少了 {sorted(missing)}：keys={sorted(detail)}"

    listed = next(item for item in api.get("/v1/agents", params={"limit": 100}).json()["items"]
                  if item["agent_id"] == agent_id)
    missing = {"description", "latest_published_version"} - set(listed)
    assert not missing, f"GET /v1/agents 少了 {sorted(missing)}：keys={sorted(listed)}"


def test_deleted_agent_detail_is_not_found(api: httpx.Client) -> None:
    agent_id = new_agent(api, "zen008-gone")
    api.delete(f"/v1/agents/{agent_id}")
    response = api.get(f"/v1/agents/{agent_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------- Z8-02 canonical digest

REJECTED_REFS = [
    pytest.param("registry.example.com/acme/agent:1.0.0", id="tag-only"),
    pytest.param(f"registry.example.com/acme/agent:1.0.0@{DIGEST}", id="tag-plus-digest"),
    pytest.param(f"@{DIGEST}", id="missing-repository"),
    pytest.param(f"{REPO}@sha256:{'ab' * 20}", id="short-digest"),
    pytest.param(f"{REPO}@sha256:{'zz' * 32}", id="non-hex-digest"),
    pytest.param(f"{REPO}@sha512:{'ab' * 32}", id="not-sha256"),
    pytest.param(f"{REPO}@{DIGEST.upper()}", id="uppercase-digest"),
    pytest.param("", id="empty"),
    pytest.param(f"{REPO}@{DIGEST}@{DIGEST}", id="two-digests"),
    pytest.param(f"{REPO} @{DIGEST}", id="whitespace"),
]


@pytest.mark.parametrize("image_ref", REJECTED_REFS)
async def test_non_canonical_digest_is_rejected(api: httpx.Client, image_ref: str) -> None:
    """P5 的入口：只要接受 tag，版本身份就不再是不可變的 digest。"""
    agent_id = new_agent(api, "zen008-ref")
    response = register_version(api, agent_id, "1.0.0", image_ref)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    conn = await pg_connect()
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM agent_versions WHERE agent_id = $1",
            uuid.UUID(agent_id))
        assert count == 0, "被拒絕的 image_ref 仍然寫了一列 agent_versions"
    finally:
        await conn.close()


@pytest.mark.parametrize("extra", [
    {"command": ["sh", "-c", "id"]},
    {"entrypoint": ["/bin/sh"]},
    {"ports": {"9999": 9999}},
    {"network": "bridge"},
    {"volumes": {"/etc": "/etc"}},
    {"user": "0:0"},
    {"capabilities": ["SYS_ADMIN"]},
    {"privileged": True},
    {"registry_password": "hunter2"},
])
def test_container_control_fields_are_rejected(api: httpx.Client, extra: dict) -> None:
    """請求端不得取得任何容器控制權（P1/P4）。多餘欄位必須是 422，不是被忽略。"""
    agent_id = new_agent(api, "zen008-extra")
    response = api.post(f"/v1/agents/{agent_id}/versions",
                        json={"semver": "1.0.0", "image_ref": CANONICAL, **extra})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("semver", ["1.0", "v1.0.0", "01.0.0", "", "latest"])
def test_invalid_semver_is_rejected(api: httpx.Client, semver: str) -> None:
    agent_id = new_agent(api, "zen008-semver")
    assert register_version(api, agent_id, semver, CANONICAL).status_code == 422


# ------------------------------------------------------------ Z8-03 建立版本契約


def test_create_version_response_matches_contract(api: httpx.Client) -> None:
    agent_id = new_agent(api, "zen008-create")
    response = register_version(api, agent_id, "1.2.0", CANONICAL)
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"agent_version_id", "semver", "version_id", "source_type",
                         "artifact", "status", "error", "created_at",
                         "manifest", "entrypoints"}
    assert body["semver"] == "1.2.0"
    assert body["version_id"] == DIGEST.removeprefix("sha256:")[:12]
    assert body["source_type"] == "OCI_IMAGE"
    assert body["status"] == "BUILDING"
    assert body["error"] is None
    assert body["manifest"] is None and body["entrypoints"] is None
    assert set(body["artifact"]) == {"image_ref", "digest", "platform",
                                     "size_bytes", "runtime_contract"}
    assert body["artifact"] == {"image_ref": CANONICAL, "digest": DIGEST,
                                "platform": None, "size_bytes": None,
                                "runtime_contract": None}
    assert uuid.UUID(body["agent_version_id"]).version == 7
    assert body["created_at"].endswith("Z")


def test_unresolvable_digest_fails_with_pull_stage(api: httpx.Client) -> None:
    """契約要求 FAILED 帶 BUILD_FAILED 與 PULL/INSPECT/VALIDATE 其中之一的 stage。

    這條同時是 Z8-09 的 PULL 分支：Registry 裡不存在的 digest 不得被當成成功，
    也不得 fallback 到任何本地 image。
    """
    agent_id = new_agent(api, "zen008-pull")
    assert register_version(api, agent_id, "1.0.0", CANONICAL).status_code == 202
    version = wait_version(api, agent_id, "1.0.0")
    assert version["status"] == "FAILED", version
    assert version["error"]["code"] == "BUILD_FAILED"
    assert version["error"]["details"]["stage"] == "PULL"
    assert version["error"]["retryable"] is False
    assert version["manifest"] is None and version["entrypoints"] is None
    assert version["artifact"]["runtime_contract"] is None


# ------------------------------------------------------------- Z8-05 P5 不可變
# 同版號取代（ADR-0016）的確定性測試在 tests/zen021；這裡只留需要真 registry 的那條。


def test_same_digest_can_be_registered_by_another_agent(api: httpx.Client) -> None:
    first, second = new_agent(api, "zen008-share-a"), new_agent(api, "zen008-share-b")
    assert register_version(api, first, "1.0.0", CANONICAL).status_code == 202
    assert register_version(api, second, "9.9.9", CANONICAL).status_code == 202


def test_no_write_endpoint_exists_for_a_published_version(api: httpx.Client) -> None:
    """P5：契約 §2 沒有任何改版本的端點。有人加了就是漂移。"""
    paths = api.get("/openapi.json").json()["paths"]
    version_paths = {p: set(ops) for p, ops in paths.items() if "/versions/" in p}
    for path, methods in version_paths.items():
        assert methods <= {"get"}, f"{path} 出現了非 GET 的版本端點：{methods}"


async def test_published_version_columns_are_stable_after_republish_attempt(
    api: httpx.Client, published_agent: dict
) -> None:
    """成功發布後用別的 digest 再註冊同 semver：建新的一筆（ADR-0016），
    但已發布那一筆的欄位一個字都不能變 —— 取代是新增一筆，不是改寫。"""
    agent_id = published_agent["agent_id"]
    conn = await pg_connect()
    fields = ("semver", "image_ref", "image_digest", "runtime_contract",
              "manifest", "entrypoints", "published_at")
    try:
        rec = await conn.fetchrow(
            f"SELECT {rows.AgentVersion.returning()} FROM agent_versions "
            "WHERE agent_id = $1 AND semver = $2 AND status = 'PUBLISHED'",
            uuid.UUID(agent_id), published_agent["semver"])
        published_id = rec["id"]
        before = {name: rec[name] for name in fields}

        replacement = register_version(api, agent_id, published_agent["semver"],
                                       f"{REPO}@{'sha256:' + 'ef' * 32}")
        assert replacement.status_code == 202, replacement.text
        assert replacement.json()["agent_version_id"] != str(published_id)

        rec = await conn.fetchrow(
            f"SELECT {rows.AgentVersion.returning()} FROM agent_versions WHERE id = $1",
            published_id)
        after = {name: rec[name] for name in fields}
        assert before == after
    finally:
        await conn.close()


# ------------------------------------------------- Z8-04 schema 不藏 image/憑證


async def test_schema_has_no_image_bytes_or_registry_credential_columns() -> None:
    """Z8-04：DB 只存 metadata。出現 blob/layer/password 欄位就是把 Registry 憑證
    或 image 內容搬進了資料層。"""
    conn = await pg_connect()
    banned = ("blob", "layer", "archive", "tarball", "bundle",
              "password", "secret", "token", "auth", "credential")
    try:
        found = await conn.fetch(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name IN ('agents','agent_versions')")
        assert found, "agents/agent_versions 不存在"
        for rec in found:
            table, column, data_type = rec["table_name"], rec["column_name"], rec["data_type"]
            for word in banned:
                assert word not in column.lower(), f"{table}.{column} 看起來在存 {word}"
            assert data_type != "bytea", f"{table}.{column} 是 bytea，疑似存 image bytes"
    finally:
        await conn.close()


async def test_published_row_contains_no_credential_material(published_agent: dict) -> None:
    conn = await pg_connect()
    try:
        rec = await conn.fetchrow(
            f"SELECT {rows.AgentVersion.returning()} FROM agent_versions WHERE agent_id = $1",
            uuid.UUID(published_agent["agent_id"]))
        serialized = str(dict(rec)).lower()
    finally:
        await conn.close()
    for word in ("authorization", "bearer ", "identitytoken", "registrytoken",
                 "password", "docker.sock", "postgresql://", "redis://"):
        assert word not in serialized, f"agent_versions 列裡出現 {word!r}"
