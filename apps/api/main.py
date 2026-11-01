from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis

from apps.api import db
from apps.api.config import get_settings
from apps.api.errors import ZentraError, internal_error_handler, validation_error_handler, zentra_error_handler
from apps.api.routers import agents, internal, mcp_keys, runs
from apps.api.mcp import mount_mcp
from apps.api.static import mount_console

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await db.init_pool()
    try:
        yield
    finally:
        await db.close_pool()
        await app.state.redis.aclose()


app = FastAPI(
    title="Zentra API",
    version="0.3.1",
    lifespan=lifespan,
    description="Agent 平台。每個 Agent 天生有 REST 門面，並可被勾選進單一 MCP 接口 /mcp（P3）。",
)
app.add_exception_handler(ZentraError, zentra_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, internal_error_handler)
app.include_router(runs.router)
app.include_router(agents.router)
app.include_router(mcp_keys.router)
app.include_router(internal.router)
# Console（ADR-0006）：UI 路由，include_in_schema=False，不進 OpenAPI。
mount_console(app)
# MCP 門面（P3）：與 REST 同源，協定端點不進 OpenAPI。
mount_mcp(app)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}
