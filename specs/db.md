# 資料層：PostgreSQL + Redis

事後要查得到的 → PostgreSQL。會過期的（佇列、token、通知、租約）→ Redis。Redis 清空只會掉進行中的工作，不掉紀錄。
容器連不到兩者，一律經 `app` 的 `/internal/*`。

## PostgreSQL

**schema 的唯一來源是 `apps/api/migrations/NNNN_*.sql`**，每列的形狀在 `apps/api/rows.py`。

### 存取

- 純 SQL：`app` 用 asyncpg（連線池），`worker` 用 psycopg 3。沒有 ORM、沒有 Alembic。
- `application_name`：`zentra-app` / `zentra-worker` / `zentra-migrate`。
- **連線只在一個 `transaction()` / `connection()` 區塊內借出，區塊內不准做 HTTP / Redis / sleep / Docker 等外部 I/O。** 安全網：`idle_in_transaction_session_timeout = 60s`。
- 連線數：`APP_WORKERS`（2）× (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`) + worker 10 + migrate 1，要低於 100。

### Migration

```bash
docker compose exec -T app python -m apps.api.migrations
```

- 啟動時不會自動跑。每支 `.sql` 一個交易，只有往前。
- `schema_migrations` 記 checksum，**已套用的 `.sql` 不得修改**。advisory lock `820020` 互斥。
- 改 schema：新增 `NNNN_<name>.sql` → 跑 migration → 同步 `rows.py` → 更新本文件。

### 慣例

- 主鍵 UUID v7，由 `apps/api/ids.py::uuid7()` 產生（例外：`run_events.seq` bigserial；一對一的表以 `agent_id` 為主鍵）。
- 時間 `timestamptz`（UTC）；狀態 `varchar + CHECK`；彈性結構 `jsonb`；金額 `numeric(12,6)`。

### 表

| 表 | 一列代表 | 重點 |
|---|---|---|
| `agents` | Agent | `name` 唯一。`deleted_at` 遺留欄位，現在是硬刪 |
| `agent_versions` | 一個版本（一個 digest） | `status` ∈ BUILDING / PUBLISHED / FAILED / SUPERSEDED。`image_ref` 原樣儲存、digest 永不改寫。partial unique：每個 `(agent_id, semver)` 最多一筆 PUBLISHED、一筆 BUILDING。`entrypoints` 是 REST / MCP / OpenAPI 的唯一 schema 來源 |
| `runs` | 一次呼叫 | `status` 六種；`trigger` ∈ API / MCP；`timeout_seconds` 1–900；`agent_version_id` 建立時決定；`container_id` / `api_key_id` / `mcp_key_id` 可 NULL；`result_received_at` 做結果冪等 |
| `run_events` | Run 的事件 | `seq` 就是 SSE id。UNIQUE `(run_id, event_id)`；UNIQUE `(run_id) WHERE terminal`；`published_at` NULL = 尚未 XADD |
| `llm_calls` | 一次 LLM 代打（含失敗） | `cost_usd` 呼叫當下算好；`credential_source` ∈ PLATFORM / AGENT；歸戶 join `runs.api_key_id` |
| `egress_calls` | 一次外部 HTTP 或寄信（含被擋） | `kind` ∈ HTTP / EMAIL；`path` 不含 query；索引 `(agent_id, created_at DESC)`、`(run_id)` 給上限計數 |
| `agent_llm_credentials` | Agent 的 LLM 金鑰 | Fernet `ciphertext` + `last_four` + `base_url` + `default_model` |
| `agent_api_keys` | 下游 REST key | `key_hash`（SHA-256，唯一）+ `key_prefix`；`revoked_at` 軟撤銷 |
| `mcp_keys` / `mcp_key_agents` | MCP key / 勾選 | 同上存雜湊；勾選 `(mcp_key_id, agent_id)`，改勾選 = 同交易刪光再插入 |
| `agent_containers` | 常駐容器 | `status` ∈ STARTING / READY / UNHEALTHY / STOPPED / FAILED；`inflight_runs`；`restart_window_started_at` + `restarts_in_window`（FAILED 且 ≥ 3 = 已放棄）；`last_died_at` / `last_death_reason` |
| `agent_settings` | 設定 | `config jsonb`，明文 |
| `agent_secrets` | 機密 | `(agent_id, name)`，Fernet |
| `agent_egress_rules` | 白名單 | UNIQUE `(agent_id, host)`；`auth_secret_name` / `auth_header` 成對 |
| `agent_email_sender` | 寄件者 | port ∈ 25 / 465 / 587；收件人 1–10；沒有密碼欄位 |

Fernet 密文（`agent_secrets`、`agent_llm_credentials`）用 `ZENTRA_SECRET_KEY`，換掉就全部解不開；key 類只存雜湊。

### 外鍵

| 子 → 父 | ON DELETE |
|---|---|
| 所有 `agent_*.agent_id`、`egress_calls.agent_id`、`mcp_key_agents.*` | CASCADE |
| `runs.agent_id`、`runs.agent_version_id`、`agent_containers.agent_version_id` | NO ACTION |
| `runs.api_key_id` / `mcp_key_id` / `container_id`、`egress_calls.rule_id` | SET NULL |
| `run_events` / `llm_calls` / `egress_calls` 的 `run_id` | CASCADE |

所以刪 Agent（`services/agents.py::delete_agent`）要由下往上：稽核、規則、寄件者、機密、設定 → `llm_calls` / `run_events` / `runs` → `agent_containers` → `agent_versions` → `agents`。

### 維運

- `postgres`（`pgvector/pgvector:pg16`），volume `pgdata`。備份 `pgdata` + `registrydata`，`ZENTRA_SECRET_KEY` 另存。
- 從主機連可能連錯庫，用 `docker compose exec postgres psql -U zentra -d zentra`。

## Redis

`redis:7-alpine`，db 0，只掛 `zentra-net`。key 格式定義在對應的函式裡，不在呼叫端手拼。

| Key | 型別 | 有效期 | 用途 | 定義在 |
|---|---|---|---|---|
| `run:{run_id}` | Stream | 3600 秒 | 事件通知（真相在 `run_events`） | `services/events.py` |
| `run_token:{token}` / `run_tokens:{run_id}` | String / Set | = `timeout_seconds` | run token → run_id；撤銷索引 | `services/runtoken.py` |
| `validation_token:{token}` / `validation_tokens:{version_id}` | String / Set | ≤ 600 秒 | 驗證 token | `services/validationtoken.py` |
| `container_token:{token}` / `container_tokens:{agent_id}` / `container_tokens_of:{uuid}` | String / Set / Set | 30 天 | container token；撤銷以容器為單位（`container_tokens_of`） | `services/containertoken.py` |
| `agent_work:{agent_id}:{version_id}` | List | 無 | 常駐容器工作佇列（一個版本一條，LPUSH / BRPOP） | `services/work.py` |
| `sandbox:inflight` | Sorted Set | 2400 秒 | 一次性沙盒併發租約（1200 秒過期，上限 `SANDBOX_MAX_CONCURRENT`） | `apps/sandbox/manager.py` |
| `q.run` / `q.build` | List | 無 | Celery 佇列 | `apps/worker/celery_app.py` |
| `unacked*`、`_kombu.binding.*`、`celery-task-meta-*` | — | meta 1 天 | Celery / kombu 內部 | |

- 事件：先 commit `run_events` 再 `XADD`；SSE 從 DB 依 `seq` 讀，`XREAD BLOCK 1000` 只用來被叫醒。
- 工作項：`run_id`、`entrypoint`、`input`、`manifest`、`default_model`、`config`、`secret_names`，用 `ensure_ascii=False`。**commit 之後才 LPUSH。** 版本最後一個容器停止時 `DEL`。
- `maxmemory-policy noeviction`；AOF 關、RDB 預設；`/data` 是匿名 volume（`docker compose down` 後會換新的）。不需要備份。
- 除錯：`docker compose exec redis redis-cli`，用 `SCAN` 不要 `KEYS *`。
