import uuid

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import get_session
from apps.api.enums import ErrorCode, RunStatus
from apps.api.errors import ZentraError, not_found
from apps.api.models import Run
from apps.api.services import runtoken
from apps.api.services import validationtoken
from apps.api.models import AgentVersion


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


async def load_run(run_id: str, session: AsyncSession) -> Run:
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise not_found("run") from None
    run = await session.get(Run, rid)
    if run is None:
        raise not_found("run")
    return run


async def require_run_token(
    run_id: str,
    authorization: str = Header(default=""),
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
) -> Run:
    """P4：沙盒只拿綁定單一 run_id 的 run token。

    帶著 A 的 token 存取 B 的 run_id → 403（不是 404 —— 對沙盒沒有必要隱藏存在性，
    而且 403 比較能讓我們在稽核時看出有人在試）。
    """
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "missing run token", 401)

    bound = await runtoken.resolve(redis, token)
    if bound is None:
        if await validationtoken.resolve(redis, token) is not None:
            raise ZentraError(ErrorCode.PERMISSION_DENIED, "validation token cannot access runs", 403)
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "run token invalid or expired", 401)
    if bound != run_id:
        raise ZentraError(
            ErrorCode.PERMISSION_DENIED, "run token does not belong to this run", 403, run_id=run_id
        )
    run = await load_run(run_id, session)
    if RunStatus(run.status).is_terminal:
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "run token revoked", 401)
    return run


async def require_validation_token(
    version_id: str,
    authorization: str = Header(default=""),
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
) -> AgentVersion:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "missing validation token", 401)
    bound = await validationtoken.resolve(redis, token)
    if bound is None:
        if await runtoken.resolve(redis, token) is not None:
            raise ZentraError(ErrorCode.PERMISSION_DENIED, "run token cannot validate versions", 403)
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "validation token invalid or expired", 401)
    if bound != version_id:
        raise ZentraError(
            ErrorCode.PERMISSION_DENIED,
            "validation token does not belong to this agent version", 403,
        )
    try:
        parsed = uuid.UUID(version_id)
    except ValueError:
        raise not_found("agent version") from None
    version = await session.get(AgentVersion, parsed)
    if version is None:
        raise not_found("agent version")
    return version
