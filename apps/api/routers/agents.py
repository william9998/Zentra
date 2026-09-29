"""Agent metadata, OCI versions, and generated Agent OpenAPI."""

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import get_session
from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError
from apps.api.schemas import (
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
    IssueAgentApiKeyRequest,
    IssuedAgentApiKeyResponse,
    LlmCallListResponse,
    CreateAgentVersionRequest,
    StartAgentContainerRequest,
    SetAgentLlmCredentialRequest,
    UpdateAgentRequest,
)
from apps.api.config import get_settings
from apps.api.services import agent_api_keys
from apps.api.services import agent_credentials
from apps.api.services import agents as service
from apps.api.services import containers as container_service
from apps.api.services import usage as usage_service
from apps.api.tasks import (
    dispatch_agent_purge,
    dispatch_build, dispatch_container_restart,
    dispatch_container_start, dispatch_container_stop,
)

router = APIRouter(prefix="/v1/agents", tags=["agents"])


@router.post("", status_code=201, response_model=CreateAgentResponse)
async def create_agent(
    body: CreateAgentRequest,
    session: AsyncSession = Depends(get_session),
) -> CreateAgentResponse:
    row = await service.create_agent(session, body)
    return CreateAgentResponse(
        agent_id=str(row.id), name=row.name, description=row.description,
        latest_published_version=None, created_at=row.created_at,
    )


