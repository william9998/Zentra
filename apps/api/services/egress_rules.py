"""對外出口白名單與寄件者設定（ZEN-019 / api-contract §2.8、§2.9）。

這兩張表回答同一個問題：**平台願意替這個 Agent 把東西送到哪裡去。**

白名單的價值全部來自「預設拒絕」（ADR-0014 E1）：一條規則都沒有，
一個外部主機都連不到。所以這裡的驗證是**故意嚴格**的 ——
`*`、scheme、port、路徑、大寫一律 `422`，
有一個逃生門就等於沒有白名單。
"""

import ipaddress
import re
import uuid
from datetime import UTC, datetime

import asyncpg
from asyncpg.exceptions import UniqueViolationError

from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError, not_found
from apps.api.ids import uuid7
from apps.api.rows import AgentEgressRule, AgentEmailSender
from apps.api.services import agent_secrets

#: 小寫主機名，可以沒有點（`localhost`、compose 的服務名）。IP 另外處理。
#: 刻意不接受結尾的點 —— `api.binance.com.` 與 `api.binance.com` 是同一台主機，
#: 但字串比對不同，會讓同一主機出現兩條規則。
_HOST_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST_PATTERN = re.compile(rf"^{_HOST_LABEL}(\.{_HOST_LABEL})*$")

ALLOWED_METHODS = ("GET", "POST")
MAX_RULES = 20
#: 平台自己會送這些，沙盒指名它們只會造成「我設的 header 去哪了」。
FORBIDDEN_AUTH_HEADERS = {"accept", "content-type", "host", "content-length"}
ALLOWED_SMTP_PORTS = (25, 465, 587)
MAX_RECIPIENTS = 10

_EMAIL_PATTERN = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


def _invalid(message: str, field: str) -> ZentraError:
    return ZentraError(ErrorCode.VALIDATION_ERROR, message, 422, details={"field": field})


#: 平台自己（compose 的服務名，加上 `app` 容器自己的 `localhost`）。代打從 `app`
#: 發出，白名單放了這些，容器就能借 `app` 的手碰到資料層或管理 API。
#:
#: ⚠️ **只擋名字**（gateway.md 硬規則第 5 點）：平台的內網 IP、
#: `host.docker.internal:<平台 port>` 仍然繞得過，這是使用者選的取捨。
PLATFORM_HOSTS = frozenset({"postgres", "redis", "registry", "app", "worker", "localhost"})


def _platform_host(host: str) -> ZentraError:
    return _invalid(
        f"host {host!r} is the platform itself -- the proxy runs inside the app "
        "container, so this would let the agent reach the platform's own services. "
        "To reach a service on the Docker host, use host.docker.internal", "host")


def normalize_ip(host: str) -> str | None:
    """IP 字面值 → 標準寫法；不是 IP 回 `None`。

    規則存標準寫法、代打時也用標準寫法比對，`0:0::1` 和 `::1` 才會命中同一條。
    """
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


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
    ip = normalize_ip(host)
    if ip is not None:
        if ipaddress.ip_address(ip).is_loopback:
            raise _platform_host(host)
        return ip
    if host.lower() in PLATFORM_HOSTS:
        raise _platform_host(host)
    if ":" in host:
        raise _invalid("host must not include a port -- a rule allows every port "
                       "on its host", "host")
    if host != host.lower():
        raise _invalid("host must be lowercase", "host")
    if len(host) > 253 or not _HOST_PATTERN.match(host):
        raise _invalid(f"host {host!r} is not a valid host name or IP", "host")
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
    conn: asyncpg.Connection, agent_id: uuid.UUID,
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
    if not await agent_secrets.exists(conn, agent_id, auth_secret_name):
        raise _invalid(
            f"there is no secret named {auth_secret_name!r} for this agent -- "
            "store it first (PUT /v1/agents/{id}/secrets/{name})", "auth_secret_name")


