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
    CreateAgentVersionRequest,
    UpdateAgentRequest,
)
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
