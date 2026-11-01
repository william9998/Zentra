"""worker 資料庫連線：每子行程一條 psycopg。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from apps.api.config import get_settings

_pid: int | None = None
_conn: psycopg.Connection | None = None


def reset_connection() -> None:
    """worker_process_init 與 pid 對不上時呼叫：丟掉 fork 繼承來的連線。"""
    global _conn, _pid
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None
    _pid = None


def _broken(conn: psycopg.Connection) -> bool:
    if conn.closed:
        return True
    return bool(getattr(conn, "broken", False))


def get_connection() -> psycopg.Connection:
    global _conn, _pid
    if _conn is None or _broken(_conn) or _pid != os.getpid():
        reset_connection()
        _conn = psycopg.connect(
            get_settings().dsn,
            autocommit=True,
            row_factory=dict_row,
            application_name="zentra-worker",
            options="-c idle_in_transaction_session_timeout=60s",
        )
        _pid = os.getpid()
    return _conn


def require_transaction(conn: psycopg.Connection) -> None:
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError("lock functions must be called inside db.transaction()")


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    conn = get_connection()
    with conn.transaction():
        yield conn


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    yield get_connection()
