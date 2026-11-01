"""寄信（ZEN-019 / api-contract §4.4、ADR-0014 §4）。

**SMTP 不是 HTTP，所以不走 §4.2 的 proxy** —— 硬要走的話得先發明一個
SMTP-over-HTTP 的形狀。

寄件者、帳號、密碼**都由平台決定**（§2.9）。沙盒送的只有收件人、主旨與內容；
密碼在這裡才從 `agent_secrets` 解密，用完就丟，從頭到尾沒進過沙盒（P4）。

## 為什麼上限這麼小

訊號型 agent 最典型的 bug 就是**重試迴圈裡寄信**。Gmail 鎖的是使用者
本人的帳號，不是這支 agent —— 所以預設每 Run 5 封、每小時 60 封（M3），
而且收件人要在白名單裡（M1）。

## `202` 的意思

SMTP 伺服器**收下了**，不表示對方讀到了。不附檔、不排程、不做退信處理。
**不冪等**：重送就是再寄一封。要冪等由 agent 自己記狀態 ——
平台做冪等要定義「什麼算同一封信」，那個定義一定會有人不同意。
"""

import asyncio
import smtplib
import ssl
import time
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

import asyncpg
from pydantic import ValidationError

from apps.api import db
from apps.api.config import get_settings
from apps.api.enums import EgressKind, EgressReason, ErrorCode
from apps.api.rows import AgentEmailSender
from apps.api.schemas import NotifyEmailRequest
from apps.api.services import agent_secrets, egress, egress_rules

MAX_RECIPIENTS_PER_MESSAGE = 10
MAX_SUBJECT_LENGTH = 200
#: 沒有寄件者設定時，稽核列的 `host` 要放什麼。欄位是 NOT NULL，而「這個
#: Agent 根本沒設寄件者」本身就是使用者要看到的資訊。
_NO_SENDER_HOST = "(not configured)"


class _SmtpAuthFailed(Exception):
    """密碼錯了重試一百次還是錯，而且 Gmail 會因此鎖帳號 —— 歸在 blocked（M5）。"""


class _TlsUnavailable(Exception):
    """連上了但升不上 TLS。平台一律要求 TLS，升不上去就不寄。"""


def _send_blocking(
    *, smtp_host: str, smtp_port: int, username: str, password: str,
    message: EmailMessage, recipients: list[str], timeout: float,
) -> None:
    """真正的 SMTP 對話。**阻塞**，所以呼叫端用 `asyncio.to_thread` 包起來。

    465 是隱式 TLS，587／25 先明文連線再 `STARTTLS`。
    **明文 SMTP 不支援** —— 升不上去就當作被擋下，不要退而用明文送密碼。
    """
    context = ssl.create_default_context()
    if smtp_port == 465:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout, context=context)
    else:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout)
    try:
        server.ehlo()
        if smtp_port != 465:
            try:
                server.starttls(context=context)
            except smtplib.SMTPNotSupportedError as exc:
                raise _TlsUnavailable(str(exc)) from exc
            server.ehlo()
        try:
            server.login(username, password)
        except smtplib.SMTPAuthenticationError as exc:
            raise _SmtpAuthFailed(f"{exc.smtp_code} {exc.smtp_error!r}") from exc
        server.send_message(message, to_addrs=recipients)
    finally:
        try:
            server.quit()
        except Exception:                       # noqa: BLE001 - 收尾失敗不該蓋掉結果
            server.close()


async def _count(
    conn: asyncpg.Connection, *, run_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None, since: datetime | None = None,
) -> int:
    """**嘗試**過幾封（契約 §4.4 第 3 點 / U6）。

    **被擋下的也算，包含 `SMTP_AUTH` 失敗** —— 反覆認證失敗正是 Gmail 鎖帳號
    的主因之一，那種情況要的是停下來，不是「因為沒寄成功所以可以再試」。

    計數就是 `egress_calls` 的列數，**不另外維護計數器**（兩份會不一致）。
    每小時是**滾動 60 分鐘窗口**，不是整點桶：整點桶允許 `:59` 寄 60 封、
    `:01` 再寄 60 封，兩分鐘 120 封，那正是這條上限存在的理由。
    """
    clauses = ["kind = $1"]
    args: list[Any] = [str(EgressKind.EMAIL)]
    n = 2
    if run_id is not None:
        clauses.append(f"run_id = ${n}")
        args.append(run_id)
        n += 1
    if agent_id is not None:
        clauses.append(f"agent_id = ${n}")
        args.append(agent_id)
        n += 1
    if since is not None:
        clauses.append(f"created_at >= ${n}")
        args.append(since)
    value = await conn.fetchval(
        f"SELECT count(call_id) FROM egress_calls WHERE {' AND '.join(clauses)}",
        *args,
    )
    return int(value or 0)


def _peek_recipients(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("to"), list):
        return ", ".join(str(item) for item in payload["to"])
    return "(invalid request)"


