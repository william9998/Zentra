"""編號 .sql 的 migration runner。只認 schema_migrations，只有 up。

連線 application_name=zentra-migrate，不設 idle_in_transaction_session_timeout。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from apps.api.config import normalize_database_url

# Session-level advisory lock；與 scripts/adopt-legacy-schema.sh 共用同一個常數。
MIGRATION_LOCK_KEY = 820020

SCHEMA_MIGRATIONS_DDL = """CREATE TABLE schema_migrations (
    version text PRIMARY KEY,
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)"""

_FILE_NAME = re.compile(r"^(\d{4})_(.+)\.sql$")
_MIGRATIONS_DIR = Path(__file__).resolve().parent

_LEGACY_HINT = (
    "資料庫已有資料表但沒有 schema_migrations。既有資料庫請先跑一次：\n"
    "  bash scripts/adopt-legacy-schema.sh --dry-run\n"
    "  確認輸出後去掉 --dry-run 再跑，然後重跑 make migrate。"
)


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        from apps.api.config import get_settings

        raw = get_settings().database_url
    try:
        return normalize_database_url(raw)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migration_files() -> list[tuple[str, str, Path]]:
    found: list[tuple[str, str, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        match = _FILE_NAME.match(path.name)
        if match is None:
            raise SystemExit(f"unexpected migration filename: {path.name}")
        found.append((match.group(1), match.group(2), path))
    if not found:
        raise SystemExit(f"no NNNN_*.sql files in {_MIGRATIONS_DIR}")
    return found


def apply(dsn: str) -> int:
    files = migration_files()
    with psycopg.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
        cursor_factory=psycopg.ClientCursor,
        application_name="zentra-migrate",
    ) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            return _apply_locked(conn, files)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


def _table_exists(conn: psycopg.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS exists",
        (f"public.{name}",),
    ).fetchone()
    return bool(row and row["exists"])


def _public_has_tables(conn: psycopg.Connection) -> bool:
    row = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
        ) AS has_tables
        """
    ).fetchone()
    return bool(row and row["has_tables"])


def _apply_locked(conn: psycopg.Connection, files: list[tuple[str, str, Path]]) -> int:
    if _table_exists(conn, "schema_migrations"):
        pass
    elif not _public_has_tables(conn):
        with conn.transaction():
            conn.execute(SCHEMA_MIGRATIONS_DDL)
        print("created schema_migrations")
    else:
        print(_LEGACY_HINT, file=sys.stderr)
        return 1

    applied = {
        row["version"]: row["checksum"]
        for row in conn.execute(
            "SELECT version, checksum FROM schema_migrations"
        ).fetchall()
    }

    for version, name, path in files:
        file_sum = checksum(path)
        if version in applied:
            if applied[version] != file_sum:
                print(
                    f"checksum mismatch for {version}: "
                    f"applied={applied[version]} file={file_sum}",
                    file=sys.stderr,
                )
                return 1
            print(f"已存在 {version} {name}")
            continue
        sql_text = path.read_text(encoding="utf-8")
        with conn.transaction():
            conn.execute(sql_text)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) "
                "VALUES (%s, %s, %s)",
                (version, name, file_sum),
            )
        print(f"套用 {version} {name}")
    return 0


def main() -> None:
    sys.exit(apply(_dsn()))


if __name__ == "__main__":
    main()
