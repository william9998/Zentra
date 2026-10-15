"""ZEN-019：寄信（api-contract §4.4 / ADR-0014 §4）。

SMTP stub 是一個**自己寫的 socket server**，它記下收到的每一個指令 ——
因為這裡最重要的斷言不是「回了什麼錯」，而是
**「那封信到底有沒有被送出去」**：

  M-1  收件人不在白名單 → stub **連線都不該有**（全有全無，不是部分寄出）
  M-2  升不上 TLS       → stub **不該收到 `MAIL` 或 `DATA`**（密碼與內容沒外流）
  M-3  沒設寄件者       → `EGRESS_BLOCKED`，**不是靜默不寄**
  M-4  上限計的是嘗試   → 被擋下的也算，包含認證失敗（Gmail 鎖的是使用者的帳號）
  M-5  每一次都寫稽核   → `422` 與被擋下的都要寫

**沒驗到的**：`465` / `587` 的成功路徑與 `SMTP_AUTH`。
兩者都要一張客戶端信得過的 TLS 憑證（平台一律 `ssl.create_default_context()`
做驗證，自簽憑證會先炸在 TLS 握手而不是認證），這個環境沒有。
見交付說明的「未覆蓋」。
"""

from __future__ import annotations

import os
import socket
import threading
from datetime import UTC, datetime

import pytest

from tests.zen019.conftest import (
    auth, fetch_egress_calls, insert_agent, insert_egress_call,
    insert_email_sender, issue_run_token, make_published_version, make_run,
    purge_agent, requires_db,
)

pytestmark = requires_db


