from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis

from apps.api.config import get_settings
from apps.api.errors import ZentraError, internal_error_handler, validation_error_handler, zentra_error_handler
from apps.api.routers import internal, runs
from apps.api.static import mount_console

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield
    finally:
        await app.state.redis.aclose()


app = FastAPI(
    title="Zentra API",
    version="0.2.2",
    lifespan=lifespan,
    description="Agent 平台。每個 Agent 天生有 REST + MCP 兩個門面（P3）。",
)
app.add_exception_handler(ZentraError, zentra_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, internal_error_handler)
app.include_router(runs.router)
app.include_router(internal.router)
# Console（ADR-0006）：UI 路由，include_in_schema=False，不進 OpenAPI。
mount_console(app)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}
