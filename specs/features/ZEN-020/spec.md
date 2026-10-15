# Feature Spec：ZEN-020 拿掉 SQLAlchemy 與 Alembic

| 欄位 | 值 |
|---|---|
| 狀態 | `DONE`（2026-10-15 驗收：zen019 + zen020 270 passed；全套件不比 S0 差；空庫 runner 冪等且 schema 與 0008 只差版本表） |
| 對齊 | SPEC `v0.6.0`、api-contract `v0.12.1`（**不變**） |
| 決策 | [ADR-0015](../../adr/ADR-0015-plain-sql-no-orm.md) |

**這不是效能修正。** 連線規則只看 ADR-0015 的 D1–D7，不要再拆階段實作。

## 已做完

`app` 用 asyncpg、`worker` 用 psycopg 3；`models.py` 與 Alembic 已刪；`rows.py` + 8 支 `.sql` + `python -m apps.api.migrations` + `scripts/adopt-legacy-schema.sh` 已在 repo。契約與 schema 沒改。

## 還剩（只做這些）

1. 重建 `app` 與 `worker` 映像。
2. 開發庫：`bash scripts/adopt-legacy-schema.sh --dry-run`，確認後再跑一次，然後 `make migrate`。
3. 跑 `tests/zen020`、`tests/zen019`，以及會打 live HTTP 的 suite（映像新了才有意義）。
4. 開發庫若仍缺 `builtin-echo`，失敗與以前相同，**不要為此改產品**。
5. `make clean-test-data`。`make smoke` 在 fixture 回來之前會 404，如實回報。

## 做到什麼算完

- `rg -n -i 'sqlalchemy|alembic' apps packages pyproject.toml` 為空。
- 空庫跑 runner 後有 `builtin-echo`，且 schema 與 `tests/zen020/fixtures/schema_0008.sql` 只差版本表。
- 等 LLM／egress／SMTP／sleep 時，`zentra-app` 沒有 `idle in transaction`。
- `FOR UPDATE`／advisory lock 在交易外呼叫會 raise。
- 全套件不比 `tests/zen020/fixtures/s0_summary.txt` 差。

## 不做

不改契約、不改 schema、不刪 `alembic_version`（留給 `0009`）、不加依賴、不宣稱變快。
