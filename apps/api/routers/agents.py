"""Agent metadata, OCI versions, and generated Agent OpenAPI."""

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Query, Request, Response

from apps.api import db
from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError
from apps.api.schemas import (
    AgentConfigRequest,
    AgentConfigResponse,
    AgentDetail,
    AgentListResponse,
    AgentSummary,
    AgentVersionListResponse,
    AgentVersionResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    AgentContainerListResponse,
    AgentContainerResponse,
    AgentApiKeyListResponse,
    AgentApiKeyResponse,
    AgentUsageResponse,
    AgentLlmCredentialResponse,
    AgentSecretListResponse,
    AgentSecretResponse,
    EgressCallListResponse,
    EgressRuleListResponse,
    EgressRuleRequest,
    EgressRuleResponse,
    EmailSenderRequest,
    EmailSenderResponse,
    IssueAgentApiKeyRequest,
    IssuedAgentApiKeyResponse,
    LlmCallListResponse,
    SetAgentSecretRequest,
    CreateAgentVersionRequest,
    StartAgentContainerRequest,
    SetAgentLlmCredentialRequest,
    UpdateAgentRequest,
)
from apps.api.config import get_settings
from apps.api.services import agent_api_keys
from apps.api.services import agent_credentials
from apps.api.services import agent_secrets as secrets_service
from apps.api.services import agent_settings as settings_service
from apps.api.services import agents as service
from apps.api.services import egress_audit
from apps.api.services import egress_rules as egress_rules_service
from apps.api.services import containers as container_service
from apps.api.services import usage as usage_service
from apps.api.tasks import (
    dispatch_agent_purge,
    dispatch_build, dispatch_container_restart,
    dispatch_container_start, dispatch_container_stop,
)

router = APIRouter(prefix="/v1/agents", tags=["agents"])


@router.post("", status_code=201, response_model=CreateAgentResponse)
async def create_agent(body: CreateAgentRequest) -> CreateAgentResponse:
    async with db.transaction() as conn:
        row = await service.create_agent(conn, body)
    return CreateAgentResponse(
        agent_id=str(row.id), name=row.name, description=row.description,
        latest_published_version=None, created_at=row.created_at,
    )


@router.get("", response_model=AgentListResponse, response_model_exclude_none=True)
async def list_agents(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> AgentListResponse:
    async with db.connection() as conn:
        rows, next_cursor = await service.list_agents(conn, limit=limit, cursor=cursor)
    return AgentListResponse(
        items=[service.agent_summary(row, latest) for row, latest in rows],
        next_cursor=next_cursor,
    )


@router.get("/{agent_id}", response_model=AgentDetail, response_model_exclude_none=True)
async def get_agent(agent_id: str, request: Request) -> AgentDetail:
    async with db.connection() as conn:
        row = await service.load_agent(conn, agent_id)
        latest = await service.latest_published(conn, row.id)
    return service.agent_detail(row, latest.semver if latest else None, str(request.base_url))


@router.patch("/{agent_id}", response_model=AgentDetail, response_model_exclude_none=True)
async def patch_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    request: Request,
) -> AgentDetail:
    async with db.transaction() as conn:
        row = await service.update_agent(conn, await service.load_agent(conn, agent_id), body)
        latest = await service.latest_published(conn, row.id)
    return service.agent_detail(row, latest.semver if latest else None, str(request.base_url))


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> Response:
    """**硬刪**這個 Agent 與它的一切（ADR-0013）。不可復原。

    DB 的部分（版本、容器紀錄、Run、事件、LLM 呼叫、憑證、API key）在這裡
    同步刪完，所以回 `204` 時它真的不見了。

    容器與 image 交給 worker 非同步收：那兩件事要碰 docker.sock 與 registry，
    而 `app` 兩個都不該碰。**清理失敗不會讓刪除失敗** —— Agent 已經不在了，
    再把錯誤丟回來只會讓使用者以為刪除沒成功，然後再按一次。
    """
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        payload = await service.delete_agent(conn, agent)
    await asyncio.to_thread(dispatch_agent_purge, payload)
    return Response(status_code=204)


