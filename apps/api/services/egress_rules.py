"""對外出口白名單與寄件者設定（ZEN-019 / api-contract §2.8、§2.9）。

這兩張表回答同一個問題：**平台願意替這個 Agent 把東西送到哪裡去。**

白名單的價值全部來自「預設拒絕」（ADR-0014 E1）：一條規則都沒有，
一個外部主機都連不到。所以這裡的驗證是**故意嚴格**的 ——
`*`、scheme、port、路徑、大寫、IP 字面值一律 `422`，
有一個逃生門就等於沒有白名單。
"""

import ipaddress
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.ids import uuid7
from apps.api.models import AgentEgressRule, AgentEmailSender
from apps.api.services import agent_secrets

#: 只接受小寫 DNS 名稱。刻意不接受結尾的點 —— `api.binance.com.` 與
#: `api.binance.com` 是同一台主機，但字串比對不同，會讓同一主機出現兩條規則。
_HOST_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST_PATTERN = re.compile(rf"^{_HOST_LABEL}(\.{_HOST_LABEL})+$")

ALLOWED_METHODS = ("GET", "POST")
MAX_RULES = 20
#: 平台自己會送這些，沙盒指名它們只會造成「我設的 header 去哪了」。
FORBIDDEN_AUTH_HEADERS = {"accept", "content-type", "host", "content-length"}
ALLOWED_SMTP_PORTS = (25, 465, 587)
MAX_RECIPIENTS = 10

_EMAIL_PATTERN = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


def _invalid(message: str, field: str) -> ZentraError:
    return ZentraError(ErrorCode.VALIDATION_ERROR, message, 422, details={"field": field})


def validate_host(host: str) -> str:
    """契約 §2.8 第 1 點。錯誤訊息要說出**哪裡**不合法，不是只說「無效」。"""
    if not isinstance(host, str) or not host:
        raise _invalid("host is required", "host")
    if "*" in host:
        raise _invalid(
            "host cannot contain '*': the whitelist has no wildcards, and one escape "
            "hatch is the same as having no whitelist at all", "host")
    if "://" in host:
        raise _invalid("host must not include a scheme -- write api.binance.com, "
                       "not https://api.binance.com", "host")
    if "/" in host:
        raise _invalid("host must not include a path -- use path_prefix for that", "host")
    # IP 字面值要在 port 檢查**之前**判：IPv6 字面值本身就有冒號，
    # 先判 port 的話 `2606:4700::1111` 會得到一句關於 port 的錯誤訊息。
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise _invalid(
            "host must be a DNS name, not an IP literal: IP literals skip the name "
            "that the whitelist is written in terms of", "host")
    if ":" in host:
        raise _invalid("host must not include a port -- only https/443 is proxied", "host")
    if host != host.lower():
        raise _invalid("host must be lowercase", "host")
    if len(host) > 253 or not _HOST_PATTERN.match(host):
        raise _invalid(f"host {host!r} is not a valid DNS name", "host")
    return host


def validate_methods(methods: list[str] | None) -> list[str]:
    """預設 `["GET"]`。要 `POST` 必須在**那一條規則上**明列（ADR-0014 E2）。"""
    if methods is None:
        return ["GET"]
    if not isinstance(methods, list) or not methods:
        raise _invalid("methods cannot be empty", "methods")
    seen: list[str] = []
    for method in methods:
        # **大小寫敏感**：`"get"` 是 `422`，不是默默轉成 `GET`。
        # 規則會被逐字比對（`match_rule` → `method in rule.methods`），
        # 悄悄正規化會讓「我明明寫了」與「它實際存了什麼」對不起來。
        if not isinstance(method, str) or method not in ALLOWED_METHODS:
            raise _invalid(
                f"method {method!r} is not allowed: only the exact strings "
                f"{list(ALLOWED_METHODS)} are proxied", "methods")
        if method not in seen:
            seen.append(method)
    return seen


def validate_path_prefix(path_prefix: str | None) -> str | None:
    if path_prefix is None or path_prefix == "":
        return None
    if not path_prefix.startswith("/"):
        raise _invalid("path_prefix must start with '/'", "path_prefix")
    if "?" in path_prefix or "#" in path_prefix:
        raise _invalid("path_prefix is a path, not a URL: drop the query string",
                       "path_prefix")
    return path_prefix