async def list_rules(conn: asyncpg.Connection, agent_id: uuid.UUID) -> list[AgentEgressRule]:
    rows = await conn.fetch(
        f"SELECT {AgentEgressRule.returning()} FROM agent_egress_rules "
        "WHERE agent_id = $1 ORDER BY host",
        agent_id,
    )
    return [AgentEgressRule.from_row(row) for row in rows]


async def load_rule(
    conn: asyncpg.Connection, agent_id: uuid.UUID, rule_id: str,
) -> AgentEgressRule:
    try:
        parsed = uuid.UUID(rule_id)
    except ValueError:
        raise not_found("egress rule") from None
    rec = await conn.fetchrow(
        f"SELECT {AgentEgressRule.returning()} FROM agent_egress_rules "
        "WHERE rule_id = $1 AND agent_id = $2",
        parsed, agent_id,
    )
    if rec is None:
        raise not_found("egress rule")
    return AgentEgressRule.from_row(rec)


def _host_conflict(host: str) -> ZentraError:
    return ZentraError(
        ErrorCode.VERSION_CONFLICT,
        f"this agent already has a rule for {host}: which rule applies has to be "
        "obvious at a glance, so hosts are unique per agent",
        409, details={"field": "host"},
    )


async def create_rule(
    conn: asyncpg.Connection, agent_id: uuid.UUID, body,
) -> AgentEgressRule:
    host = validate_host(body.host)
    methods = validate_methods(body.methods)
    path_prefix = validate_path_prefix(body.path_prefix)
    await _validate_auth(conn, agent_id, body.auth_secret_name, body.auth_header)

    existing = await list_rules(conn, agent_id)
    if any(rule.host == host for rule in existing):
        raise _host_conflict(host)
    if len(existing) >= MAX_RULES:
        raise _invalid(
            f"this agent already has {len(existing)} rules, the limit is {MAX_RULES}",
            "host")

    now = datetime.now(UTC)
    try:
        rec = await conn.fetchrow(
            f"""
            INSERT INTO agent_egress_rules
                (rule_id, agent_id, host, methods, path_prefix,
                 auth_secret_name, auth_header, description, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)
            RETURNING {AgentEgressRule.returning()}
            """,
            uuid7(), agent_id, host, methods, path_prefix,
            body.auth_secret_name, body.auth_header, body.description, now,
        )
    except UniqueViolationError as exc:
        raise _host_conflict(host) from exc
    return AgentEgressRule.from_row(rec)


async def update_rule(
    conn: asyncpg.Connection, agent_id: uuid.UUID, row: AgentEgressRule, body,
) -> AgentEgressRule:
    """整條取代（`PUT`）。**改規則立刻生效**，包括對已經在跑的 Run 的下一次呼叫。"""
    host = validate_host(body.host)
    methods = validate_methods(body.methods)
    path_prefix = validate_path_prefix(body.path_prefix)
    await _validate_auth(conn, agent_id, body.auth_secret_name, body.auth_header)

    clash_id = await conn.fetchval(
        "SELECT rule_id FROM agent_egress_rules "
        "WHERE agent_id = $1 AND host = $2 AND rule_id <> $3",
        agent_id, host, row.rule_id,
    )
    if clash_id is not None:
        raise ZentraError(
            ErrorCode.VERSION_CONFLICT,
            f"this agent already has another rule for {host}", 409,
            details={"field": "host"},
        )

    now = datetime.now(UTC)
    try:
        rec = await conn.fetchrow(
            f"""
            UPDATE agent_egress_rules SET
                host = $2, methods = $3, path_prefix = $4,
                auth_secret_name = $5, auth_header = $6, description = $7,
                updated_at = $8
            WHERE rule_id = $1
            RETURNING {AgentEgressRule.returning()}
            """,
            row.rule_id, host, methods, path_prefix,
            body.auth_secret_name, body.auth_header, body.description, now,
        )
    except UniqueViolationError as exc:
        raise ZentraError(
            ErrorCode.VERSION_CONFLICT,
            f"this agent already has another rule for {host}", 409,
            details={"field": "host"},
        ) from exc
    return AgentEgressRule.from_row(rec)