# ── 常駐容器（ZEN-015）────────────────────────────────────────────────
#
# **啟動是明確動作，不是註冊的副作用。** 註冊是「這個版本存在」，
# 啟動是「現在開始佔資源」——綁在一起會讓「發十個版本」變成「開十個容器」。


def _container_view(row, semver: str) -> AgentContainerResponse:
    return AgentContainerResponse(
        container_id=str(row.id),
        agent_id=str(row.agent_id),
        agent_version=semver,
        status=row.status,
        inflight_runs=row.inflight_runs,
        max_concurrent_runs=get_settings().sandbox_runs_per_container,
        restart_count=row.restart_count,
        started_at=row.started_at,
        last_health_at=row.last_health_at,
        error=row.error,
    )


@router.get("/{agent_id}/containers", response_model=AgentContainerListResponse)
async def list_agent_containers(agent_id: str) -> AgentContainerListResponse:
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        rows = await container_service.list_containers(conn, agent.id)
        items = [
            _container_view(row, await container_service.semver_of(conn, row.agent_version_id))
            for row in rows
        ]
    return AgentContainerListResponse(items=items)


@router.post("/{agent_id}/containers", status_code=202, response_model=AgentContainerResponse)
async def start_agent_container(
    agent_id: str,
    body: StartAgentContainerRequest | None = None,
) -> AgentContainerResponse:
    """啟動這個 Agent 的常駐容器。

    回 `202`：這裡只登記 `STARTING`，**實際建容器由 worker 做** ——
    api 不掛 docker.sock，而且 image pull 可能很慢，不該卡住請求。
    """
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        version = None
        if body and body.version:
            version = await service.load_version_by_semver(conn, agent.id, body.version)
        row = await container_service.request_start(conn, agent.id, version)
        semver = await container_service.semver_of(conn, row.agent_version_id)
    dispatch_container_start(str(row.id))
    return _container_view(row, semver)


@router.post("/{agent_id}/containers/{container_uuid}/restart",
             status_code=202, response_model=AgentContainerResponse)
async def restart_agent_container(
    agent_id: str,
    container_uuid: str,
) -> AgentContainerResponse:
    """重啟。容器掛掉時使用者唯一能做的事，所以它必須存在且好按。"""
    async with db.transaction() as conn:
        await service.load_agent(conn, agent_id)
        row = await container_service.load_container(conn, container_uuid)
        row = await container_service.request_restart(conn, row)
        semver = await container_service.semver_of(conn, row.agent_version_id)
    dispatch_container_restart(str(row.id))
    return _container_view(row, semver)


@router.delete("/{agent_id}/containers/{container_uuid}", status_code=202,
               response_model=AgentContainerResponse)
async def stop_agent_container(
    agent_id: str,
    container_uuid: str,
) -> AgentContainerResponse:
    async with db.transaction() as conn:
        await service.load_agent(conn, agent_id)
        row = await container_service.load_container(conn, container_uuid)
        row = await container_service.request_stop(conn, row)
        semver = await container_service.semver_of(conn, row.agent_version_id)
    dispatch_container_stop(str(row.id))
    return _container_view(row, semver)


# ── LLM 憑證（ZEN-012）────────────────────────────────────────────────
#
# 憑證綁 Agent 不綁版本：發新版不用重填，改金鑰則所有版本一起變。
# 三條路由裡**沒有任何一條讀得出明文** —— 那不是靠紀律，是靠沒有那個功能。


@router.put("/{agent_id}/llm-credential", response_model=AgentLlmCredentialResponse)
async def set_llm_credential(
    agent_id: str,
    body: SetAgentLlmCredentialRequest,
) -> AgentLlmCredentialResponse:
    """註冊或覆寫這個 Agent 的 LLM 憑證。

    **覆寫立刻對所有版本生效。** 這是刻意的：版本不可變（P5）指的是程式碼與 image，
    憑證是維運設定。代價見 [ADR-0011]。
    """
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        meta = await agent_credentials.set_credential(
            conn, agent.id,
            api_key=body.api_key, base_url=body.base_url, default_model=body.default_model,
        )
    return AgentLlmCredentialResponse(**meta.__dict__)


