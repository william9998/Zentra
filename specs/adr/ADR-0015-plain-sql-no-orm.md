# ADR-0015：拿掉 SQLAlchemy 與 Alembic，改用純 SQL

| | |
|---|---|
| 狀態 | **已採納** |
| 日期 | 2026-10-14 |
| 相關 | SPEC `v0.6.0` §6；實作 [ZEN-020](../features/ZEN-020/spec.md)。**api-contract 不變、schema 不變、P1–P5 不變** |

## 為什麼

不是效能。要讓送進資料庫的 SQL 與連線借還寫在程式碼表面上。2026-10-14 的事故是「等外部回應時握著連線」；熱修 `fbea4d5` 已擋住，這份 ADR 把它變成結構。

選過、否決的：只加 review 規則（A）；留 SQLAlchemy Core（B）。採用 **C**：`app` asyncpg、`worker` psycopg 3、編號 `.sql` + 自寫 runner。兩邊都換 psycopg 會動到 `app` 的型別轉換，不採用。

## 決定

### 連線（違反就是 bug）

工作單元裡只做這條連線上的 SQL。唯一寫法：`db.transaction()`／`db.connection()`。區塊結束即還連線。

工作單元內禁止 HTTP、DNS、SMTP、任何 Redis、sleep、Docker、等 future。收 `conn` 的函式不准做這些；做外部 I/O 的函式不收 `conn`。

沒有隱式交易。`FOR UPDATE` 與 `pg_advisory_xact_lock` 必須在 `transaction()` 裡，否則鎖立刻放掉、冪等 claim 靜默失效。FastAPI 不注入「一個 request 一條連線」。

`application_name`：`zentra-app`／`zentra-worker`／`zentra-migrate`。`app` 與 `worker` 設 `idle_in_transaction_session_timeout=60s`（安全網，觸發代表有 bug，修程式不調值；回 `INTERNAL_ERROR`）。runner 不設。

`app` 池子：`min_size=DB_POOL_SIZE`（10）、`max_size=DB_POOL_SIZE+DB_MAX_OVERFLOW`（30）、acquire 30s。worker 每子行程一條 lazy 連線，`worker_process_init` 丟棄 fork 繼承的連線。加總留在 `max_connections=100` 以下。

### SQL

| | `app` | `worker` |
|---|---|---|
| driver | asyncpg，`$1` | psycopg 3，`%(name)s`（字面 `%` 寫 `%%`） |
| jsonb 寫 | Python 物件（pool 已註冊 codec） | `psycopg.types.json.Jsonb(obj)` |

兩邊都不准先 `json.dumps`。不共用 SQL 字串、不寫佔位符轉換。值走參數。

列用 `apps/api/rows.py` 的 frozen dataclass。改資料只准顯式 `UPDATE`。計數器用 `SET x = x + 1`。

### Migration

`apps/api/migrations/NNNN_*.sql` 是 schema 唯一事實來源。`python -m apps.api.migrations` 只認 `schema_migrations`：空庫從 `0001` 跑；已有表但沒版本表就拒絕，叫人先跑 `scripts/adopt-legacy-schema.sh`。已套用檔 checksum 變了就拒絕。只有 up。

既有庫（`alembic_version='0008'`）各跑一次 adopt 腳本。**不刪 `alembic_version`**，留給下一支改 schema 的 `0009`。`make up` 不自動跑 adopt。

`DATABASE_URL` 剝掉 `+driver`，舊前綴仍可用。

## 何時重看

兩種佔位符造成第二次 bug → 兩邊改 psycopg。worker 開始用執行緒碰 DB → 再談 pool。
