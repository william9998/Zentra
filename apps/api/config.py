from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/config.py → apps/ → apps/console。容器內是 /srv/apps/console（api.Dockerfile
# 的 WORKDIR /srv），本機 uvicorn 則不管從哪個 cwd 啟動都解析得到。
_CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    zentra_env: str = "dev"

    database_url: str = "postgresql+asyncpg://zentra:zentra@postgres:5432/zentra"
    redis_url: str = "redis://redis:6379/0"

    # Console 靜態檔（ADR-0006）：api 同時是 Console 的 web server，沒有 console 容器。
    console_dir: Path = _CONSOLE_DIR

    # 沙盒（P4：這裡不放任何長期憑證）
    sandbox_image: str = "zentra/agent-runtime:dev"
    sandbox_network: str = "zentra-sandbox-net"
    sandbox_gateway_url: str = "http://app:8000/internal"
    sandbox_max_concurrent: int = 10

    # SPEC §10：Run 最長 15 分鐘硬上限
    run_timeout_seconds: int = 900

    # ── LLM（ZEN-002 / P4）────────────────────────────────────────────
    # 真金鑰**只**存在這裡，也就是 app 服務的環境變數裡。
    # 它永遠不進沙盒：沙盒拿到的是綁定單一 run_id 的 run token，
    # Internal Gateway 收到請求後才換上真金鑰（SPEC §7.3）。
    # 這幾個明確標 alias：這個 Settings 沒有 env_prefix，預設會去讀 `LLM_API_KEY`
    # 那種太通用、容易跟主機上別的東西撞名的名字。
    llm_base_url: str = Field(
        default="https://api.openai.com/v1", validation_alias="ZENTRA_LLM_BASE_URL")
    llm_api_key: str = Field(          # 空字串 = 未設定 → 呼叫回 LLM_ERROR
        default="", validation_alias="ZENTRA_LLM_API_KEY")
    llm_default_model: str = Field(
        default="gpt-4o-mini", validation_alias="ZENTRA_LLM_DEFAULT_MODEL")
    llm_timeout_seconds: float = Field(
        default=120.0, validation_alias="ZENTRA_LLM_TIMEOUT_SECONDS")

    # 靜態加密用的金鑰（ZEN-012）。使用者交給平台保管的 LLM 金鑰用它加密後才進 DB。
    # 產生方式：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    #
    # **沒設定就不准存任何憑證** —— 服務會拒絕寫入並說明原因，而不是把明文寫進資料庫。
    # 換掉這把金鑰會讓既有的憑證全部解不開（那是設計如此：它就是那些憑證的鎖）。
    secret_key: str = Field(default="", validation_alias="ZENTRA_SECRET_KEY")

    # 每百萬 token 的美金單價，用來算 `usage.cost_usd`。
    # 形狀：{"<model>": {"input": "0.15", "output": "0.60"}}
    # 沒設定的 model → 成本記 "0"。SPEC §11 把「計費出帳」列在範圍外，
    # 這裡只做計量；留空不影響 token 數的正確性。
    llm_prices: dict[str, dict[str, str]] = {}

    @property
    def sync_database_url(self) -> str:
        """Alembic 用同步 driver。"""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