@router.get("/{agent_id}/llm-credential", response_model=AgentLlmCredentialResponse)
async def get_llm_credential(
    agent_id: str,
) -> AgentLlmCredentialResponse:
    """回這個 Agent 有沒有設憑證。**不回金鑰**，只有尾四碼。"""
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        meta = await agent_credentials.describe(conn, agent.id)
    return AgentLlmCredentialResponse(**meta.__dict__)


@router.delete("/{agent_id}/llm-credential", status_code=204)
async def delete_llm_credential(
    agent_id: str,
) -> Response:
    """移除憑證。之後這個 Agent 退回用平台預設金鑰。"""
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        await agent_credentials.delete_credential(conn, agent.id)
    return Response(status_code=204)


# ── 可變設定・機密・對外能力（ZEN-019 / ADR-0014）─────────────────────
#
# 三層都**綁 Agent 不綁版本**，改一次對所有版本生效（同 ADR-0011 的取捨）。
# 三層的界線是「值會不會被看到」：
#
#   設定（§2.6）  明文，回得出來，**完整送進沙盒**
#   機密（§2.7）  Fernet 密文，讀不出來，**沙盒只拿得到名字**
#   能力（§2.8/§2.9）  平台願意替它送到哪裡去；認證由平台注入


@router.get("/{agent_id}/config", response_model=AgentConfigResponse)
async def get_agent_config(
    agent_id: str,
) -> AgentConfigResponse:
    """這個 Agent 的可變設定。未設定過時 `config: {}`、`updated_at: null`。

    **回的是明文** —— 設定不是機密（§2.6 第 1 點）。平台不掃描內容、
    不猜哪個值像 secret：一個猜得不準的警告比沒有警告更糟。
    """
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        view = await settings_service.describe(conn, agent.id)
    return AgentConfigResponse(config=view.config, updated_at=view.updated_at)


@router.put("/{agent_id}/config", response_model=AgentConfigResponse)
async def put_agent_config(
    agent_id: str,
    body: AgentConfigRequest,
) -> AgentConfigResponse:
    """**整份取代**，沒有 `PATCH`（§2.6 第 2 點）。

    要改一個鍵就 `GET` → 改 → `PUT`；要刪一個鍵就 `PUT` 一份不含它的文件。
    更新**立刻對所有版本、所有進行中的容器生效**，下一筆 Run 就是新值；
    已經在跑的 Run 不受影響 —— 它的 `ctx.config` 在派工那一刻就定稿了。
    """
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        view = await settings_service.replace(conn, agent.id, body.config)
    return AgentConfigResponse(config=view.config, updated_at=view.updated_at)


def _secret_view(meta) -> AgentSecretResponse:
    return AgentSecretResponse(
        name=meta.name, last_four=meta.last_four,
        created_at=meta.created_at, updated_at=meta.updated_at,
    )


@router.get("/{agent_id}/secrets", response_model=AgentSecretListResponse)
async def list_agent_secrets(
    agent_id: str,
) -> AgentSecretListResponse:
    """機密的 metadata。**沒有任何端點回傳明文或密文** —— 這是契約禁止，
    不是「目前沒做」。`last_four` 是唯一看得到的線索。"""
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        rows = await secrets_service.list_secrets(conn, agent.id)
    return AgentSecretListResponse(items=[_secret_view(row) for row in rows])


@router.put("/{agent_id}/secrets/{name}", response_model=AgentSecretResponse)
async def put_agent_secret(
    agent_id: str,
    name: str,
    body: SetAgentSecretRequest,
) -> AgentSecretResponse:
    """存一個機密。重複 `PUT` 同名是覆寫，`created_at` 保留。

    **值不會進沙盒**：沙盒只拿得到名字清單（`secret_names`），用法是在出口
    規則或寄件者設定裡**以名字引用**它，平台在代打時才解密注入。

    平台未設定 `ZENTRA_SECRET_KEY` 時回 `422`，**不接受機密**，
    而不是退回明文儲存。
    """
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        meta = await secrets_service.set_secret(conn, agent.id, name, body.value)
    return _secret_view(meta)


