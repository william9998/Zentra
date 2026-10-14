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


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


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


async def require_run_token_only(
    authorization: str = Header(default=""),
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
) -> Run:
    """像 `require_run_token`，但 Run 是**單靠 token** 解出來的。

    `/internal/llm/*` 的路徑裡沒有 `run_id`（契約 §4 就是這樣定的：那條路徑要與
    OpenAI 相容，不能塞我們自己的路徑參數）。所以 token 同時是身分**與**歸屬：
    它綁定哪個 run，這次呼叫就記在那個 run 上，呼叫者無法指定別人的 run。

    validation token 與 container token 明確被擋掉 —— 契約 §4：它們不能存取
    `/internal/runs/*`、`/internal/llm/*`、`/internal/egress/*` 或
    `/internal/notify/*`。**出口與寄信都必須有 run token，稽核才綁得到 `run_id`**
    （§2.10）；container token 一把服務整個 Agent，用它記帳會記不出是哪一筆 Run。
    """
    from apps.api.services import containertoken

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "missing run token", 401)

    run_id = await runtoken.resolve(redis, token)
    if run_id is None:
        if await validationtoken.resolve(redis, token) is not None:
            raise ZentraError(
                ErrorCode.PERMISSION_DENIED,
                "a validation token cannot use this gateway route", 403,
            )
        if await containertoken.resolve(redis, token) is not None:
            raise ZentraError(
                ErrorCode.PERMISSION_DENIED,
                "a container token can only take work; this route needs the run token "
                "that arrives with the work item", 403,
            )
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "run token invalid or expired", 401)

    run = await load_run(_text(run_id), session)
    if RunStatus(run.status).is_terminal:
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "run token revoked", 401)
    return run


async def optional_agent_api_key(
    agent_id: str,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    """解析呼叫端帶的 Agent API key（ZEN-015）。回傳 `AgentApiKey` 或 `None`。

    **有帶就必須有效，沒帶預設放行。**

    沒帶放行是因為契約 §0 寫明 `/v1` 的認證在 MVP 未實作，而 Console 目前也沒有
    帶 key 的能力。所以現在 key 買到的是**用量歸戶**，不是**擋住未授權的人** ——
    這兩件事不一樣，設定 `ZENTRA_REQUIRE_AGENT_API_KEY=true` 才會變成後者。

    帶了無效的 key 一律擋。「帶了但打錯」跟「沒帶」是不同的意圖，
    默默當成匿名放行會讓呼叫端以為自己的用量有被記到。
    """
    from apps.api.config import get_settings
    from apps.api.services import agent_api_keys

    scheme, _, token = authorization.partition(" ")
    presented = token if scheme.lower() == "bearer" and token else ""

    if not presented:
        if get_settings().require_agent_api_key:
            raise ZentraError(
                ErrorCode.UNAUTHENTICATED,
                "this platform requires an agent API key: send Authorization: Bearer <key>",
                401,
            )
        return None

    row = await agent_api_keys.resolve(session, presented)
    if row is None:
        raise ZentraError(
            ErrorCode.UNAUTHENTICATED, "the agent API key is invalid or revoked", 401
        )
    if str(row.agent_id) != agent_id:
        # 跨 Agent 使用是這把 key 唯一有意義的濫用方式，所以這條是重點。
        raise ZentraError(
            ErrorCode.PERMISSION_DENIED,
            "this API key belongs to a different agent", 403,
        )
    await agent_api_keys.touch(session, row)
    return row


async def require_container_token(
    agent_id: str,
    authorization: str = Header(default=""),
    redis: Redis = Depends(get_redis),
) -> tuple[str, str]:
    """常駐容器取工作用的憑證（ZEN-015）。回傳 `(agent_id, container_id)`。

    **權限只有一件事**：取這個 agent 的工作。它讀不了 Run、呼叫不了 LLM、
    拿不到別的 Agent 的工作 —— 那些都要 run token，而 run token 跟著工作項才進來。
    理由見 `services/containertoken.py`。
    """
    from apps.api.services import containertoken

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ZentraError(ErrorCode.UNAUTHENTICATED, "missing container token", 401)

    bound = await containertoken.resolve(redis, token)
    if bound is None:
        raise ZentraError(
            ErrorCode.UNAUTHENTICATED, "container token invalid or expired", 401
        )
    bound_agent, bound_container = bound
    if bound_agent != agent_id:
        # 跨 Agent 取工作是這個 token 唯一有意義的濫用方式，所以這條是重點。
        raise ZentraError(
            ErrorCode.PERMISSION_DENIED,
            "this container token belongs to a different agent", 403,
        )
    return bound_agent, bound_container


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
