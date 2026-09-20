"""worker 用同步 SQLAlchemy。

Celery 是同步的，硬把 async engine 塞進 prefork worker 只會製造 event loop 的麻煩。
api 用 asyncpg，worker 用 psycopg —— 同一個 schema，兩種 driver，這是刻意的。
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from apps.api.config import get_settings

_settings = get_settings()

engine = create_engine(_settings.sync_database_url, pool_pre_ping=True, future=True)
SessionSync: sessionmaker[Session] = sessionmaker(engine, expire_on_commit=False)
