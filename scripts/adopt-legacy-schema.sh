#!/usr/bin/env bash
# 過渡工具：把既有資料庫（版本表停在 0008）接到 schema_migrations。
#
# 在刪除舊版本表的 0009 合併時，同一個 PR 刪掉這支腳本。
# 之後的新環境都是空庫起步，舊庫則已全部接過。
#
#   bash scripts/adopt-legacy-schema.sh --dry-run   # 只檢查、印出將寫入的八列，最後 ROLLBACK
#   bash scripts/adopt-legacy-schema.sh             # 真的寫入
#
# 只用 docker compose exec，不呼叫 Python、不需要 uv。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 與 apps/api/migrations/__main__.py 的 MIGRATION_LOCK_KEY 相同。
MIGRATION_LOCK_KEY=820020

DRY_RUN=0
DATABASE=zentra
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --database)
      DATABASE="${2:-}"
      if [[ -z "$DATABASE" || ! "$DATABASE" =~ ^[a-z0-9_]+$ ]]; then
        echo "用法：bash scripts/adopt-legacy-schema.sh [--dry-run] [--database NAME]" >&2
        exit 1
      fi
      shift
      ;;
    *)
      echo "用法：bash scripts/adopt-legacy-schema.sh [--dry-run] [--database NAME]" >&2
      exit 1
      ;;
  esac
  shift
done

psql() {
  docker compose exec -T postgres psql -U zentra -d "$DATABASE" -v ON_ERROR_STOP=1 "$@"
}

schema_migrations_exists() {
  psql -t -A -c "SELECT to_regclass('public.schema_migrations') IS NOT NULL;"
}

# ── 1. 已經接過？ ──────────────────────────────────────────────────
if [[ "$(schema_migrations_exists)" == "t" ]]; then
  VERSIONS=$(psql -t -A -c "SELECT version FROM schema_migrations ORDER BY version;")
  EXPECTED=$'0001\n0002\n0003\n0004\n0005\n0006\n0007\n0008'
  if [[ "$(printf '%s\n' "$VERSIONS" | sed '/^$/d')" == "$EXPECTED" ]]; then
    echo "已接過。schema_migrations 現有內容："
    psql -c "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version;"
    exit 0
  fi
  echo "schema_migrations 已存在但列不完整（需要 0001…0008 八列）。現況：" >&2
  psql -c "SELECT version, name, checksum FROM schema_migrations ORDER BY version;" >&2
  exit 1
fi

# ── 2. 舊版本表必須恰好一列且為 0008 ─────────────────────────────
HAS_LEGACY=$(psql -t -A -c "SELECT to_regclass('public.alembic_version') IS NOT NULL;")
if [[ "$HAS_LEGACY" != "t" ]]; then
  echo "找不到舊版本表 alembic_version。只接受 0008。請先用 ZEN-020 之前的 image（例如 fbea4d5）跑 alembic upgrade head 再來。" >&2
  exit 1
fi

LEGACY_COUNT=$(psql -t -A -c "SELECT count(*) FROM alembic_version;")
LEGACY_VALUE=$(psql -t -A -c "SELECT version_num FROM alembic_version;" || true)
if [[ "$LEGACY_COUNT" != "1" || "$LEGACY_VALUE" != "0008" ]]; then
  echo "只接受 0008。目前 alembic_version 列數=${LEGACY_COUNT} 值='${LEGACY_VALUE}'。請先用 ZEN-020 之前的 image（例如 fbea4d5）跑 alembic upgrade head 再來。" >&2
  exit 1
fi

# ── 3. 在 app 容器內對同一批檔案算 checksum ──────────────────────
SUMS=$(docker compose exec -T app sh -c 'sha256sum apps/api/migrations/000[1-8]_*.sql' 2>/dev/null || true)
if [[ -z "$SUMS" ]]; then
  echo "app 容器內找不到八支 apps/api/migrations/000[1-8]_*.sql。請先重建 app 映像。" >&2
  exit 1
fi

FILE_COUNT=$(printf '%s\n' "$SUMS" | sed '/^$/d' | wc -l | tr -d ' ')
if [[ "$FILE_COUNT" != "8" ]]; then
  echo "app 容器內的 0001…0008 .sql 必須恰好八個，實際 ${FILE_COUNT} 個。請先重建 app 映像。" >&2
  printf '%s\n' "$SUMS" >&2
  exit 1
fi

VALUES_SQL=""
SEEN=""
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  checksum=${line%% *}
  filepath=${line##* }
  filename=$(basename "$filepath")
  version=${filename%%_*}
  rest=${filename#*_}
  name=${rest%.sql}
  if [[ ! "$checksum" =~ ^[0-9a-f]{64}$ ]]; then
    echo "checksum 格式不符：${checksum}" >&2
    exit 1
  fi
  if [[ ! "$name" =~ ^[a-z0-9_]+$ ]]; then
    echo "name 格式不符：${name}" >&2
    exit 1
  fi
  if [[ ! "$version" =~ ^000[1-8]$ ]]; then
    echo "version 不是 0001…0008：${version}" >&2
    exit 1
  fi
  case " $SEEN " in
    *" $version "*) echo "重複的 version：${version}" >&2; exit 1 ;;
  esac
  SEEN="$SEEN $version"
  VALUES_SQL="${VALUES_SQL}  ('${version}', '${name}', '${checksum}'),"$'\n'
  printf '  %s  %s  %s\n' "$version" "$name" "$checksum"
done <<< "$SUMS"

for v in 0001 0002 0003 0004 0005 0006 0007 0008; do
  case " $SEEN " in
    *" $v "*) ;;
    *) echo "缺少 ${v} 的 .sql。請先重建 app 映像。" >&2; exit 1 ;;
  esac
done

VALUES_SQL="${VALUES_SQL%"${VALUES_SQL##*[![:space:]]}"}"
VALUES_SQL="${VALUES_SQL%,}"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "--dry-run：以上八列將寫入 schema_migrations；第 4 步以 ROLLBACK 結束。"
  END_TX="ROLLBACK;"
else
  END_TX="COMMIT;"
fi

# ── 4. 一個交易：鎖 → 再檢查 → 建表 → INSERT 八列 ──────────────
# CREATE TABLE 與 apps/api/migrations/__main__.py 的 SCHEMA_MIGRATIONS_DDL 逐字相同。
psql <<SQL
BEGIN;
SELECT pg_advisory_xact_lock(${MIGRATION_LOCK_KEY});
DO \$\$
BEGIN
  IF to_regclass('public.schema_migrations') IS NOT NULL THEN
    RAISE EXCEPTION 'schema_migrations 已存在';
  END IF;
  IF to_regclass('public.alembic_version') IS NULL THEN
    RAISE EXCEPTION '找不到舊版本表，只接受 0008';
  END IF;
  IF (SELECT count(*) FROM alembic_version) <> 1 THEN
    RAISE EXCEPTION '舊版本表必須恰好一列';
  END IF;
  IF (SELECT version_num FROM alembic_version) <> '0008' THEN
    RAISE EXCEPTION '只接受 0008，目前是 %', (SELECT version_num FROM alembic_version);
  END IF;
END
\$\$;
CREATE TABLE schema_migrations (
    version text PRIMARY KEY,
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations (version, name, checksum) VALUES
${VALUES_SQL};
${END_TX}
SQL

if [[ $DRY_RUN -eq 1 ]]; then
  echo "dry-run 完成，沒有留下 schema_migrations。"
else
  echo "已寫入 schema_migrations（0001…0008）。"
  psql -c "SELECT version, name, checksum FROM schema_migrations ORDER BY version;"
fi