async def _validate_auth(
    session: AsyncSession, agent_id: uuid.UUID,
    auth_secret_name: str | None, auth_header: str | None,
) -> None:
    """成對出現，而且 header 不能是平台自己要送的那幾個（ADR-0014 E3）。"""
    if (auth_secret_name is None) != (auth_header is None):
        raise _invalid(
            "auth_secret_name and auth_header come as a pair: the platform needs to "
            "know both which secret to inject and which header to put it in",
            "auth_header" if auth_secret_name else "auth_secret_name")
    if auth_secret_name is None:
        return
    if auth_header.lower() in FORBIDDEN_AUTH_HEADERS:
        raise _invalid(
            f"auth_header cannot be {auth_header!r}: the platform sets that header itself",
            "auth_header")
    if not await agent_secrets.exists(session, agent_id, auth_secret_name):
        raise _invalid(
            f"there is no secret named {auth_secret_name!r} for this agent -- "
            "store it first (PUT /v1/agents/{id}/secrets/{name})", "auth_secret_name")


async def list_rules(session: AsyncSession, agent_id: uuid.UUID) -> list[AgentEgressRule]:
    return list((await session.execute(
        select(AgentEgressRule).where(AgentEgressRule.agent_id == agent_id)
        .order_by(AgentEgressRule.host)
    )).scalars().all())


async def load_rule(
    session: AsyncSession, agent_id: uuid.UUID, rule_id: str,
) -> AgentEgressRule:
    try:
        parsed = uuid.UUID(rule_id)
    except ValueError:
        raise not_found("egress rule") from None
    row = await session.get(AgentEgressRule, parsed)
    if row is None or row.agent_id != agent_id:
        raise not_found("egress rule")
    return row


async def create_rule(
    session: AsyncSession, agent_id: uuid.UUID, body,
) -> AgentEgressRule:
    host = validate_host(body.host)
    methods = validate_methods(body.methods)
    path_prefix = validate_path_prefix(body.path_prefix)
    await _validate_auth(session, agent_id, body.auth_secret_name, body.auth_header)

    existing = await list_rules(session, agent_id)
    if any(rule.host == host for rule in existing):
        raise ZentraError(
            ErrorCode.VERSION_CONFLICT,
            f"this agent already has a rule for {host}: which rule applies has to be "
            "obvious at a glance, so hosts are unique per agent",
            409, details={"field": "host"},
        )
    if len(existing) >= MAX_RULES:
        raise _invalid(
            f"this agent already has {len(existing)} rules, the limit is {MAX_RULES}",
            "host")

    now = datetime.now(UTC)
    row = AgentEgressRule(
        rule_id=uuid7(), agent_id=agent_id, host=host, methods=methods,
        path_prefix=path_prefix, auth_secret_name=body.auth_secret_name,
        auth_header=body.auth_header, description=body.description,
        created_at=now, updated_at=now,
    )
    session.add(row)
    await session.commit()
    return row


async def update_rule(
    session: AsyncSession, agent_id: uuid.UUID, row: AgentEgressRule, body,
) -> AgentEgressRule:
    """整條取代（`PUT`）。**改規則立刻生效**，包括對已經在跑的 Run 的下一次呼叫。"""
    host = validate_host(body.host)
    methods = validate_methods(body.methods)
    path_prefix = validate_path_prefix(body.path_prefix)
    await _validate_auth(session, agent_id, body.auth_secret_name, body.auth_header)

    clash = (await session.execute(
        select(AgentEgressRule).where(
            AgentEgressRule.agent_id == agent_id,
            AgentEgressRule.host == host,
            AgentEgressRule.rule_id != row.rule_id,
        )
    )).scalar_one_or_none()
    if clash is not None:
        raise ZentraError(
            ErrorCode.VERSION_CONFLICT,
            f"this agent already has another rule for {host}", 409,
            details={"field": "host"},
        )

    row.host = host
    row.methods = methods
    row.path_prefix = path_prefix
    row.auth_secret_name = body.auth_secret_name
    row.auth_header = body.auth_header
    row.description = body.description
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return row


