from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """剝掉 +driver，一律變成 postgresql://。asyncpg 與 psycopg 都只接受這個。

    接受 postgresql://、postgres://、postgresql+asyncpg://、postgresql+psycopg://、
    postgresql+psycopg2://。其他 scheme 在啟動時報錯。
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.split("+", 1)[0]
    if scheme not in ("postgresql", "postgres"):
        raise ValueError(
            f"unsupported DATABASE_URL scheme {parsed.scheme!r}; "
            "expected postgresql://, postgres://, postgresql+asyncpg://, "
            "or postgresql+psycopg://"
        )
    return urlunparse(parsed._replace(scheme="postgresql"))

# apps/api/config.py → apps/ → apps/console。容器內是 /srv/apps/console（api.Dockerfile
# 的 WORKDIR /srv），本機 uvicorn 則不管從哪個 cwd 啟動都解析得到。
_CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    zentra_env: str = "dev"

    database_url: str = "postgresql://zentra:zentra@postgres:5432/zentra"
    redis_url: str = "redis://redis:6379/0"

    # app 的連線池（只有一個 uvicorn 行程，所以這就是整個 HTTP 端的上限）。
    # 等外部回應的路徑都會先結束交易，所以連線只在真的查詢時被占用。
    # ⚠️ 加總要低於 Postgres 的 max_connections（預設 100），worker 的 10 個子行程也要連。
    db_pool_size: int = Field(default=10, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, validation_alias="DB_MAX_OVERFLOW")

    # Console 靜態檔（ADR-0006）：api 同時是 Console 的 web server，沒有 console 容器。
    console_dir: Path = _CONSOLE_DIR

    # 沙盒（P4：這裡不放任何長期憑證）
    sandbox_image: str = "zentra/agent-runtime:dev"
    sandbox_network: str = "zentra-sandbox-net"
    sandbox_gateway_url: str = "http://app:8000/internal"
    sandbox_max_concurrent: int = 10

    # SPEC §10：Run 最長 15 分鐘硬上限
    run_timeout_seconds: int = 900

    # 呼叫 Agent 是否**強制**帶 API key（ZEN-015）。
    #
    # 預設 false：契約 §0 寫明 `/v1` 的認證在 MVP 未實作，而 Console 目前也沒有
    # 帶 key 的能力（它該走同源 session，那還沒做）。所以現在的行為是
    # **有帶就驗、沒帶放行** —— key 買到的是「用量歸戶」，不是「擋住未授權的人」。
    #
    # 這兩件事不一樣，別混。要真的擋人就把它設成 true，但**設之前先確認 Console
    # 已經有辦法認證**，否則 Console 會整個不能用。
    require_agent_api_key: bool = Field(
        default=False, validation_alias="ZENTRA_REQUIRE_AGENT_API_KEY")

    # ── 常駐容器（ZEN-015 / ADR-0012）────────────────────────────────
    # 一個版本**只開一個**常駐容器，它同時最多跑這麼多個 Run。
    # 滿了不開第二個容器：多的工作留在 Redis 佇列，容器空出位子才會來拿。
    #
    # ⚠️ 這個數字與容器的 mem_limit（2 GB）是**一組的**：N=50 表示平均每 Run 約 40 MB。
    #    agent 若把大 payload 整份讀進記憶體，幾個並行就會吃掉額度，
    #    然後同容器的 N 個一起被 OOM killer 帶走。調 N 時要一起看記憶體。
    sandbox_runs_per_container: int = Field(
        default=50, validation_alias="SANDBOX_RUNS_PER_CONTAINER")
    # 一筆 Run 在常駐容器佇列裡最多等多久（秒）。排隊時間**不算**進 Run 的 timeout_seconds
    # （容器取走才開始計時），但不能無限等 —— 容器死掉時佇列裡的工作永遠不會被取走。
    run_queue_timeout_seconds: int = Field(
        default=3600, validation_alias="RUN_QUEUE_TIMEOUT_SECONDS")

    # 容器閒置多久回收（秒）。0 = 不自動回收，只能手動停止。
    #
    # **自動啟動之後這個值不能是 0。** 任何被呼叫過一次的 Agent 都會留下一個
    # 常駐容器，而每個容器吃 2 GB —— 不回收的話，叫過三個 Agent 就把這台機器塞滿。
    # 30 分鐘是「還會再被呼叫」與「別一直佔著」之間的折衷。
    container_idle_timeout_seconds: int = Field(
        default=1800, validation_alias="CONTAINER_IDLE_TIMEOUT_SECONDS")

    # 第一筆 Run 最多等常駐容器啟動多久（秒）。含 image pull。
    # 逾時不會讓 Run 失敗 —— 它會退回一次性沙盒，常駐只是效能手段。
    container_start_timeout_seconds: int = Field(
        default=180, validation_alias="CONTAINER_START_TIMEOUT_SECONDS")

    # 內部 Registry（ADR-0009 / ADR-0013）。
    #
    # 兩個值指的是**同一個 registry 的兩個視角**：`image_ref` 裡寫的是主機看得到的
    # `localhost:6000`（執行 pull 的是主機的 Docker daemon，它不在 zentra-net 上），
    # 而 worker 要刪 manifest 時是從 zentra-net 裡面打，位址是服務名。
    # 少了這組對應就會出現「刪不到但也不報錯」——它去 localhost 找一個不存在的 registry。
    internal_registry_host: str = Field(
        default="localhost:6000", validation_alias="INTERNAL_REGISTRY_HOST")
    internal_registry_url: str = Field(
        default="http://registry:5000", validation_alias="INTERNAL_REGISTRY_URL")

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

    # ── 對外出口與寄信（ZEN-019 / ADR-0014 E6、M3）─────────────────────
    #
    # 這幾個上限不是效能調校，是**防止被封 IP 與被鎖 Gmail 帳號**的閘門。
    # 被封的是整台主機、被鎖的是使用者本人的帳號 —— 兩者都不是這支 agent
    # 自己承擔後果，所以預設值要小，調大要有具體理由。
    # 這幾個**刻意不寫 validation_alias**：這個 Settings 沒有 env_prefix，
    # 欄位名本身就是環境變數名（大小寫不敏感），例如
    # `EGRESS_MAX_CALLS_PER_RUN` → `egress_max_calls_per_run`。
    # SPEC §10 要求全部可由環境變數調整，測試也要把上限調到 1–2 才驗得了。
    egress_max_calls_per_run: int = 100
    #: request body 與 response 的硬上限。**超量不截斷、不夾值，直接當錯誤** ——
    #: 截斷過的 JSON 解析失敗時，錯誤訊息會指向 agent 的解析程式碼。
    egress_max_request_bytes: int = 64 * 1024
    egress_max_response_bytes: int = 1024 * 1024
    email_max_per_run: int = 5
    #: **滾動 60 分鐘窗口，不是整點桶**（契約 §4.4 第 3 點）：整點桶允許
    #: `:59` 寄 60 封、`:01` 再寄 60 封，兩分鐘 120 封 —— 那正是這條上限要擋的。
    email_max_per_hour: int = 60
    email_max_body_bytes: int = 256 * 1024
    email_timeout_seconds: float = 30.0

    # 每百萬 token 的美金單價，用來算 `usage.cost_usd`。
    # 形狀：{"<model>": {"input": "0.15", "output": "0.60"}}
    # 沒設定的 model → 成本記 "0"。SPEC §11 把「計費出帳」列在範圍外，
    # 這裡只做計量；留空不影響 token 數的正確性。
    llm_prices: dict[str, dict[str, str]] = {}

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @property
    def dsn(self) -> str:
        """正規化後的 postgresql://…，給 asyncpg / psycopg / runner 用。"""
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