class SmtpStub:
    """只講到「能讓 smtplib 走到該走的那一步」為止的 SMTP。

    刻意**不支援 STARTTLS**：那正是 `TLS_REQUIRED` 要驗的狀況。
    `commands` 讓測試回答「它到底收到了什麼」—— 這比回應碼重要得多。
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self.commands: list[str] = []
        self.connections = 0
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", port))
        self._server.listen(5)
        self._server.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except OSError:
                continue
            self.connections += 1
            threading.Thread(target=self._handle, args=(connection,),
                             daemon=True).start()

    def _handle(self, connection: socket.socket) -> None:
        connection.settimeout(5)
        connection.sendall(b"220 stub ESMTP\r\n")
        buffer = b""
        try:
            while not self._stop.is_set():
                chunk = connection.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\r\n" in buffer:
                    line, buffer = buffer.split(b"\r\n", 1)
                    command = line.decode("utf-8", "replace")
                    self.commands.append(command)
                    verb = command.split(" ", 1)[0].upper()
                    if verb in ("EHLO", "HELO"):
                        # **沒有 STARTTLS** —— 平台必須因此放棄，而不是改用明文。
                        connection.sendall(b"250-stub\r\n250 SIZE 10240000\r\n")
                    elif verb == "QUIT":
                        connection.sendall(b"221 bye\r\n")
                        return
                    else:
                        connection.sendall(b"502 not implemented\r\n")
        except OSError:
            return
        finally:
            connection.close()

    def close(self) -> None:
        self._stop.set()
        self._server.close()


    def reset(self) -> None:
        self.commands.clear()
        self.connections = 0


#: **一個 session 綁一次 port。** 每個測試各自 bind 的話，
#: 上一個 listening socket 還沒真的釋放就會 `Address already in use`，
#: 而那個錯看起來像測試環境壞掉，其實只是收尾還沒完成。
#: port 只能是 25／465／587（`agent_email_sender` 的 CHECK 約束）。
@pytest.fixture(scope="session")
def _smtp_server():
    stub = SmtpStub(25)
    yield stub
    stub.close()


@pytest.fixture
def smtp_stub(_smtp_server):
    _smtp_server.reset()
    return _smtp_server


async def _setup(redis_client, *, sender=None, recipients=("ok@example.com",)):
    """建 Agent + 機密 + （選填）寄件者 + RUNNING 的 Run。

    寄件者**直接寫 DB**：測試 stub 跑在 `127.0.0.1` 上，而公開端點的
    `validate_host()` 只收 DNS 名稱（那條規則本身由 `test_public_contract` 驗）。
    """
    from apps.api.services import agent_secrets

    agent = await insert_agent(name=f"zen019-mail-{os.urandom(5).hex()}")
    from apps.api import db

    async with db.transaction() as conn:
        await agent_secrets.set_secret(conn, agent.id, "SMTP_PASSWORD", "app-password")
    if sender is not None:
        await insert_email_sender(
            agent.id, host=sender["host"], port=sender["port"],
            recipients=list(recipients))
    version = await make_published_version(agent.id)
    run = await make_run(agent.id, version.id)
    token = await issue_run_token(redis_client, run.id)
    return agent, run, token


async def audit(run_id):
    return await fetch_egress_calls(run_id)


# ── M-3：沒設寄件者不是靜默不寄（Z19-41）─────────────────────────────

async def test_missing_sender_is_blocked_loudly_and_audited(
    app_client, redis_client,
):
    agent, run, token = await _setup(redis_client, sender=None)
    try:
        response = await app_client.post(
            "/internal/notify/email",
            json={"to": ["ok@example.com"], "subject": "s", "text": "t"},
            headers=auth(token))
        assert response.status_code == 403, response.text
        error = response.json()["error"]
        assert error["code"] == "EGRESS_BLOCKED"
        assert error["details"]["reason"] == "SENDER_NOT_CONFIGURED"
        assert error["retryable"] is False

        rows = await audit( run.id)
        assert len(rows) == 1 and rows[0].kind == "EMAIL"
        assert rows[0].error == "EGRESS_BLOCKED: SENDER_NOT_CONFIGURED"
    finally:
        await purge_agent( agent.id)


# ── M-1：全有全無（Z19-40）───────────────────────────────────────────

async def test_one_bad_recipient_drops_the_whole_message(
    app_client, redis_client, smtp_stub,
):
    """**部分寄出會讓「到底寄了沒」說不清楚。** 所以 stub 連線數必須是 0。"""
    agent, run, token = await _setup(
        redis_client,
        sender={"host": "127.0.0.1", "port": smtp_stub.port},
        recipients=("ok@example.com",))
    try:
        response = await app_client.post(
            "/internal/notify/email",
            json={"to": ["ok@example.com", "nope@example.com"],
                  "subject": "s", "text": "t"},
            headers=auth(token))
        assert response.status_code == 403, response.text
        details = response.json()["error"]["details"]
        assert details["reason"] == "RECIPIENT_NOT_ALLOWED"
        assert details["recipient"] == "nope@example.com"
        # **一封都沒寄** —— 連 SMTP 連線都沒建立
        assert smtp_stub.connections == 0
        assert smtp_stub.commands == []

        rows = await audit( run.id)
        assert len(rows) == 1
        assert rows[0].error == "EGRESS_BLOCKED: RECIPIENT_NOT_ALLOWED"
        assert rows[0].host == "127.0.0.1"
        assert rows[0].path == "ok@example.com, nope@example.com"
        assert rows[0].status_code is None and rows[0].method is None
    finally:
        await purge_agent( agent.id)


# ── M-2：升不上 TLS 就不寄（Z19-43 的 TLS 部分）──────────────────────

async def test_tls_required_sends_nothing(
    app_client, redis_client, smtp_stub,
):
    """平台一律要求 TLS。**升不上去就不寄** —— 不會退而用明文送密碼。"""
    agent, run, token = await _setup(
        redis_client,
        sender={"host": "127.0.0.1", "port": smtp_stub.port})
    try:
        response = await app_client.post(
            "/internal/notify/email",
            json={"to": ["ok@example.com"], "subject": "s", "text": "secret body"},
            headers=auth(token))
        assert response.status_code == 403, response.text
        assert response.json()["error"]["details"]["reason"] == "TLS_REQUIRED"

        assert smtp_stub.connections == 1
        verbs = {command.split(" ", 1)[0].upper() for command in smtp_stub.commands}
        # 走到 EHLO 是必然的（要先問才知道有沒有 STARTTLS），
        # 但 **MAIL / RCPT / DATA / AUTH 一個都不准出現**
        assert "EHLO" in verbs
        assert not verbs & {"MAIL", "RCPT", "DATA", "AUTH"}
        assert not any("secret body" in command for command in smtp_stub.commands)

        rows = await audit( run.id)
        assert rows[0].error == "EGRESS_BLOCKED: TLS_REQUIRED"
    finally:
        await purge_agent( agent.id)


async def test_unreachable_smtp_is_egress_failed_and_retryable(
    app_client, redis_client,
):
    """政策放行了但連不上 → `502 EGRESS_FAILED`，可重試（M5 的另一半）。"""
    agent, run, token = await _setup(
        redis_client,
        sender={"host": "127.0.0.1", "port": 465})   # 沒有人在聽
    try:
        response = await app_client.post(
            "/internal/notify/email",
            json={"to": ["ok@example.com"], "subject": "s", "text": "t"},
            headers=auth(token))
        assert response.status_code == 502, response.text
        error = response.json()["error"]
        assert error["code"] == "EGRESS_FAILED"
        assert error["retryable"] is True

        rows = await audit( run.id)
        assert rows[0].error.startswith("EGRESS_FAILED:")
        assert rows[0].latency_ms is not None
    finally:
        await purge_agent( agent.id)


# ── 請求不能奪權（Z19-44 的 422 部分）────────────────────────────────

@pytest.mark.parametrize("body", [
    {"to": ["ok@example.com"], "subject": "s", "text": "t", "from": "evil@x.com"},
    {"to": ["ok@example.com"], "subject": "s", "text": "t", "smtp_host": "evil"},
    {"to": ["ok@example.com"], "subject": "s", "text": "t", "password": "p"},
    {"to": ["ok@example.com"], "subject": "s", "text": "t", "run_id": "x"},
    {"to": [], "subject": "s", "text": "t"},
    {"to": [f"a{i}@x.com" for i in range(11)], "subject": "s", "text": "t"},
    {"to": ["ok@example.com"], "subject": "x" * 201, "text": "t"},
    {"to": ["ok@example.com"], "subject": "s"},               # text 與 html 都缺
    {"to": ["ok@example.com"], "subject": "", "text": "t"},
])
async def test_malformed_requests_are_422_and_still_audited(
    app_client, redis_client, smtp_stub, body,
):
    """U3：`422` 也要寫稽核列 —— 那條路徑上一列都沒有才是最糟的情況。"""
    agent, run, token = await _setup(
        redis_client,
        sender={"host": "127.0.0.1", "port": smtp_stub.port})
    try:
        response = await app_client.post("/internal/notify/email", json=body,
                                         headers=auth(token))
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert smtp_stub.connections == 0

        rows = await audit( run.id)
        assert len(rows) == 1
        assert rows[0].error.startswith("VALIDATION_ERROR:")
    finally:
        await purge_agent( agent.id)


# ── M-4：上限計的是「嘗試」（Z19-42 / U5 / U6）───────────────────────

async def test_per_run_and_per_hour_limits_count_attempts(
    app_client, redis_client, smtp_stub, monkeypatch,
):
    """**上限算的是 `egress_calls` 的列數，被擋下的也算**（U5／U6）。

    這裡直接在稽核表裡種「過去的嘗試」，因為那就是計數的唯一來源 ——
    契約明寫「不另外維護計數器：兩份會不一致，而不一致時沒有辦法判斷哪份是對的」。

    ⚠️ 檢查順序：收件人白名單排在上限**前面**（契約 §4.4 的規則順序）。
    所以一個收件人寫錯的迴圈會一直拿到 `RECIPIENT_NOT_ALLOWED`，不會變成
    `RUN_EMAIL_LIMIT` —— 那是對的：它連線都沒建立，碰不到使用者的信箱帳號。
    """
    from datetime import timedelta

    from apps.api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "email_max_per_run", 2, raising=False)
    monkeypatch.setattr(settings, "email_max_per_hour", 3, raising=False)

    agent, run, token = await _setup(
        redis_client,
        sender={"host": "127.0.0.1", "port": smtp_stub.port})
    message = {"to": ["ok@example.com"], "subject": "s", "text": "t"}
    try:
        async def seed(run_id, *, minutes_ago=0, blocked=True):
            await insert_egress_call(
                agent.id, run_id, kind="EMAIL", host="127.0.0.1",
                path="ok@example.com", request_bytes=1, response_bytes=0,
                latency_ms=None if blocked else 500,
                error="EGRESS_BLOCKED: SMTP_AUTH" if blocked else None,
                created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago))

        # 這筆 Run 已經嘗試過兩次（含一次認證失敗 —— 那正是 Gmail 會鎖帳號的情況）
        await seed(run.id)
        await seed(run.id, blocked=False)

        limited = await app_client.post("/internal/notify/email", json=message,
                                        headers=auth(token))
        assert limited.status_code == 403, limited.text
        assert limited.json()["error"]["details"]["reason"] == "RUN_EMAIL_LIMIT"
        assert smtp_stub.connections == 0

        # 換一筆 Run：每 Run 的計數歸零，但**每小時是跨 Run 累計的**。
        # 上面那兩列 + 剛才被擋下的那一列 = 3，已達每小時上限。
        other = await make_run(agent.id, run.agent_version_id)
        other_token = await issue_run_token(redis_client, other.id)
        hourly = await app_client.post("/internal/notify/email", json=message,
                                       headers=auth(other_token))
        assert hourly.status_code == 403, hourly.text
        assert hourly.json()["error"]["details"]["reason"] == "HOURLY_EMAIL_LIMIT"
        assert smtp_stub.connections == 0
    finally:
        await purge_agent( agent.id)


async def test_the_hourly_window_rolls_it_is_not_an_hour_bucket(
    app_client, redis_client, smtp_stub, monkeypatch,
):
    """U6：**滾動 60 分鐘，不是整點桶。** 整點桶允許 `:59` 寄 60 封、
    `:01` 再寄 60 封 —— 兩分鐘 120 封，那正是這條上限存在的理由。"""
    from datetime import timedelta

    from apps.api.config import get_settings

    monkeypatch.setattr(get_settings(), "email_max_per_hour", 1, raising=False)
    agent, run, token = await _setup(
        redis_client,
        sender={"host": "127.0.0.1", "port": smtp_stub.port})
    try:
        # 61 分鐘前的那一封已經滑出窗口，不該計入
        await insert_egress_call(
            agent.id, run.id, kind="EMAIL", host="127.0.0.1",
            path="ok@example.com", request_bytes=1, response_bytes=0,
            latency_ms=500,
            created_at=datetime.now(UTC) - timedelta(minutes=61))

        response = await app_client.post(
            "/internal/notify/email",
            json={"to": ["ok@example.com"], "subject": "s", "text": "t"},
            headers=auth(token))
        # 窗口內是 0 封 → 放行到 SMTP，然後因為 stub 不支援 STARTTLS 才被擋
        assert response.json()["error"]["details"]["reason"] == "TLS_REQUIRED", \
            "61 分鐘前那一封不該還算在這一小時裡"
    finally:
        await purge_agent( agent.id)


async def test_oversized_body_is_blocked(
    app_client, redis_client, smtp_stub, monkeypatch,
):
    from apps.api.config import get_settings

    monkeypatch.setattr(get_settings(), "email_max_body_bytes", 1024, raising=False)
    agent, run, token = await _setup(
        redis_client,
        sender={"host": "127.0.0.1", "port": smtp_stub.port})
    try:
        response = await app_client.post(
            "/internal/notify/email",
            json={"to": ["ok@example.com"], "subject": "s", "text": "x" * 2048},
            headers=auth(token))
        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "BODY_TOO_LARGE"
        assert smtp_stub.connections == 0
        rows = await audit( run.id)
        assert rows[0].request_bytes == 2048
    finally:
        await purge_agent( agent.id)


# ── Z19-47：EMAIL 列的形狀 ───────────────────────────────────────────

async def test_email_audit_rows_use_the_shared_columns(
    app_client, redis_client, smtp_stub,
):
    agent, run, token = await _setup(
        redis_client,
        sender={"host": "127.0.0.1", "port": smtp_stub.port},
        recipients=("a@example.com", "b@example.com"))
    try:
        await app_client.post(
            "/internal/notify/email",
            json={"to": ["a@example.com", "b@example.com"], "subject": "s",
                  "text": "hello"},
            headers=auth(token))
        rows = await audit( run.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.kind == "EMAIL"
        assert row.host == "127.0.0.1"           # SMTP 主機
        assert row.path == "a@example.com, b@example.com"   # 收件人，`, ` 串接
        assert row.method is None and row.rule_id is None and row.status_code is None
        assert row.request_bytes > 0
    finally:
        await purge_agent( agent.id)
