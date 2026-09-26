"""Agent metadata, OCI versions, and generated Agent OpenAPI."""

import asyncio

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import get_session
from apps.api.schemas import (
    AgentDetail,
    AgentListResponse,
    AgentSummary,
    AgentVersionListResponse,
    AgentVersionResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    AgentLlmCredentialResponse,
    CreateAgentVersionRequest,
    SetAgentLlmCredentialRequest,
    UpdateAgentRequest,
)
from apps.api.services import agent_credentials
from apps.api.services import agents as service
from apps.api.tasks import dispatch_build

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
    await service.delete_agent(session, await service.load_agent(session, agent_id))
    return Response(status_code=204)


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
