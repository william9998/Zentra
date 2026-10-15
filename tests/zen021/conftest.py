"""ZEN-021（ADR-0016 同版號取代）沿用 ZEN-019 的真 DB + in-process app。"""

from tests.zen019.conftest import _asyncpg_pool, app_client  # noqa: F401
