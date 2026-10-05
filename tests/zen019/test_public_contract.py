"""ZEN-019：13 條公開端點逐欄位對 api-contract v0.12.1 §2.6–§2.10。

對 in-process 的 FastAPI app 打**真 HTTP**（ASGI transport）——
路由、pydantic 驗證、序列化、錯誤處理全部走完整條路徑，不是函式直呼。
`test_live_server_serves_the_same_endpoints` 另外對跑著的 `app` 服務確認一次。

每一條擋的是一個具體的錯：

  C-1  `config` 被省略或變成 null   → 前端要分「沒有這個 key」與「空的」兩種情況
  C-2  PUT 變成合併                → 使用者刪不掉一個鍵，而且他以為刪掉了
  C-3  機密讀得回來                → 一個被攻陷的 API 等於全部密碼
  C-4  刪掉還被引用的機密           → 那條能力在下一次 Run 才無聲失敗
  C-5  白名單有逃生門               → 預設拒絕失效
  C-6  寄件者表裡有密碼欄位         → 第二種加密方式、第二種洩漏方式
  C-7  刪 Agent 留下機密            → 被刪掉的 Agent 還握著能用的密碼
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from tests.zen019.conftest import BASE_URL, new_id, purge_agent, requires_db

pytestmark = requires_db


@pytest.fixture
async def agent_id(app_client, session):
    response = await app_client.post("/v1/agents", json={
        "name": f"zen019-contract-{os.urandom(5).hex()}", "description": "ZEN-019"})
    assert response.status_code == 201, response.text
    value = response.json()["agent_id"]
    yield value
    import uuid as _uuid

    await purge_agent(session, _uuid.UUID(value))


# ── §2.6 設定（Z19-01 / Z19-02 / Z19-03）──────────────────────────────

async def test_config_is_exactly_the_contract_shape(app_client, agent_id):
    empty = await app_client.get(f"/v1/agents/{agent_id}/config")
    assert empty.status_code == 200
    # C-1：`config` 這個 key 一定在，不是 null、不被省略
    assert empty.json() == {"config": {}, "updated_at": None}

    document = {"symbols": ["BTCUSDT"], "interval": "1h", "nothing": None,
                "deep": {"a": {"b": [1, 2, {"c": True}]}}}
    saved = await app_client.put(f"/v1/agents/{agent_id}/config",
                                 json={"config": document})
    assert saved.status_code == 200, saved.text
    assert saved.json()["config"] == document
    assert saved.json()["updated_at"].endswith("Z")
    assert set(saved.json()) == {"config", "updated_at"}

    # C-2：**整份取代，不是合併**
    replaced = await app_client.put(f"/v1/agents/{agent_id}/config",
                                    json={"config": {"interval": "4h"}})
    assert replaced.json()["config"] == {"interval": "4h"}
    read_back = await app_client.get(f"/v1/agents/{agent_id}/config")
    assert "symbols" not in read_back.json()["config"]


async def test_config_has_no_patch(app_client, agent_id):
    response = await app_client.patch(f"/v1/agents/{agent_id}/config",
                                      json={"config": {}})
    assert response.status_code in (404, 405)


@pytest.mark.parametrize("body", [
    {"symbols": ["BTC"]},                 # 裸物件，沒有 config 包裝
    {"config": None},
    {"config": []},
    {"config": "text"},
    {"config": {"1abc": 1}},
    {"config": {"a-b": 1}},
    {"config": {"": 1}},
    {"config": {"x" * 65: 1}},
])
async def test_invalid_config_documents_are_422(app_client, agent_id, body):
    response = await app_client.put(f"/v1/agents/{agent_id}/config", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    # DB 未變更
    after = await app_client.get(f"/v1/agents/{agent_id}/config")
    assert after.json()["config"] == {}


async def test_config_key_count_and_size_limits(app_client, agent_id):
    ok = await app_client.put(f"/v1/agents/{agent_id}/config",
                              json={"config": {f"k{i}": i for i in range(100)}})
    assert ok.status_code == 200
    too_many = await app_client.put(f"/v1/agents/{agent_id}/config",
                                    json={"config": {f"k{i}": i for i in range(101)}})
    assert too_many.status_code == 422
    too_big = await app_client.put(
        f"/v1/agents/{agent_id}/config", json={"config": {"blob": "x" * (64 * 1024)}})
    assert too_big.status_code == 422


# ── §2.7 機密（Z19-12 / Z19-13 / Z19-14 / Z19-16 / Z19-18）────────────

async def test_secret_crud_contract(app_client, agent_id):
    created = await app_client.put(
        f"/v1/agents/{agent_id}/secrets/SMTP_PASSWORD",
        json={"value": "abcd efgh ijkl mnop"})
    # U9：新建與覆寫都是 200
    assert created.status_code == 200, created.text
    body = created.json()
    assert set(body) == {"name", "last_four", "created_at", "updated_at"}
    assert body["last_four"] == "mnop"
    assert body["created_at"] == body["updated_at"]

    listing = await app_client.get(f"/v1/agents/{agent_id}/secrets")
    assert listing.status_code == 200
    assert set(listing.json()) == {"items"}
    assert set(listing.json()["items"][0]) == {
        "name", "last_four", "created_at", "updated_at"}

    # C-3：明文與密文都讀不回來
    assert "abcd efgh ijkl mnop" not in listing.text
    assert "ciphertext" not in listing.text

    overwritten = await app_client.put(
        f"/v1/agents/{agent_id}/secrets/SMTP_PASSWORD", json={"value": "new-value-9999"})
    assert overwritten.status_code == 200
    assert overwritten.json()["created_at"] == body["created_at"], "created_at 保留"
    assert overwritten.json()["updated_at"] != body["updated_at"]
    assert overwritten.json()["last_four"] == "9999"

    assert (await app_client.delete(
        f"/v1/agents/{agent_id}/secrets/SMTP_PASSWORD")).status_code == 204
    # 冪等：再刪一次、刪不存在的都是 204
    assert (await app_client.delete(
        f"/v1/agents/{agent_id}/secrets/SMTP_PASSWORD")).status_code == 204
    assert (await app_client.delete(
        f"/v1/agents/{agent_id}/secrets/NEVER_EXISTED")).status_code == 204


async def test_short_secret_values_get_an_equal_length_mask(app_client, agent_id):
    response = await app_client.put(f"/v1/agents/{agent_id}/secrets/SHORT",
                                    json={"value": "ab"})
    assert response.status_code == 200
    assert response.json()["last_four"] == "**"
    assert response.json()["last_four"] != "ab"


@pytest.mark.parametrize("name,value,status", [
    ("1abc", "value", 422),
    ("a-b", "value", 422),
    ("x" * 65, "value", 422),
    ("OK_NAME", "", 422),
    ("OK_NAME", "x" * 4097, 422),
    ("OK_NAME", "x" * 4096, 200),
])
async def test_secret_bounds(app_client, agent_id, name, value, status):
    response = await app_client.put(f"/v1/agents/{agent_id}/secrets/{name}",
                                    json={"value": value})
    assert response.status_code == status, response.text


async def test_the_51st_secret_is_rejected(app_client, agent_id):
    for index in range(50):
        response = await app_client.put(f"/v1/agents/{agent_id}/secrets/S{index}",
                                        json={"value": "v"})
        assert response.status_code == 200, (index, response.text)
    overflow = await app_client.put(f"/v1/agents/{agent_id}/secrets/S50",
                                    json={"value": "v"})
    assert overflow.status_code == 422


async def test_deleting_a_referenced_secret_is_409_and_names_the_users(
    app_client, agent_id,
):
    """C-4：**要說得出被誰引用**，不是只說刪不掉。"""
    await app_client.put(f"/v1/agents/{agent_id}/secrets/SHARED",
                         json={"value": "v"})
    rule = await app_client.post(f"/v1/agents/{agent_id}/egress-rules", json={
        "host": "api.binance.com", "auth_secret_name": "SHARED",
        "auth_header": "X-MBX-APIKEY"})
    assert rule.status_code == 201, rule.text
    sender = await app_client.put(f"/v1/agents/{agent_id}/email-sender", json={
        "smtp_host": "smtp.gmail.com", "smtp_port": 587, "username": "me@x.com",
        "from_address": "me@x.com", "password_secret_name": "SHARED",
        "allowed_recipients": ["me@x.com"]})
    assert sender.status_code == 200, sender.text

    conflict = await app_client.delete(f"/v1/agents/{agent_id}/secrets/SHARED")
    assert conflict.status_code == 409, conflict.text
    error = conflict.json()["error"]
    assert error["code"] == "VERSION_CONFLICT"
    users = error["details"]["referenced_by"]
    assert len(users) == 2
    kinds = {user["kind"] for user in users}
    assert kinds == {"egress_rule", "email_sender"}
    assert any(user.get("rule_id") == rule.json()["rule_id"] for user in users)

    # 機密仍在
    listing = await app_client.get(f"/v1/agents/{agent_id}/secrets")
    assert [row["name"] for row in listing.json()["items"]] == ["SHARED"]

    # 解除兩個引用之後就刪得掉
    await app_client.delete(
        f"/v1/agents/{agent_id}/egress-rules/{rule.json()['rule_id']}")
    await app_client.delete(f"/v1/agents/{agent_id}/email-sender")
    assert (await app_client.delete(
        f"/v1/agents/{agent_id}/secrets/SHARED")).status_code == 204


async def test_no_public_response_schema_exposes_a_secret_value(app_client):
    """Z19-18：掃 OpenAPI，不得有 `value` / `ciphertext` / `plaintext` 回應欄位。"""
    spec = (await app_client.get("/openapi.json")).json()
    schemas = spec.get("components", {}).get("schemas", {})
    for name, schema in schemas.items():
        if not name.endswith("Response") and "Response" not in name:
            continue
        properties = set(schema.get("properties") or {})
        assert not properties & {"value", "ciphertext", "plaintext", "secret"}, name
    # `/internal/*` 完全不在公開契約裡
    assert not [path for path in spec["paths"] if path.startswith("/internal")]


def test_the_api_router_does_not_import_the_decryption_helper():
    """Z19-18：解密只發生在 Gateway 的 egress / email 兩條路徑。"""
    import pathlib

    router = pathlib.Path(__file__).resolve().parents[2] / "apps/api/routers/agents.py"
    source = router.read_text()
    assert "decrypt" not in source
    assert "resolve(" not in source.replace("resolve_for_work", "")


# ── §2.8 出口白名單（Z19-19 ~ Z19-23）─────────────────────────────────

async def test_egress_rule_crud_contract(app_client, agent_id):
    created = await app_client.post(f"/v1/agents/{agent_id}/egress-rules", json={
        "host": "api.binance.com", "methods": ["GET"], "path_prefix": "/api/v3/",
        "description": "Binance 現貨行情"})
    assert created.status_code == 201, created.text
    assert set(created.json()) == {
        "rule_id", "agent_id", "host", "methods", "path_prefix",
        "auth_secret_name", "auth_header", "description", "created_at", "updated_at"}
    rule_id = created.json()["rule_id"]
    assert created.json()["auth_secret_name"] is None

    listing = await app_client.get(f"/v1/agents/{agent_id}/egress-rules")
    assert set(listing.json()) == {"items"}
    assert listing.json()["items"][0]["host"] == "api.binance.com"

    # PUT 是**整條取代**：沒給的選填欄位回到預設，不是保留舊值
    replaced = await app_client.put(
        f"/v1/agents/{agent_id}/egress-rules/{rule_id}",
        json={"host": "api.binance.com"})
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["methods"] == ["GET"]
    assert replaced.json()["path_prefix"] is None
    assert replaced.json()["description"] is None

    assert (await app_client.delete(
        f"/v1/agents/{agent_id}/egress-rules/{rule_id}")).status_code == 204
    assert (await app_client.get(
        f"/v1/agents/{agent_id}/egress-rules")).json()["items"] == []


@pytest.mark.parametrize("host", [
    "*.binance.com", "*", "https://api.binance.com", "api.binance.com:443",
    "api.binance.com/api", "API.Binance.com", "1.1.1.1", "2606:4700::1111",
    "", "a" * 254,
])
async def test_invalid_hosts_are_422(app_client, agent_id, host):
    response = await app_client.post(f"/v1/agents/{agent_id}/egress-rules",
                                     json={"host": host})
    assert response.status_code == 422, (host, response.text)


@pytest.mark.parametrize("methods,status", [
    (None, 201), ([], 422), (["PUT"], 422), (["DELETE"], 422), (["PATCH"], 422),
    (["get"], 422), (["GET", "POST"], 201),
])
async def test_method_whitelist(app_client, agent_id, methods, status):
    body = {"host": f"h{os.urandom(3).hex()}.example.com"}
    if methods is not None:
        body["methods"] = methods
    response = await app_client.post(f"/v1/agents/{agent_id}/egress-rules", json=body)
    assert response.status_code == status, response.text
    if status == 201 and methods is None:
        assert response.json()["methods"] == ["GET"]


@pytest.mark.parametrize("extra", [
    {"auth_secret_name": "MISSING_PAIR"},
    {"auth_header": "X-Api-Key"},
    {"auth_secret_name": "NOT_STORED", "auth_header": "X-Api-Key"},
    {"auth_secret_name": "STORED", "auth_header": "Accept"},
    {"auth_secret_name": "STORED", "auth_header": "content-type"},
    {"auth_secret_name": "STORED", "auth_header": "Host"},
    {"auth_secret_name": "STORED", "auth_header": "Content-Length"},
])
async def test_auth_pairing_rules(app_client, agent_id, extra):
    await app_client.put(f"/v1/agents/{agent_id}/secrets/STORED", json={"value": "v"})
    response = await app_client.post(
        f"/v1/agents/{agent_id}/egress-rules",
        json={"host": f"h{os.urandom(3).hex()}.example.com", **extra})
    assert response.status_code == 422, response.text


async def test_a_valid_auth_pair_is_accepted(app_client, agent_id):
    """反向斷言。"""
    await app_client.put(f"/v1/agents/{agent_id}/secrets/STORED", json={"value": "v"})
    response = await app_client.post(f"/v1/agents/{agent_id}/egress-rules", json={
        "host": "api.binance.com", "auth_secret_name": "STORED",
        "auth_header": "X-MBX-APIKEY"})
    assert response.status_code == 201, response.text


async def test_duplicate_host_is_409_and_the_21st_rule_is_422(app_client, agent_id):
    first = await app_client.post(f"/v1/agents/{agent_id}/egress-rules",
                                  json={"host": "api.binance.com"})
    assert first.status_code == 201
    clash = await app_client.post(f"/v1/agents/{agent_id}/egress-rules",
                                  json={"host": "api.binance.com"})
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "VERSION_CONFLICT"

    for index in range(1, 20):
        response = await app_client.post(
            f"/v1/agents/{agent_id}/egress-rules",
            json={"host": f"h{index}.example.com"})
        assert response.status_code == 201, (index, response.text)
    overflow = await app_client.post(f"/v1/agents/{agent_id}/egress-rules",
                                     json={"host": "h20.example.com"})
    assert overflow.status_code == 422


# ── §2.9 寄件者（Z19-37 ~ Z19-39）─────────────────────────────────────

SENDER = {
    "smtp_host": "smtp.gmail.com", "smtp_port": 587, "username": "me@x.com",
    "from_address": "me@x.com", "password_secret_name": "SMTP_PASSWORD",
    "allowed_recipients": ["me@x.com"],
}


async def test_email_sender_contract(app_client, agent_id):
    unset = await app_client.get(f"/v1/agents/{agent_id}/email-sender")
    assert unset.status_code == 200
    body = unset.json()
    assert body["configured"] is False
    # key 都在，值是 null
    for field in ("smtp_host", "smtp_port", "username", "from_address",
                  "password_secret_name", "allowed_recipients", "updated_at"):
        assert field in body and body[field] is None

    await app_client.put(f"/v1/agents/{agent_id}/secrets/SMTP_PASSWORD",
                         json={"value": "pw"})
    saved = await app_client.put(f"/v1/agents/{agent_id}/email-sender", json=SENDER)
    assert saved.status_code == 200, saved.text
    assert saved.json()["configured"] is True
    assert saved.json()["allowed_recipients"] == ["me@x.com"]
    assert "password" not in str(set(saved.json()) - {"password_secret_name"})

    assert (await app_client.delete(
        f"/v1/agents/{agent_id}/email-sender")).status_code == 204
    assert (await app_client.get(
        f"/v1/agents/{agent_id}/email-sender")).json()["configured"] is False


@pytest.mark.parametrize("field", ["password", "smtp_password", "secret"])
async def test_the_sender_table_has_no_password_column(app_client, agent_id, field):
    """C-6：訊息要指出該去 `/secrets` 存，不是只說「多了欄位」。"""
    await app_client.put(f"/v1/agents/{agent_id}/secrets/SMTP_PASSWORD",
                         json={"value": "pw"})
    response = await app_client.put(f"/v1/agents/{agent_id}/email-sender",
                                    json={**SENDER, field: "hunter2"})
    assert response.status_code == 422, response.text
    assert "secrets" in response.text


@pytest.mark.parametrize("patch,status", [
    ({"smtp_port": 2525}, 422),
    ({"smtp_port": 0}, 422),
    ({"smtp_port": None}, 422),
    ({"smtp_port": 587}, 200),
    ({"smtp_port": 465}, 200),
    ({"smtp_port": 25}, 200),
    ({"allowed_recipients": []}, 422),
    ({"allowed_recipients": ["*@example.com"]}, 422),
    ({"allowed_recipients": ["example.com"]}, 422),
    ({"allowed_recipients": [f"a{i}@x.com" for i in range(11)]}, 422),
    ({"allowed_recipients": [f"a{i}@x.com" for i in range(10)]}, 200),
    ({"password_secret_name": "NOT_STORED"}, 422),
])
async def test_email_sender_bounds(app_client, agent_id, patch, status):
    await app_client.put(f"/v1/agents/{agent_id}/secrets/SMTP_PASSWORD",
                         json={"value": "pw"})
    response = await app_client.put(f"/v1/agents/{agent_id}/email-sender",
                                    json={**SENDER, **patch})
    assert response.status_code == status, (patch, response.text)


# ── §2.10 稽核查詢（Z19-48）───────────────────────────────────────────

async def test_egress_calls_filters_and_cursor(app_client, session, agent_id):
    """造五列再翻頁。`errors_only` 要同時涵蓋 `error IS NOT NULL` 與
    `status_code >= 400`（§2.10 第 3 點）。"""
    import uuid as _uuid
    from datetime import UTC, datetime, timedelta

    from apps.api.models import AgentVersion, EgressCall, Run

    agent_uuid = _uuid.UUID(agent_id)
    version = AgentVersion(
        id=new_id(), agent_id=agent_uuid, semver="1.0.0",
        image_ref="localhost:6000/x@sha256:" + "a" * 64,
        image_digest="sha256:" + "a" * 64, version_id="a" * 12,
        status="PUBLISHED", published_at=datetime.now(UTC))
    session.add(version)
    await session.commit()
    run = Run(id=new_id(), agent_id=agent_uuid, agent_version_id=version.id,
              status="SUCCEEDED", trigger="API", entrypoint="default", input={})
    session.add(run)
    await session.commit()

    base = datetime.now(UTC) - timedelta(minutes=10)
    rows = [
        dict(kind="HTTP", host="api.binance.com", path="/api/v3/klines",
             method="GET", status_code=200, error=None, latency_ms=10),
        dict(kind="HTTP", host="api.binance.com", path="/api/v3/depth",
             method="GET", status_code=404, error=None, latency_ms=11),
        dict(kind="HTTP", host="api.okx.com", path="/api/v5/x",
             method="GET", status_code=None, error="EGRESS_BLOCKED: HOST_NOT_ALLOWED",
             latency_ms=None),
        dict(kind="EMAIL", host="smtp.gmail.com", path="me@x.com",
             method=None, status_code=None, error=None, latency_ms=900),
        dict(kind="EMAIL", host="smtp.gmail.com", path="bad@x.com",
             method=None, status_code=None,
             error="EGRESS_BLOCKED: RECIPIENT_NOT_ALLOWED", latency_ms=None),
    ]
    for index, row in enumerate(rows):
        session.add(EgressCall(call_id=new_id(), run_id=run.id, agent_id=agent_uuid,
                               request_bytes=1, response_bytes=2,
                               created_at=base + timedelta(seconds=index), **row))
    await session.commit()

    everything = await app_client.get(f"/v1/agents/{agent_id}/egress-calls")
    assert everything.status_code == 200
    assert len(everything.json()["items"]) == 5
    assert everything.json()["next_cursor"] is None
    first = everything.json()["items"][0]
    assert set(first) == {
        "call_id", "run_id", "kind", "rule_id", "method", "host", "path",
        "status_code", "request_bytes", "response_bytes", "latency_ms",
        "error", "created_at"}

    async def count(query):
        response = await app_client.get(f"/v1/agents/{agent_id}/egress-calls?{query}")
        assert response.status_code == 200, response.text
        return len(response.json()["items"])

    assert await count("kind=HTTP") == 3
    assert await count("kind=EMAIL") == 2
    assert await count(f"run_id={run.id}") == 5
    assert await count("host=api.okx.com") == 1
    assert await count("status_code=200") == 1
    # 404 那列 `error` 是 null，但 status_code >= 400 —— 要被收進來
    assert await count("errors_only=true") == 3
    assert await count("kind=HTTP&errors_only=true") == 2

    page = await app_client.get(f"/v1/agents/{agent_id}/egress-calls?limit=2")
    assert len(page.json()["items"]) == 2
    seen = [item["call_id"] for item in page.json()["items"]]
    cursor = page.json()["next_cursor"]
    assert cursor
    while cursor:
        following = await app_client.get(
            f"/v1/agents/{agent_id}/egress-calls?limit=2&cursor={cursor}")
        seen.extend(item["call_id"] for item in following.json()["items"])
        cursor = following.json()["next_cursor"]
    assert len(seen) == 5 and len(set(seen)) == 5, "不重不漏"

    assert (await app_client.get(
        f"/v1/agents/{agent_id}/egress-calls?kind=SMTP")).status_code == 422
    assert (await app_client.get(
        f"/v1/agents/{agent_id}/egress-calls?run_id=not-a-uuid")).status_code == 422


# ── §2.5 刪 Agent 要帶走五張表（Z19-50）──────────────────────────────

async def test_deleting_the_agent_removes_all_five_tables(app_client, session):
    """C-7：被刪掉的 Agent 沒理由還握著能用的密碼。"""
    import uuid as _uuid

    created = await app_client.post("/v1/agents", json={
        "name": f"zen019-delete-{os.urandom(5).hex()}"})
    agent_id = created.json()["agent_id"]
    agent_uuid = _uuid.UUID(agent_id)

    await app_client.put(f"/v1/agents/{agent_id}/config",
                         json={"config": {"a": 1}})
    await app_client.put(f"/v1/agents/{agent_id}/secrets/S", json={"value": "v"})
    rule = await app_client.post(f"/v1/agents/{agent_id}/egress-rules",
                                 json={"host": "api.binance.com"})
    await app_client.put(f"/v1/agents/{agent_id}/email-sender", json={
        **SENDER, "password_secret_name": "S"})

    from datetime import UTC, datetime

    from apps.api.models import AgentVersion, EgressCall, Run

    version = AgentVersion(
        id=new_id(), agent_id=agent_uuid, semver="1.0.0",
        image_ref="localhost:6000/x@sha256:" + "b" * 64,
        image_digest="sha256:" + "b" * 64, version_id="b" * 12,
        status="PUBLISHED", published_at=datetime.now(UTC))
    session.add(version)
    await session.commit()
    run = Run(id=new_id(), agent_id=agent_uuid, agent_version_id=version.id,
              status="SUCCEEDED", trigger="API", entrypoint="default", input={})
    session.add(run)
    await session.commit()
    session.add(EgressCall(
        call_id=new_id(), run_id=run.id, agent_id=agent_uuid, kind="HTTP",
        rule_id=_uuid.UUID(rule.json()["rule_id"]), method="GET",
        host="api.binance.com", path="/api/v3/x", status_code=200,
        request_bytes=0, response_bytes=1, latency_ms=5,
        created_at=datetime.now(UTC)))
    await session.commit()

    # ON DELETE SET NULL：刪規則**不抹掉**稽核紀錄
    await app_client.delete(
        f"/v1/agents/{agent_id}/egress-rules/{rule.json()['rule_id']}")
    remaining = (await app_client.get(
        f"/v1/agents/{agent_id}/egress-calls")).json()["items"]
    assert len(remaining) == 1 and remaining[0]["rule_id"] is None

    assert (await app_client.delete(f"/v1/agents/{agent_id}")).status_code == 204
    for table in ("agent_settings", "agent_secrets", "agent_egress_rules",
                  "agent_email_sender", "egress_calls"):
        count = (await session.execute(
            text(f"SELECT count(*) FROM {table} WHERE agent_id = :id"),
            {"id": agent_uuid})).scalar_one()
        assert count == 0, table


# ── 對跑著的服務確認一次（契約層：真 HTTP，不是 ASGI）────────────────

@pytest.mark.skipif(not BASE_URL, reason="需要 ZENTRA_TEST_BASE_URL")
async def test_live_server_serves_the_same_endpoints():
    """in-process 跟真的跑著的那個是同一份程式碼，但部署過的那個才是使用者碰到的。"""
    import httpx

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        created = await client.post("/v1/agents", json={
            "name": f"zen019-live-{os.urandom(5).hex()}"})
        assert created.status_code == 201, created.text
        agent_id = created.json()["agent_id"]
        try:
            assert (await client.get(f"/v1/agents/{agent_id}/config")).json() == {
                "config": {}, "updated_at": None}
            assert (await client.put(f"/v1/agents/{agent_id}/config",
                                     json={"config": {"k": 1}})).status_code == 200
            assert (await client.put(f"/v1/agents/{agent_id}/secrets/K",
                                     json={"value": "value-1234"})
                    ).json()["last_four"] == "1234"
            assert (await client.post(f"/v1/agents/{agent_id}/egress-rules",
                                      json={"host": "api.binance.com"})
                    ).status_code == 201
            calls = await client.get(f"/v1/agents/{agent_id}/egress-calls")
            assert calls.status_code == 200 and calls.json()["items"] == []
        finally:
            await client.delete(f"/v1/agents/{agent_id}")