async def delete_rule(conn: asyncpg.Connection, row: AgentEgressRule) -> None:
    """刪規則**不會**抹掉它的稽核紀錄 —— `egress_calls.rule_id` 是 SET NULL。"""
    await conn.execute(
        "DELETE FROM agent_egress_rules WHERE rule_id = $1", row.rule_id)


async def match_rule(
    conn: asyncpg.Connection, agent_id: uuid.UUID, host: str,
) -> AgentEgressRule | None:
    """host 精確比對。**沒有後綴比對、沒有萬用字元**（E1）。"""
    rec = await conn.fetchrow(
        f"SELECT {AgentEgressRule.returning()} FROM agent_egress_rules "
        "WHERE agent_id = $1 AND host = $2",
        agent_id, host,
    )
    return AgentEgressRule.from_row(rec) if rec is not None else None


async def has_rules(conn: asyncpg.Connection, agent_id: uuid.UUID) -> bool:
    value = await conn.fetchval(
        "SELECT 1 FROM agent_egress_rules WHERE agent_id = $1 LIMIT 1", agent_id)
    return value is not None


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


async def get_sender(conn: asyncpg.Connection, agent_id: uuid.UUID) -> AgentEmailSender | None:
    rec = await conn.fetchrow(
        f"SELECT {AgentEmailSender.returning()} FROM agent_email_sender WHERE agent_id = $1",
        agent_id,
    )
    return AgentEmailSender.from_row(rec) if rec is not None else None


async def set_sender(
    conn: asyncpg.Connection, agent_id: uuid.UUID, body,
) -> AgentEmailSender:
    if body.smtp_port not in ALLOWED_SMTP_PORTS:
        raise _invalid(
            f"smtp_port must be one of {list(ALLOWED_SMTP_PORTS)} "
            "(587 STARTTLS, 465 implicit TLS; plaintext SMTP is never used)", "smtp_port")
    validate_host(body.smtp_host)
    recipients = validate_recipients(body.allowed_recipients)
    agent_secrets.validate_name(body.password_secret_name)
    if not await agent_secrets.exists(conn, agent_id, body.password_secret_name):
        raise _invalid(
            f"there is no secret named {body.password_secret_name!r} for this agent -- "
            "store the SMTP password there first (PUT /v1/agents/{id}/secrets/{name}); "
            "this table has no password column on purpose",
            "password_secret_name")

    now = datetime.now(UTC)
    rec = await conn.fetchrow(
        f"""
        INSERT INTO agent_email_sender
            (agent_id, smtp_host, smtp_port, username, from_address,
             password_secret_name, allowed_recipients, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
        ON CONFLICT (agent_id) DO UPDATE SET
            smtp_host = EXCLUDED.smtp_host,
            smtp_port = EXCLUDED.smtp_port,
            username = EXCLUDED.username,
            from_address = EXCLUDED.from_address,
            password_secret_name = EXCLUDED.password_secret_name,
            allowed_recipients = EXCLUDED.allowed_recipients,
            updated_at = EXCLUDED.updated_at
        RETURNING {AgentEmailSender.returning()}
        """,
        agent_id, body.smtp_host, body.smtp_port, body.username, body.from_address,
        body.password_secret_name, recipients, now,
    )
    return AgentEmailSender.from_row(rec)


async def delete_sender(conn: asyncpg.Connection, agent_id: uuid.UUID) -> None:
    """刪掉之後 §4.4 一律 `EGRESS_BLOCKED`（`SENDER_NOT_CONFIGURED`），
    **不是靜默不寄** —— agent 要知道信沒寄出去。"""
    await conn.execute(
        "DELETE FROM agent_email_sender WHERE agent_id = $1", agent_id)