async def send(*, run_id: uuid.UUID, agent_id: uuid.UUID, payload: Any) -> str:
    """寄一封信，回 `message_id`。

    每一個通過認證的請求都寫一列 `egress_calls`，**`422` 與被擋下的都要寫**
    （契約 §2.10 第 4 點）。所以驗證在這裡做，不是交給 FastAPI 的 body 綁定 ——
    那條路徑會在進到這個函式之前就回 `422`，寫不了稽核列。
    """
    settings = get_settings()
    run_id_text = str(run_id)

    async with db.connection() as conn:
        sender: AgentEmailSender | None = await egress_rules.get_sender(conn, agent_id)
    host = sender.smtp_host if sender is not None else _NO_SENDER_HOST
    audit_path = _peek_recipients(payload)

    async def write(**kwargs) -> None:
        async with db.transaction() as conn:
            await egress.record(
                conn, run_id=run_id, agent_id=agent_id, kind=EgressKind.EMAIL,
                host=kwargs.pop("host", host), path=kwargs.pop("path", audit_path),
                **kwargs,
            )

    async def block(reason: EgressReason, message: str, request_bytes: int = 0, **details):
        await write(request_bytes=request_bytes, error=egress.block_error_text(reason))
        return egress.blocked(reason, message, run_id_text, **details)

    try:
        body = NotifyEmailRequest.model_validate(payload)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error.get("loc", ())) or "body"
        detail = f"{location}: {error.get('msg', 'invalid')}"
        await write(error=egress.invalid_error_text(detail))
        raise egress.invalid(
            f"the email request is malformed ({detail}). The sender address, SMTP "
            "host and password are decided by the platform -- they cannot be set "
            "here (see the email sender settings).",
            run_id_text, field=location,
        ) from exc

    recipients = [address.strip() for address in body.to]
    subject, text, html = body.subject, body.text, body.html
    audit_path = ", ".join(recipients)

    if sender is None:
        # **不是靜默不寄** —— agent 要知道信沒寄出去（§2.9 第 5 點）。
        raise await block(
            EgressReason.SENDER_NOT_CONFIGURED,
            "this agent has no email sender configured, so nothing was sent. "
            "Configure one in the console (Agent > 對外出口 > 寄件者).")

    if not recipients or len(recipients) > MAX_RECIPIENTS_PER_MESSAGE:
        raise await block(
            EgressReason.RECIPIENT_NOT_ALLOWED,
            f"a message takes 1-{MAX_RECIPIENTS_PER_MESSAGE} recipients, got "
            f"{len(recipients)}")

    # **收件人逐一比對。有任何一個不在清單裡，整封信都不寄** ——
    # 部分寄出會讓「到底寄了沒」說不清楚（§4.4 第 2 點）。
    allowed = set(sender.allowed_recipients or [])
    for address in recipients:
        if address not in allowed:
            raise await block(
                EgressReason.RECIPIENT_NOT_ALLOWED,
                f"{address} is not in this agent's allowed_recipients, so the whole "
                "message was dropped", recipient=address)

    body_bytes = len((text or "").encode()) + len((html or "").encode())
    if body_bytes > settings.email_max_body_bytes:
        raise await block(
            EgressReason.BODY_TOO_LARGE,
            f"text + html is {body_bytes} bytes, the limit is "
            f"{settings.email_max_body_bytes}", request_bytes=body_bytes)

    async with db.connection() as conn:
        per_run = await _count(conn, run_id=run_id)
        per_hour = await _count(
            conn, agent_id=agent_id, since=datetime.now(UTC) - timedelta(hours=1))
        password = await agent_secrets.resolve(conn, agent_id, sender.password_secret_name)
    if per_run >= settings.email_max_per_run:
        raise await block(
            EgressReason.RUN_EMAIL_LIMIT,
            f"this run already attempted {per_run} emails, the limit is "
            f"{settings.email_max_per_run}", request_bytes=body_bytes)
    if per_hour >= settings.email_max_per_hour:
        raise await block(
            EgressReason.HOURLY_EMAIL_LIMIT,
            f"this agent attempted {per_hour} emails in the last 60 minutes, "
            f"the limit is "
            f"{settings.email_max_per_hour}", request_bytes=body_bytes)
    if password is None:
        raise await block(
            EgressReason.SENDER_NOT_CONFIGURED,
            f"the sender references secret {sender.password_secret_name!r}, which no "
            "longer exists; store it again", request_bytes=body_bytes)

    message = EmailMessage()
    message["From"] = sender.from_address
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message_id = make_msgid(domain="zentra")
    message["Message-ID"] = message_id
    if text:
        message.set_content(text)
    else:
        message.set_content("")
    if html:
        message.add_alternative(html, subtype="html")

    started = time.monotonic()
    try:
        await asyncio.to_thread(
            _send_blocking,
            smtp_host=sender.smtp_host, smtp_port=sender.smtp_port,
            username=sender.username, password=password, message=message,
            recipients=recipients, timeout=settings.email_timeout_seconds,
        )
    except _SmtpAuthFailed as exc:
        raise await block(
            EgressReason.SMTP_AUTH,
            f"the SMTP server rejected the credentials ({exc}). For Gmail this must be "
            "an app password, not the account password.",
            request_bytes=body_bytes) from exc
    except _TlsUnavailable as exc:
        raise await block(
            EgressReason.TLS_REQUIRED,
            f"{sender.smtp_host}:{sender.smtp_port} does not offer STARTTLS and the "
            "platform will not send credentials in the clear", request_bytes=body_bytes,
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        latency = int((time.monotonic() - started) * 1000)
        await write(
            host=sender.smtp_host, path=audit_path, request_bytes=body_bytes,
            latency_ms=latency,
            error=f"{ErrorCode.EGRESS_FAILED}: {type(exc).__name__}: {exc}",
        )
        raise egress.failed(
            f"could not send through {sender.smtp_host}: {type(exc).__name__}: {exc}",
            run_id_text) from exc

    latency = int((time.monotonic() - started) * 1000)
    await write(
        host=sender.smtp_host, path=audit_path, request_bytes=body_bytes,
        latency_ms=latency,
    )
    return message_id
