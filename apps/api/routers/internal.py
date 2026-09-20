"""Internal Gateway —— 沙盒**唯一**的網路對端（P4，api-contract.md §4）。

不對外開放，不進 OpenAPI。認證一律 run token：綁定單一 run_id、TTL = 該 Run 的 timeout、
跑完即撤銷。帶著 A 的 token 存取 B 的 run_id → 403。

ZEN-000 不含 `/internal/llm/*`（骨架不呼叫 LLM，留到 ZEN-002）。
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db import get_session
from apps.api.deps import get_redis, require_run_token
from apps.api.enums import BUILTIN_AGENT_SEMVER, EventType, RunStatus
from apps.api.models import Run
from apps.api.schemas import BootstrapResponse, SandboxEventBatch, SandboxResult
from apps.api.services import events

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


@router.get("/runs/{run_id}/bootstrap", response_model=BootstrapResponse)
async def bootstrap(run: Run = Depends(require_run_token)) -> BootstrapResponse:
    """沙盒啟動後第一件事：拿 manifest 與 input。"""
    return BootstrapResponse(
        run_id=str(run.id),
        entrypoint=run.entrypoint,
        input=run.input,
        # ZEN-000 的 agent 寫死在映像裡，所以 manifest 只是個佔位。
        # ZEN-001 起這裡會回真的 manifest + entrypoints schema。
        manifest={"agent_version": BUILTIN_AGENT_SEMVER, "builtin": True},
    )


@router.post("/runs/{run_id}/events", status_code=202)
async def ingest_events(
    body: SandboxEventBatch,
    run: Run = Depends(require_run_token),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict:
    """沙盒回寫事件。沙盒**不得**直接寫 Postgres 或 Redis —— 由這裡代寫（SPEC §5）。"""
    allowed = {str(e) for e in EventType}
    seqs = []
    for ev in body.events:
        if ev.type not in allowed:
            continue          # 不認識的事件型別一律丟棄，不讓沙盒塞任意資料進事件流
        seqs.append(await events.append_event(session, redis, run.id, ev.type, ev.payload))
    return {"accepted": len(seqs), "last_seq": seqs[-1] if seqs else None}


@router.post("/runs/{run_id}/result", status_code=202)
async def ingest_result(
    body: SandboxResult,
    run: Run = Depends(require_run_token),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """沙盒回寫最終 output / error。

    **狀態不是在這裡轉的** —— 由 worker 在確認沙盒真的結束（拿到 exit code）之後才轉。
    沙盒自己說「我成功了」不算數。
    """
    if RunStatus(run.status).is_terminal:
        return {"accepted": False, "reason": "run already terminal"}
    run.output = body.output
    run.error = body.error
    run.usage = body.usage
    await session.commit()
    return {"accepted": True}