@router.get("", response_model=AgentListResponse, response_model_exclude_none=True)
async def list_agents(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> AgentListResponse:
    rows, next_cursor = await service.list_agents(session, limit=limit, cursor=cursor)
    return AgentListResponse(
        items=[service.agent_summary(row, latest) for row, latest in rows],
        next_cursor=next_cursor,
    )


@router.get("/{agent_id}", response_model=AgentDetail, response_model_exclude_none=True)
async def get_agent(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentDetail:
    row = await service.load_agent(session, agent_id)
    latest = await service.latest_published(session, row.id)
    return service.agent_detail(row, latest.semver if latest else None, str(request.base_url))


@router.patch("/{agent_id}", response_model=AgentDetail, response_model_exclude_none=True)
async def patch_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentDetail:
    row = await service.update_agent(session, await service.load_agent(session, agent_id), body)
    latest = await service.latest_published(session, row.id)
    return service.agent_detail(row, latest.semver if latest else None, str(request.base_url))


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """**硬刪**這個 Agent 與它的一切（ADR-0013）。不可復原。

    DB 的部分（版本、容器紀錄、Run、事件、LLM 呼叫、憑證、API key）在這裡
    同步刪完，所以回 `204` 時它真的不見了。

    容器與 image 交給 worker 非同步收：那兩件事要碰 docker.sock 與 registry，
    而 `app` 兩個都不該碰。**清理失敗不會讓刪除失敗** —— Agent 已經不在了，
    再把錯誤丟回來只會讓使用者以為刪除沒成功，然後再按一次。
    """
    agent = await service.load_agent(session, agent_id)
    payload = await service.delete_agent(session, agent)
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


async def _semver_of(session: AsyncSession, row) -> str:
    from apps.api.models import AgentVersion
    version = await session.get(AgentVersion, row.agent_version_id)
    return version.semver if version else "?"


@router.get("/{agent_id}/containers", response_model=AgentContainerListResponse)
async def list_agent_containers(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> AgentContainerListResponse:
    agent = await service.load_agent(session, agent_id)
    rows = await container_service.list_containers(session, agent.id)
    return AgentContainerListResponse(items=[
        _container_view(row, await _semver_of(session, row)) for row in rows
    ])


@router.post("/{agent_id}/containers", status_code=202, response_model=AgentContainerResponse)
async def start_agent_container(
    agent_id: str,
    body: StartAgentContainerRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> AgentContainerResponse:
    """啟動這個 Agent 的常駐容器。

    回 `202`：這裡只登記 `STARTING`，**實際建容器由 worker 做** ——
    api 不掛 docker.sock，而且 image pull 可能很慢，不該卡住請求。
    """
    agent = await service.load_agent(session, agent_id)
    version = None
    if body and body.version:
        version = await service.load_version_by_semver(session, agent.id, body.version)
    row = await container_service.request_start(session, agent.id, version)
    dispatch_container_start(str(row.id))
    return _container_view(row, await _semver_of(session, row))


@router.post("/{agent_id}/containers/{container_uuid}/restart",
             status_code=202, response_model=AgentContainerResponse)
async def restart_agent_container(
    agent_id: str,
    container_uuid: str,
    session: AsyncSession = Depends(get_session),
) -> AgentContainerResponse:
    """重啟。容器掛掉時使用者唯一能做的事，所以它必須存在且好按。"""
    await service.load_agent(session, agent_id)
    row = await container_service.load_container(session, container_uuid)
    row = await container_service.request_restart(session, row)
    dispatch_container_restart(str(row.id))
    return _container_view(row, await _semver_of(session, row))


@router.delete("/{agent_id}/containers/{container_uuid}", status_code=202,
               response_model=AgentContainerResponse)
async def stop_agent_container(
    agent_id: str,
    container_uuid: str,
    session: AsyncSession = Depends(get_session),
) -> AgentContainerResponse:
    await service.load_agent(session, agent_id)
    row = await container_service.load_container(session, container_uuid)
    row = await container_service.request_stop(session, row)
    dispatch_container_stop(str(row.id))
    return _container_view(row, await _semver_of(session, row))


# ── LLM 憑證（ZEN-012）────────────────────────────────────────────────
#
# 憑證綁 Agent 不綁版本：發新版不用重填，改金鑰則所有版本一起變。
# 三條路由裡**沒有任何一條讀得出明文** —— 那不是靠紀律，是靠沒有那個功能。


@router.put("/{agent_id}/llm-credential", response_model=AgentLlmCredentialResponse)
async def set_llm_credential(
    agent_id: str,
    body: SetAgentLlmCredentialRequest,
    session: AsyncSession = Depends(get_session),
) -> AgentLlmCredentialResponse:
    """註冊或覆寫這個 Agent 的 LLM 憑證。

    **覆寫立刻對所有版本生效。** 這是刻意的：版本不可變（P5）指的是程式碼與 image，
    憑證是維運設定。代價見 [ADR-0011]。
    """
    agent = await service.load_agent(session, agent_id)
    meta = await agent_credentials.set_credential(
        session, agent.id,
        api_key=body.api_key, base_url=body.base_url, default_model=body.default_model,
    )
    return AgentLlmCredentialResponse(**meta.__dict__)


@router.get("/{agent_id}/llm-credential", response_model=AgentLlmCredentialResponse)
async def get_llm_credential(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> AgentLlmCredentialResponse:
    """回這個 Agent 有沒有設憑證。**不回金鑰**，只有尾四碼。"""
    agent = await service.load_agent(session, agent_id)
    meta = await agent_credentials.describe(session, agent.id)
    return AgentLlmCredentialResponse(**meta.__dict__)


@router.delete("/{agent_id}/llm-credential", status_code=204)
async def delete_llm_credential(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """移除憑證。之後這個 Agent 退回用平台預設金鑰。"""
    agent = await service.load_agent(session, agent_id)
    await agent_credentials.delete_credential(session, agent.id)
    return Response(status_code=204)


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
    session: AsyncSession = Depends(get_session),
) -> IssuedAgentApiKeyResponse:
    """發一把 key 給下游應用。**`key` 只在這次回應出現，之後讀不回來。**"""
    agent = await service.load_agent(session, agent_id)
    issued = await agent_api_keys.issue(session, agent.id, body.name)
    return IssuedAgentApiKeyResponse(
        **_key_view(issued.row).model_dump(), key=issued.plaintext,
    )


@router.get("/{agent_id}/api-keys", response_model=AgentApiKeyListResponse)
async def list_agent_api_keys(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> AgentApiKeyListResponse:
    """列出這個 Agent 的所有 key，**含已撤銷的**。

    已撤銷的留在列表裡是因為歷史 Run 還掛在它們身上；藏起來的話，
    用量查詢會出現一把查不到名字的 key。
    """
    agent = await service.load_agent(session, agent_id)
    rows = await agent_api_keys.list_keys(session, agent.id)
    return AgentApiKeyListResponse(items=[_key_view(row) for row in rows])


@router.delete("/{agent_id}/api-keys/{key_id}", response_model=AgentApiKeyResponse)
async def revoke_agent_api_key(
    agent_id: str,
    key_id: str,
    session: AsyncSession = Depends(get_session),
) -> AgentApiKeyResponse:
    """撤銷一把 key。**軟撤銷**，所以回 200 帶 `revoked_at`，不是 204。

    撤銷後立刻失效，但那把 key 的列仍在，過去的 Run 也還查得到是誰打的。
    重複撤銷是冪等的。
    """
    agent = await service.load_agent(session, agent_id)
    row = await agent_api_keys.load_key(session, agent.id, key_id)
    return _key_view(await agent_api_keys.revoke(session, row))


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
    session: AsyncSession = Depends(get_session),
) -> AgentUsageResponse:
    """這個 Agent 的 LLM 用量：總計、依 key、依 model。

    回答的是「這把 key 這個月花了多少、花在哪個模型上」（spec §3.3 的第一條查詢）。
    """
    agent = await service.load_agent(session, agent_id)
    filters = _filters(api_key_id, since, until, model, credential_source, None, False)
    return AgentUsageResponse(**await usage_service.aggregate(session, agent.id, filters))


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
    session: AsyncSession = Depends(get_session),
) -> LlmCallListResponse:
    """逐筆列出 LLM 呼叫，最新的在前。

    回答的是「這把 key 的哪一筆 request 出錯了」（spec §3.3 的第二條查詢）。
    `errors_only=true` 是除錯的第一個動作，所以給它一個開關，
    不要求呼叫端自己組 `status_code >= 400 OR error IS NOT NULL`。
    """
    agent = await service.load_agent(session, agent_id)
    filters = _filters(
        api_key_id, since, until, model, credential_source, status_code, errors_only)
    items, next_cursor = await usage_service.list_calls(
        session, agent.id, filters, limit=limit, cursor=cursor)
    return LlmCallListResponse(items=items, next_cursor=next_cursor)


@router.post("/{agent_id}/versions", status_code=202, response_model=AgentVersionResponse,
             response_model_exclude_none=False)
async def create_version(
    agent_id: str,
    body: CreateAgentVersionRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AgentVersionResponse:
    agent = await service.load_agent(session, agent_id)
    row = await service.create_version(session, agent, body)
    await asyncio.to_thread(dispatch_build, str(row.id))
    response.headers["Location"] = f"/v1/agents/{agent_id}/versions/{row.semver}"
    return service.version_response(row)


@router.get("/{agent_id}/versions", response_model=AgentVersionListResponse)
async def list_versions(
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> AgentVersionListResponse:
    agent = await service.load_agent(session, agent_id)
    rows, next_cursor = await service.list_versions(
        session, agent.id, limit=limit, cursor=cursor
    )
    return AgentVersionListResponse(
        items=[service.version_response(row, detail=True) for row in rows],
        next_cursor=next_cursor,
    )


@router.get("/{agent_id}/versions/{semver}", response_model=AgentVersionResponse)
async def get_version(
    agent_id: str,
    semver: str,
    session: AsyncSession = Depends(get_session),
) -> AgentVersionResponse:
    agent = await service.load_agent(session, agent_id)
    return service.version_response(
        await service.load_version_by_semver(session, agent.id, semver), detail=True
    )


@router.get("/{agent_id}/openapi.json")
async def agent_openapi(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    agent = await service.load_agent(session, agent_id)
    version = await service.resolve_published_version(session, agent.id, None)
    return service.generate_agent_openapi(agent, version, str(request.base_url).rstrip("/"))
