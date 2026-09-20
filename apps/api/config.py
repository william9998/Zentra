from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    zentra_env: str = "dev"

    database_url: str = "postgresql+asyncpg://zentra:zentra@postgres:5432/zentra"
    redis_url: str = "redis://redis:6379/0"

    # 沙盒（P4：這裡不放任何長期憑證）
    sandbox_image: str = "zentra/agent-runtime:dev"
    sandbox_network: str = "zentra-sandbox-net"
    sandbox_gateway_url: str = "http://api:8000/internal"
    sandbox_max_concurrent: int = 10

    # SPEC §10：Run 最長 15 分鐘硬上限
    run_timeout_seconds: int = 900

    @property
    def sync_database_url(self) -> str:
        """Alembic 用同步 driver。"""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
