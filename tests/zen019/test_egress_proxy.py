"""ZEN-019：出口 proxy（api-contract §4.2 / ADR-0014 §3）。

真 PostgreSQL + 真 Redis + in-process FastAPI app，**假 resolver 與假 transport**。

為什麼不打外網：proxy 只准 `https` 且解析到私有位址一律擋，而 compose 網段上
任何 stub 都在 `172.16/12` —— **一個正確的實作會把自己的測試 stub 擋掉**。
所以成功路徑驗的是「平台組出來的請求長什麼樣」：method、注入的 header、
`User-Agent`、連到哪個 IP、有沒有跟隨 redirect、在哪裡截斷。
那些全部不需要真的連出去（feature spec §5.3.1）。

每一條擋的是一個具體的錯：

  E-1  沒有規則卻連得出去        → 白名單的價值全部來自「預設拒絕」
  E-2  沙盒自帶認證              → 默默丟掉會讓「我的 key 怎麼沒生效」變成謎
  E-3  跟隨 redirect             → 對方一行 302 就繞過白名單
  E-4  解析後不檢查／檢查完再解析 → SSRF / DNS rebinding，沙盒觸達 postgres
  E-5  先解析再比對白名單        → 沙盒可以用任意網址驅動 app 發 DNS 查詢
  E-6  上游 4xx 被當成平台錯誤   → 「主源失敗換備援」就寫不出來
  E-7  被擋下的沒寫稽核          → 「為什麼連不出去」在畫面上沒有線索
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from tests.zen019.conftest import (
    auth, fetch_egress_calls, insert_agent, insert_egress_rule,
    issue_run_token, make_published_version, make_run, new_id, purge_agent,
    requires_db,
)

pytestmark = requires_db

HOST = "api.example-exchange.com"
#: 一個**真的是公網**的位址。
#: ⚠️ 不要用 `203.0.113.x`／`192.0.2.x`（TEST-NET）：它們 `is_global` 是 False，
#: 會被 `PRIVATE_ADDRESS` 正確地擋下來，然後整組成功路徑的測試都紅 ——
#: 而那是實作對、測試錯。假 transport 讓它不必真的連得到。
PUBLIC_IP = "93.184.216.34"
URL = f"https://{HOST}/api/v3/klines?symbol=BTCUSDT&interval=1h"


class Recorder:
    """假 transport：記下平台**實際**送出去的請求。"""

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def transport(self) -> httpx.MockTransport:
        def wrapped(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._handler(request)
        return httpx.MockTransport(wrapped)


@pytest.fixture
def egress_module():
    from apps.api.services import egress

    return egress


@pytest.fixture
def wire(monkeypatch, egress_module):
    """把 resolver 與 transport 換掉。**兩者都不改變任何檢查。**"""
    state = {"resolved": [], "calls": 0}

    def install(addresses, handler):
        recorder = Recorder(handler)

        async def fake_resolve(host: str, port: int):
            state["calls"] += 1
            state["resolved"].append((host, port))
            return list(addresses)

        monkeypatch.setattr(egress_module, "resolve_addresses", fake_resolve)
        monkeypatch.setattr(egress_module, "build_transport", recorder.transport)
        return recorder

    install.state = state
    return install


def ok_json(body="[[1,2,3]]", headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=body,
            headers=headers or {"content-type": "application/json"})
    return handler


async def setup_agent(redis_client, *, rules=None):
    """建一個 Agent + 已發布版本 + RUNNING 的 Run + run token。"""
    agent = await insert_agent(name=f"zen019-egress-{os.urandom(5).hex()}")
    version = await make_published_version(agent.id)
    run = await make_run(agent.id, version.id)
    token = await issue_run_token(redis_client, run.id)
    for rule in rules or []:
        await insert_egress_rule(agent.id, host=rule.get("host", HOST), **{
            key: rule[key] for key in ("methods", "path_prefix",
                                       "auth_secret_name", "auth_header")
            if key in rule
        })
    return agent, run, token


async def cleanup(agent_id):
    await purge_agent(agent_id)


async def audit_rows(run_id):
    return await fetch_egress_calls(run_id)


# ── E-1：預設拒絕（Z19-24）─────────────────────────────────────────────

async def test_no_rules_blocks_everything_and_writes_an_audit_row(
    app_client, redis_client, wire,
):
    wire([PUBLIC_IP], ok_json())
    agent, run, token = await setup_agent(redis_client)
    try:
        response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        assert response.status_code == 403
        error = response.json()["error"]
        assert error["code"] == "EGRESS_BLOCKED"
        assert error["details"]["reason"] == "NO_RULES"
        assert error["retryable"] is False
        assert error["run_id"] == str(run.id)

        rows = await audit_rows( run.id)
        assert len(rows) == 1
        assert rows[0].error == "EGRESS_BLOCKED: NO_RULES"
        assert rows[0].status_code is None
        # E-5：連 DNS 都還沒發出去
        assert wire.state["calls"] == 0
    finally:
        await cleanup(agent.id)


@pytest.mark.parametrize("bad_url,reason", [
    (f"http://{HOST}/api/v3/x", "SCHEME_NOT_ALLOWED"),
    (f"HTTPS://{HOST}/api/v3/x", "SCHEME_NOT_ALLOWED"),
    (f"ftp://{HOST}/api/v3/x", "SCHEME_NOT_ALLOWED"),
    (f"{HOST}/api/v3/x", "SCHEME_NOT_ALLOWED"),
    ("https://other.example.com/api/v3/x", "HOST_NOT_ALLOWED"),
    (f"https://{HOST}/api/v1/x", "PATH_NOT_ALLOWED"),
])
async def test_policy_rejections(
    app_client, redis_client, wire, bad_url, reason,
):
    wire([PUBLIC_IP], ok_json())
    agent, run, token = await setup_agent(
        redis_client, rules=[{"path_prefix": "/api/v3/"}])
    try:
        response = await app_client.post(
            "/internal/egress/request", json={"url": bad_url}, headers=auth(token))
        assert response.status_code == 403, response.text
        assert response.json()["error"]["details"]["reason"] == reason
        rows = await audit_rows( run.id)
        assert len(rows) == 1 and rows[0].error == f"EGRESS_BLOCKED: {reason}"
    finally:
        await cleanup(agent.id)


async def test_method_not_on_the_rule_is_blocked(
    app_client, redis_client, wire,
):
    wire([PUBLIC_IP], ok_json())
    agent, run, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request",
            json={"url": URL, "method": "POST", "body": "{}"}, headers=auth(token))
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "METHOD_NOT_ALLOWED"
    finally:
        await cleanup(agent.id)


# ── E-5：host 沒命中時**不准發 DNS 查詢**（Z19-30）─────────────────────

async def test_resolver_is_not_called_when_the_host_is_not_allowed(
    app_client, redis_client, wire,
):
    """沙盒可以用任意網址驅動 `app` 發 DNS 查詢 —— 那本身就是一條外洩通道。"""
    wire([PUBLIC_IP], ok_json())
    agent, _, token = await setup_agent(redis_client, rules=[{}])
    try:
        await app_client.post("/internal/egress/request",
                              json={"url": "https://evil.example.net/x"},
                              headers=auth(token))
        assert wire.state["calls"] == 0
    finally:
        await cleanup(agent.id)


# ── E-4：解析後檢查 + 連那個 IP（Z19-28 / Z19-29）──────────────────────

@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "172.17.0.2", "192.168.1.1",
    "169.254.169.254", "::1", "fd00::1", "::ffff:127.0.0.1",
])
async def test_private_resolution_is_blocked_and_nothing_is_sent(
    app_client, redis_client, wire, address,
):
    recorder = None

    def handler(request):                    # pragma: no cover - 不該被呼叫
        raise AssertionError("the proxy must not connect to a private address")

    recorder = wire([address], handler)
    agent, run, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        assert response.status_code == 403
        details = response.json()["error"]["details"]
        assert details["reason"] == "PRIVATE_ADDRESS"
        assert details["resolved"] == address
        assert recorder.requests == []       # **一個位元組都沒送出去**
        rows = await audit_rows( run.id)
        assert rows[0].error == "EGRESS_BLOCKED: PRIVATE_ADDRESS"
    finally:
        await cleanup(agent.id)


async def test_a_private_address_hidden_behind_a_public_one_is_still_blocked(
    app_client, redis_client, wire,
):
    """round-robin DNS 把內網位址藏在第二筆 —— 只看第一筆等於沒檢查。"""
    recorder = wire([PUBLIC_IP, "127.0.0.1"], ok_json())
    agent, _, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "PRIVATE_ADDRESS"
        assert recorder.requests == []
    finally:
        await cleanup(agent.id)


async def test_the_proxy_connects_to_the_address_it_checked(
    app_client, redis_client, wire,
):
    """**DNS rebinding（Z19-29）。**

    解析一次 → 檢查 → **連那個 IP**，並帶 `Host` header 與該名稱的 SNI。
    交給 http client 自己再解析第二次的話，第二次可以回 `127.0.0.1`
    而檢查完全沒發生。
    """
    recorder = wire([PUBLIC_IP], ok_json())
    agent, _, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        assert response.status_code == 200, response.text
        assert wire.state["calls"] == 1, "只准解析一次"
        assert len(recorder.requests) == 1
        sent = recorder.requests[0]
        # 目的地是**檢查過的那個 IP**，不是主機名
        assert sent.url.host == PUBLIC_IP
        # 但 Host header 與 SNI 還是原主機名 —— 憑證因此仍以它驗證
        assert sent.headers["host"] == HOST
        assert sent.extensions.get("sni_hostname") == HOST
        # query string 原樣帶過去
        assert "symbol=BTCUSDT" in str(sent.url)
    finally:
        await cleanup(agent.id)


# ── E-6：信封語意（Z19-32）────────────────────────────────────────────

@pytest.mark.parametrize("status", [200, 418, 429, 500])
async def test_upstream_status_comes_back_in_the_envelope(
    app_client, redis_client, wire, status,
):
    """上游回 418／429／500 **不是平台錯誤** —— 這是備援能實作的前提。"""
    def handler(request):
        return httpx.Response(status, text='{"ok":false}',
                              headers={"content-type": "application/json"})

    wire([PUBLIC_IP], handler)
    agent, run, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        assert response.status_code == 200
        envelope = response.json()
        assert envelope["status"] == status
        assert envelope["body"] == '{"ok":false}'
        rows = await audit_rows( run.id)
        # 送出去了 → `error` 是 None，即使上游回 4xx／5xx（§2.10 第 4 點）
        assert rows[0].error is None
        assert rows[0].status_code == status
        assert rows[0].latency_ms is not None
    finally:
        await cleanup(agent.id)


async def test_response_headers_are_a_whitelisted_subset(
    app_client, redis_client, wire,
):
    def handler(request):
        return httpx.Response(200, text="{}", headers=[
            ("content-type", "application/json"),
            ("set-cookie", "session=leak"),
            ("authorization", "Bearer leak"),
            ("x-internal", "leak"),
            ("retry-after", "30"),
            ("x-ratelimit-remaining", "99"),
        ])

    wire([PUBLIC_IP], handler)
    agent, _, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        headers = response.json()["headers"]
        # 契約 §4.2 第 7 點的白名單：content-type / content-length / date /
        # retry-after / x-ratelimit-*。`content-length` 由 httpx 自己補上。
        assert set(headers) <= {"content-type", "content-length", "date",
                                "retry-after"} | {
            key for key in headers if key.startswith("x-ratelimit-")}
        assert {"content-type", "retry-after", "x-ratelimit-remaining"} <= set(headers)
        assert "set-cookie" not in headers and "authorization" not in headers
        assert "x-internal" not in headers
        assert "leak" not in json.dumps(headers)
    finally:
        await cleanup(agent.id)


# ── E-3：不跟隨 redirect（Z19-26）─────────────────────────────────────

async def test_redirects_are_blocked_and_not_followed(
    app_client, redis_client, wire,
):
    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.example.net/x"})

    recorder = wire([PUBLIC_IP], handler)
    agent, run, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        assert response.status_code == 403
        details = response.json()["error"]["details"]
        assert details["reason"] == "REDIRECT"
        assert details["location"] == "https://evil.example.net/x"
        # **stub 只收到一個請求** —— 證明沒有跟過去
        assert len(recorder.requests) == 1
        rows = await audit_rows( run.id)
        assert rows[0].error == "EGRESS_BLOCKED: REDIRECT"
    finally:
        await cleanup(agent.id)


# ── E-2：沙盒不得自帶 header（Z19-27 / U1）────────────────────────────

@pytest.mark.parametrize("header", ["Authorization", "Cookie", "X-Api-Key", "User-Agent"])
async def test_sandbox_supplied_headers_are_422_and_named(
    app_client, redis_client, wire, header,
):
    recorder = wire([PUBLIC_IP], ok_json())
    agent, run, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request",
            json={"url": URL, "headers": {header: "x"}}, headers=auth(token))
        # v0.12.1 U1：這是「請求本身寫錯了」，不是「被政策擋下」
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"]["header"] == header
        # §4.3 的 reason 表只服務 EGRESS_BLOCKED
        assert "reason" not in error["details"]
        assert recorder.requests == []
        # U3：422 也要寫稽核
        rows = await audit_rows( run.id)
        assert len(rows) == 1
        assert rows[0].error.startswith("VALIDATION_ERROR:")
    finally:
        await cleanup(agent.id)


async def test_accept_and_content_type_are_allowed_through(
    app_client, redis_client, wire,
):
    """反向斷言。少了它，一個「全部拒絕」的實作也會全綠。"""
    recorder = wire([PUBLIC_IP], ok_json())
    agent, _, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request",
            json={"url": URL, "headers": {"Accept": "application/json"}},
            headers=auth(token))
        assert response.status_code == 200, response.text
        assert recorder.requests[0].headers["accept"] == "application/json"
        assert recorder.requests[0].headers["user-agent"] == "Zentra-Egress/1"
    finally:
        await cleanup(agent.id)


async def test_the_platform_injects_the_rule_credential(
    app_client, redis_client, wire,
):
    """機密**從頭到尾沒進過沙盒**：沙盒送的 body 裡沒有它，平台才加上去。"""
    from apps.api.services import agent_secrets

    recorder = wire([PUBLIC_IP], ok_json())
    agent, _, token = await setup_agent(redis_client)
    try:
        from apps.api import db

        async with db.transaction() as conn:
            await agent_secrets.set_secret(conn, agent.id, "EXCHANGE_KEY", "s3cret-value")
        await insert_egress_rule(
            agent.id, host=HOST, methods=["GET"],
            auth_secret_name="EXCHANGE_KEY", auth_header="X-MBX-APIKEY")

        response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        assert response.status_code == 200, response.text
        assert recorder.requests[0].headers["x-mbx-apikey"] == "s3cret-value"
    finally:
        await cleanup(agent.id)


# ── E-6：上限（Z19-34 / U4 / U5）──────────────────────────────────────

async def test_timeout_over_the_limit_is_422_not_clamped(
    app_client, redis_client, wire,
):
    wire([PUBLIC_IP], ok_json())
    agent, run, token = await setup_agent(redis_client, rules=[{}])
    try:
        for bad in (16, 0, 10.5, "10"):
            response = await app_client.post(
                "/internal/egress/request",
                json={"url": URL, "timeout_seconds": bad}, headers=auth(token))
            assert response.status_code == 422, (bad, response.text)
            assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        # 每一次都要留下一列（U3）
        assert len(await audit_rows( run.id)) == 4
    finally:
        await cleanup(agent.id)


async def test_request_and_response_size_limits(
    app_client, redis_client, wire, monkeypatch,
):
    from apps.api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "egress_max_request_bytes", 1024, raising=False)
    monkeypatch.setattr(settings, "egress_max_response_bytes", 512, raising=False)

    wire([PUBLIC_IP], ok_json(body="x" * 4096))
    agent, run, token = await setup_agent(
        redis_client, rules=[{"methods": ["GET", "POST"]}])
    try:
        oversized = await app_client.post(
            "/internal/egress/request",
            json={"url": URL, "method": "POST", "body": "y" * 2048},
            headers=auth(token))
        assert oversized.status_code == 403
        assert oversized.json()["error"]["details"]["reason"] == "REQUEST_TOO_LARGE"

        big_response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        assert big_response.status_code == 403
        assert big_response.json()["error"]["details"]["reason"] == "RESPONSE_TOO_LARGE"
        # **不截斷**：回的是錯誤，不是半截 body
        assert "body" not in big_response.json()
    finally:
        await cleanup(agent.id)


async def test_run_call_limit_counts_blocked_attempts_too(
    app_client, redis_client, wire, monkeypatch,
):
    """U5：**被擋下的也算。** 只計成功的話，一個一直打非白名單主機的迴圈
    完全不受限 —— 而會失控的迴圈通常正是一直失敗的那個。"""
    from apps.api.config import get_settings

    monkeypatch.setattr(get_settings(), "egress_max_calls_per_run", 2, raising=False)
    wire([PUBLIC_IP], ok_json())
    agent, run, token = await setup_agent(redis_client, rules=[{}])
    try:
        first = await app_client.post("/internal/egress/request",
                                      json={"url": "https://nope.example.org/x"},
                                      headers=auth(token))
        assert first.json()["error"]["details"]["reason"] == "HOST_NOT_ALLOWED"
        second = await app_client.post("/internal/egress/request",
                                       json={"url": URL}, headers=auth(token))
        assert second.status_code == 200
        third = await app_client.post("/internal/egress/request",
                                      json={"url": URL}, headers=auth(token))
        assert third.status_code == 403
        assert third.json()["error"]["details"]["reason"] == "RUN_CALL_LIMIT"

        # 換一筆 Run，計數歸零
        other = await make_run(agent.id, run.agent_version_id)
        other_token = await issue_run_token(redis_client, other.id)
        fresh = await app_client.post("/internal/egress/request",
                                      json={"url": URL}, headers=auth(other_token))
        assert fresh.status_code == 200
    finally:
        await cleanup(agent.id)


async def test_upstream_failure_is_egress_failed_and_retryable(
    app_client, redis_client, wire,
):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    wire([PUBLIC_IP], handler)
    agent, run, token = await setup_agent(redis_client, rules=[{}])
    try:
        response = await app_client.post(
            "/internal/egress/request", json={"url": URL}, headers=auth(token))
        assert response.status_code == 502
        error = response.json()["error"]
        assert error["code"] == "EGRESS_FAILED"
        assert error["retryable"] is True
        rows = await audit_rows( run.id)
        assert rows[0].error.startswith("EGRESS_FAILED:")
    finally:
        await cleanup(agent.id)


# ── E-7：稽核（Z19-45 / Z19-46 / Z19-49）──────────────────────────────

async def test_audit_covers_every_attempt_and_strips_the_query_string(
    app_client, redis_client, wire,
):
    wire([PUBLIC_IP], ok_json())
    agent, run, token = await setup_agent(redis_client, rules=[{}])
    sentinel = "s3nt1nel-t0ken"
    try:
        await app_client.post(
            "/internal/egress/request",
            json={"url": f"https://{HOST}/api/v3/klines?token={sentinel}"},
            headers=auth(token))
        await app_client.post(
            "/internal/egress/request",
            json={"url": "https://nope.example.org/x"}, headers=auth(token))

        rows = await audit_rows( run.id)
        assert len(rows) == 2
        assert rows[0].path == "/api/v3/klines", "query string 不入庫"
        assert sentinel not in json.dumps([r.path for r in rows])
        assert rows[0].error is None and rows[0].status_code == 200
        assert rows[1].error == "EGRESS_BLOCKED: HOST_NOT_ALLOWED"
        assert rows[1].status_code is None

        # 公開查詢看得到同樣的東西
        listing = await app_client.get(
            f"/v1/agents/{agent.id}/egress-calls?run_id={run.id}")
        assert listing.status_code == 200
        body = listing.json()
        assert len(body["items"]) == 2
        assert "next_cursor" in body
        assert all(item["path"].startswith("/") for item in body["items"])
        assert sentinel not in listing.text
    finally:
        await cleanup(agent.id)


# ── token scope（Z19-53）──────────────────────────────────────────────

async def test_container_and_validation_tokens_cannot_use_egress(
    app_client, redis_client,
):
    from apps.api.services import containertoken, validationtoken
    from redis import Redis as SyncRedis
    import os as _os

    agent = await insert_agent(name=f"zen019-scope-{os.urandom(5).hex()}")
    version = await make_published_version(agent.id)
    sync = SyncRedis.from_url(
        _os.environ.get("ZENTRA_TEST_REDIS_URL", "redis://redis:6379/0"),
        decode_responses=True)
    try:
        container_token = containertoken.issue_sync(sync, agent.id, 300, new_id())
        validation_token = validationtoken.issue_sync(sync, version.id, 300)
        for token in (container_token, validation_token):
            for path in ("/internal/egress/request", "/internal/notify/email"):
                response = await app_client.post(path, json={}, headers=auth(token))
                assert response.status_code == 403, (path, response.text)
                assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        # 沒有 token / 亂給 token
        for headers in ({}, auth("not-a-token")):
            response = await app_client.post(
                "/internal/egress/request", json={"url": URL}, headers=headers)
            assert response.status_code == 401
    finally:
        sync.close()
        await cleanup(agent.id)