@router.delete("/{agent_id}/secrets/{name}", status_code=204)
async def delete_agent_secret(
    agent_id: str,
    name: str,
) -> Response:
    """硬刪，重複刪除冪等（不存在也回 204）。

    **還被引用時回 409**，`details.referenced_by` 列出引用者 ——
    直接刪掉會讓那條能力在下一次 Run 才無聲失敗。
    """
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        await secrets_service.delete_secret(conn, agent.id, name)
    return Response(status_code=204)


def _rule_view(row) -> EgressRuleResponse:
    return EgressRuleResponse(
        rule_id=str(row.rule_id), agent_id=str(row.agent_id), host=row.host,
        methods=list(row.methods or []), path_prefix=row.path_prefix,
        auth_secret_name=row.auth_secret_name, auth_header=row.auth_header,
        description=row.description,
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.get("/{agent_id}/egress-rules", response_model=EgressRuleListResponse)
async def list_egress_rules(
    agent_id: str,
) -> EgressRuleListResponse:
    """這個 Agent 能連到哪些主機。**空清單表示一個都連不到**（預設拒絕）。"""
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        rows = await egress_rules_service.list_rules(conn, agent.id)
    return EgressRuleListResponse(items=[_rule_view(row) for row in rows])


@router.post("/{agent_id}/egress-rules", status_code=201,
             response_model=EgressRuleResponse)
async def create_egress_rule(
    agent_id: str,
    body: EgressRuleRequest,
) -> EgressRuleResponse:
    """加一條白名單。`host` 在同一個 Agent 內唯一，撞名回 `409`。

    **改規則立刻生效**，包括對已經在跑的 Run 的下一次呼叫。
    """
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        row = await egress_rules_service.create_rule(conn, agent.id, body)
    return _rule_view(row)


@router.put("/{agent_id}/egress-rules/{rule_id}", response_model=EgressRuleResponse)
async def update_egress_rule(
    agent_id: str,
    rule_id: str,
    body: EgressRuleRequest,
) -> EgressRuleResponse:
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        row = await egress_rules_service.load_rule(conn, agent.id, rule_id)
        row = await egress_rules_service.update_rule(conn, agent.id, row, body)
    return _rule_view(row)


@router.delete("/{agent_id}/egress-rules/{rule_id}", status_code=204)
async def delete_egress_rule(
    agent_id: str,
    rule_id: str,
) -> Response:
    """刪一條白名單。**它過去的稽核紀錄留著**（`egress_calls.rule_id` 是 SET NULL）——
    那正是「我改了什麼、然後發生了什麼」最需要對照的時候。"""
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        row = await egress_rules_service.load_rule(conn, agent.id, rule_id)
        await egress_rules_service.delete_rule(conn, row)
    return Response(status_code=204)


def _sender_view(row) -> EmailSenderResponse:
    if row is None:
        return EmailSenderResponse(configured=False)
    return EmailSenderResponse(
        configured=True, smtp_host=row.smtp_host, smtp_port=row.smtp_port,
        username=row.username, from_address=row.from_address,
        password_secret_name=row.password_secret_name,
        allowed_recipients=list(row.allowed_recipients or []),
        updated_at=row.updated_at,
    )


@router.get("/{agent_id}/email-sender", response_model=EmailSenderResponse)
async def get_email_sender(
    agent_id: str,
) -> EmailSenderResponse:
    """未設定時 `configured: false`，其餘欄位 `null`。"""
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        sender = await egress_rules_service.get_sender(conn, agent.id)
    return _sender_view(sender)


@router.put("/{agent_id}/email-sender", response_model=EmailSenderResponse)
async def put_email_sender(
    agent_id: str,
    body: EmailSenderRequest,
) -> EmailSenderResponse:
    """設定寄件者。**這裡沒有密碼欄位** —— 密碼只能以 `password_secret_name`
    指名一個 §2.7 的機密。

    `allowed_recipients` 必填非空：少了它，一支迴圈寫錯的 agent 會用使用者的
    Gmail 帳號變成垃圾信來源，而 Google 封的是**那個帳號**。
    """
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        sender = await egress_rules_service.set_sender(conn, agent.id, body)
    return _sender_view(sender)


@router.delete("/{agent_id}/email-sender", status_code=204)
async def delete_email_sender(
    agent_id: str,
) -> Response:
    """移除寄件者。之後寄信一律 `EGRESS_BLOCKED`（`SENDER_NOT_CONFIGURED`），
    **不是靜默不寄** —— agent 要知道信沒寄出去。"""
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        await egress_rules_service.delete_sender(conn, agent.id)
    return Response(status_code=204)


@router.get("/{agent_id}/egress-calls", response_model=EgressCallListResponse)
async def list_egress_calls(
    agent_id: str,
    kind: str | None = None,
    run_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    host: str | None = None,
    status_code: int | None = None,
    errors_only: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> EgressCallListResponse:
    """這個 Agent 實際往外送了什麼（§2.10）。

    **HTTP 與寄信在同一張表**，用 `kind` 分。**被擋下的呼叫也在裡面** ——
    只記成功的話，「為什麼我的 agent 連不出去」在畫面上完全沒有線索。
    """
    if kind is not None and kind not in ("HTTP", "EMAIL"):
        raise ZentraError(ErrorCode.VALIDATION_ERROR, "kind must be HTTP or EMAIL", 422,
                          details={"field": "kind"})
    run_uuid = None
    if run_id:
        try:
            run_uuid = uuid.UUID(run_id)
        except ValueError as exc:
            raise ZentraError(ErrorCode.VALIDATION_ERROR, "run_id is invalid", 422,
                              details={"field": "run_id"}) from exc
    filters = egress_audit.CallFilters(
        kind=kind, run_id=run_uuid, since=since, until=until, host=host,
        status_code=status_code, errors_only=errors_only,
    )
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        items, next_cursor = await egress_audit.list_calls(
            conn, agent.id, filters, limit=limit, cursor=cursor)
    return EgressCallListResponse(items=items, next_cursor=next_cursor)


# ── 下游 API key（ZEN-015）──────────────────────────────────────────────
# 明文只在 POST 的回應裡出現一次。DB 存的是 SHA-256，所以**沒有任何端點讀得回來** ——
# 這同樣不是靠紀律，是靠沒有那個功能。


def _key_view(row) -> AgentApiKeyResponse:
    return AgentApiKeyResponse(
        key_id=str(row.id), agent_id=str(row.agent_id), name=row.name,
        key_prefix=row.key_prefix, created_at=row.created_at,
        last_used_at=row.last_used_at, revoked_at=row.revoked_at,
    )


@router.post("/{agent_id}/api-keys", status_code=201,
             response_model=IssuedAgentApiKeyResponse)
async def issue_agent_api_key(
    agent_id: str,
    body: IssueAgentApiKeyRequest,
) -> IssuedAgentApiKeyResponse:
    """發一把 key 給下游應用。**`key` 只在這次回應出現，之後讀不回來。**"""
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        issued = await agent_api_keys.issue(conn, agent.id, body.name)
    return IssuedAgentApiKeyResponse(
        **_key_view(issued.row).model_dump(), key=issued.plaintext,
    )


@router.get("/{agent_id}/api-keys", response_model=AgentApiKeyListResponse)
async def list_agent_api_keys(
    agent_id: str,
) -> AgentApiKeyListResponse:
    """列出這個 Agent 的所有 key，**含已撤銷的**。

    已撤銷的留在列表裡是因為歷史 Run 還掛在它們身上；藏起來的話，
    用量查詢會出現一把查不到名字的 key。
    """
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        rows = await agent_api_keys.list_keys(conn, agent.id)
    return AgentApiKeyListResponse(items=[_key_view(row) for row in rows])


@router.delete("/{agent_id}/api-keys/{key_id}", response_model=AgentApiKeyResponse)
async def revoke_agent_api_key(
    agent_id: str,
    key_id: str,
) -> AgentApiKeyResponse:
    """撤銷一把 key。**軟撤銷**，所以回 200 帶 `revoked_at`，不是 204。

    撤銷後立刻失效，但那把 key 的列仍在，過去的 Run 也還查得到是誰打的。
    重複撤銷是冪等的。
    """
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        row = await agent_api_keys.load_key(conn, agent.id, key_id)
        row = await agent_api_keys.revoke(conn, row)
    return _key_view(row)


# ── 用量與除錯查詢（ZEN-015 §3.3）──────────────────────────────────────
# 歸戶靠 `runs.api_key_id` join `llm_calls`。沒有 key 的 Run（Console 發起）
# 在報表裡是 `key_id: null` 的一列，**不是被濾掉** —— 沒歸戶的用量通常正是要找的。


def _filters(
    api_key_id: str | None, since: datetime | None, until: datetime | None,
    model: str | None, credential_source: str | None, status_code: int | None,
    errors_only: bool,
) -> usage_service.CallFilters:
    key_uuid = None
    if api_key_id:
        try:
            key_uuid = uuid.UUID(api_key_id)
        except ValueError as exc:
            raise ZentraError(
                ErrorCode.VALIDATION_ERROR, "api_key_id is invalid", 422,
                details={"field": "api_key_id"},
            ) from exc
    return usage_service.CallFilters(
        api_key_id=key_uuid, since=since, until=until, model=model,
        credential_source=credential_source, status_code=status_code,
        errors_only=errors_only,
    )


@router.get("/{agent_id}/usage", response_model=AgentUsageResponse)
async def agent_usage(
    agent_id: str,
    api_key_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    model: str | None = None,
    credential_source: str | None = None,
) -> AgentUsageResponse:
    """這個 Agent 的 LLM 用量：總計、依 key、依 model。

    回答的是「這把 key 這個月花了多少、花在哪個模型上」（spec §3.3 的第一條查詢）。
    """
    filters = _filters(api_key_id, since, until, model, credential_source, None, False)
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        payload = await usage_service.aggregate(conn, agent.id, filters)
    return AgentUsageResponse(**payload)


@router.get("/{agent_id}/llm-calls", response_model=LlmCallListResponse)
async def agent_llm_calls(
    agent_id: str,
    api_key_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    model: str | None = None,
    credential_source: str | None = None,
    status_code: int | None = None,
    errors_only: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> LlmCallListResponse:
    """逐筆列出 LLM 呼叫，最新的在前。

    回答的是「這把 key 的哪一筆 request 出錯了」（spec §3.3 的第二條查詢）。
    `errors_only=true` 是除錯的第一個動作，所以給它一個開關，
    不要求呼叫端自己組 `status_code >= 400 OR error IS NOT NULL`。
    """
    filters = _filters(
        api_key_id, since, until, model, credential_source, status_code, errors_only)
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        items, next_cursor = await usage_service.list_calls(
            conn, agent.id, filters, limit=limit, cursor=cursor)
    return LlmCallListResponse(items=items, next_cursor=next_cursor)


@router.post("/{agent_id}/versions", status_code=202, response_model=AgentVersionResponse,
             response_model_exclude_none=False)
async def create_version(
    agent_id: str,
    body: CreateAgentVersionRequest,
    response: Response,
) -> AgentVersionResponse:
    async with db.transaction() as conn:
        agent = await service.load_agent(conn, agent_id)
        row, needs_build = await service.create_version(conn, agent, body)
    if needs_build:
        await asyncio.to_thread(dispatch_build, str(row.id))
    response.headers["Location"] = f"/v1/agents/{agent_id}/versions/{row.semver}"
    return service.version_response(row)


@router.get("/{agent_id}/versions", response_model=AgentVersionListResponse)
async def list_versions(
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> AgentVersionListResponse:
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        rows, next_cursor = await service.list_versions(
            conn, agent.id, limit=limit, cursor=cursor
        )
    return AgentVersionListResponse(
        items=[service.version_response(row, detail=True) for row in rows],
        next_cursor=next_cursor,
    )


@router.get("/{agent_id}/versions/{semver}", response_model=AgentVersionResponse)
async def get_version(
    agent_id: str,
    semver: str,
) -> AgentVersionResponse:
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        row = await service.load_version_by_semver(conn, agent.id, semver)
    return service.version_response(row, detail=True)


@router.get("/{agent_id}/openapi.json")
async def agent_openapi(
    agent_id: str,
    request: Request,
) -> dict:
    async with db.connection() as conn:
        agent = await service.load_agent(conn, agent_id)
        version = await service.resolve_published_version(conn, agent.id, None)
    return service.generate_agent_openapi(agent, version, str(request.base_url).rstrip("/"))