async def delete_rule(session: AsyncSession, row: AgentEgressRule) -> None:
    """刪規則**不會**抹掉它的稽核紀錄 —— `egress_calls.rule_id` 是 SET NULL。"""
    await session.delete(row)
    await session.commit()


async def match_rule(
    session: AsyncSession, agent_id: uuid.UUID, host: str,
) -> AgentEgressRule | None:
    """host 精確比對。**沒有後綴比對、沒有萬用字元**（E1）。"""
    return (await session.execute(
        select(AgentEgressRule).where(
            AgentEgressRule.agent_id == agent_id, AgentEgressRule.host == host)
    )).scalar_one_or_none()


async def has_rules(session: AsyncSession, agent_id: uuid.UUID) -> bool:
    row = (await session.execute(
        select(AgentEgressRule.rule_id).where(AgentEgressRule.agent_id == agent_id).limit(1)
    )).scalar_one_or_none()
    return row is not None


# ── 寄件者（§2.9）──────────────────────────────────────────────────────


def validate_recipients(recipients: list[str]) -> list[str]:
    """**必填、非空、最多 10 個精確地址，不接受網域萬用字元**（ADR-0014 M1）。"""
    if not isinstance(recipients, list) or not recipients:
        raise _invalid(
            "allowed_recipients is required and cannot be empty: without it, one agent "
            "stuck in a retry loop turns your own mail account into a spam source, and "
            "the provider locks that account -- not this agent",
            "allowed_recipients")
    if len(recipients) > MAX_RECIPIENTS:
        raise _invalid(
            f"allowed_recipients has {len(recipients)} entries, the limit is "
            f"{MAX_RECIPIENTS}", "allowed_recipients")
    cleaned: list[str] = []
    for address in recipients:
        value = str(address).strip()
        if "*" in value:
            raise _invalid("allowed_recipients takes exact addresses, not domain wildcards",
                           "allowed_recipients")
        if not _EMAIL_PATTERN.match(value):
            raise _invalid(f"{value!r} is not a valid email address", "allowed_recipients")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


async def get_sender(session: AsyncSession, agent_id: uuid.UUID) -> AgentEmailSender | None:
    return await session.get(AgentEmailSender, agent_id)


async def set_sender(
    session: AsyncSession, agent_id: uuid.UUID, body,
) -> AgentEmailSender:
    if body.smtp_port not in ALLOWED_SMTP_PORTS:
        raise _invalid(
            f"smtp_port must be one of {list(ALLOWED_SMTP_PORTS)} "
            "(587 STARTTLS, 465 implicit TLS; plaintext SMTP is never used)", "smtp_port")
    validate_host(body.smtp_host)
    recipients = validate_recipients(body.allowed_recipients)
    agent_secrets.validate_name(body.password_secret_name)
    if not await agent_secrets.exists(session, agent_id, body.password_secret_name):
        raise _invalid(
            f"there is no secret named {body.password_secret_name!r} for this agent -- "
            "store the SMTP password there first (PUT /v1/agents/{id}/secrets/{name}); "
            "this table has no password column on purpose",
            "password_secret_name")

    now = datetime.now(UTC)
    row = await session.get(AgentEmailSender, agent_id)
    if row is None:
        row = AgentEmailSender(agent_id=agent_id, created_at=now)
        session.add(row)
    row.smtp_host = body.smtp_host
    row.smtp_port = body.smtp_port
    row.username = body.username
    row.from_address = body.from_address
    row.password_secret_name = body.password_secret_name
    row.allowed_recipients = recipients
    row.updated_at = now
    await session.commit()
    return row


async def delete_sender(session: AsyncSession, agent_id: uuid.UUID) -> None:
    """刪掉之後 §4.4 一律 `EGRESS_BLOCKED`（`SENDER_NOT_CONFIGURED`），
    **不是靜默不寄** —— agent 要知道信沒寄出去。"""
    row = await session.get(AgentEmailSender, agent_id)
    if row is not None:
        await session.delete(row)
        await session.commit()
