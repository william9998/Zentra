#!/usr/bin/env bash
# 清掉平台上的測試 Agent 與執行紀錄。
#
# **開發結束前必跑**（AGENTS.md §開發資料的清理）。測試套件與手動驗證都會在平台上
# 建立 Agent 與 Run，而且不會自己收 —— 不清的話 Console 會被塞滿到看不出哪個是真的。
#
#   bash scripts/clean-test-data.sh            # 清掉全部，只留內建 fixture
#   bash scripts/clean-test-data.sh --dry-run  # 只看會刪什麼
#
# **內建 fixture 一定留著**：`builtin-echo`（01930000-…-0001）是
# scripts/verify-zen000.sh 與 9 個測試檔的承重結構，刪了冒煙測試直接掛（見 ZEN-010）。
set -euo pipefail

BUILTIN='01930000-0000-7000-8000-000000000001'
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

psql() { docker compose exec -T postgres psql -U zentra -d zentra "$@"; }

echo "=== 目前 ==="
psql -c "
SELECT
  (SELECT count(*) FROM agents)                AS agents,
  (SELECT count(*) FROM agent_versions)        AS versions,
  (SELECT count(*) FROM agent_llm_credentials) AS credentials,
  (SELECT count(*) FROM runs)                  AS runs,
  (SELECT count(*) FROM run_events)            AS events,
  (SELECT count(*) FROM llm_calls)             AS llm_calls,
  (SELECT count(*) FROM agent_containers)      AS containers,
  (SELECT count(*) FROM agent_api_keys)        AS api_keys;"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "=== --dry-run：會刪掉這些 Agent（保留 builtin-echo）==="
  psql -c "SELECT name, created_at FROM agents WHERE id <> '$BUILTIN' ORDER BY created_at LIMIT 40;"
  exit 0
fi

# 常駐容器要先真的停掉再刪列（ZEN-015）。**順序不能反** ——
# 先刪 DB 的話那些容器就沒人認得了，它們會繼續佔記憶體直到有人手動 docker ps 發現。
STALE=$(psql -t -A -c "SELECT id FROM agent_containers WHERE agent_id <> '$BUILTIN';")
if [[ -n "$STALE" ]]; then
  echo "=== 停掉測試 Agent 的常駐容器 ==="
  while read -r cid; do
    [[ -z "$cid" ]] && continue
    docker rm -f "zentra-serve-$cid" >/dev/null 2>&1 && echo "  已移除 zentra-serve-$cid" || true
  done <<< "$STALE"
fi

# runs → run_events / llm_calls，agents → agent_llm_credentials / agent_api_keys，
# 都靠 FK CASCADE。agent_containers 例外：它擋著 agent_versions（FK 沒有 CASCADE，
# 因為「容器還在跑」本來就該擋住刪版本），所以自己先刪。
psql <<SQL
BEGIN;
DELETE FROM runs;
-- api key **全刪，含 builtin-echo 的**：那個 fixture 不會有真的下游應用在用，
-- 掛在它身上的每一把都是開發時測出來的。留著等於下次開發要從一串測試 key 裡找。
DELETE FROM agent_api_keys;
DELETE FROM agent_containers WHERE agent_id <> '$BUILTIN';
DELETE FROM agent_versions   WHERE agent_id <> '$BUILTIN';
DELETE FROM agents           WHERE id       <> '$BUILTIN';
COMMIT;
SQL

echo "=== 清完 ==="
psql -c "
SELECT
  (SELECT count(*) FROM agents)                AS agents,
  (SELECT count(*) FROM agent_versions)        AS versions,
  (SELECT count(*) FROM agent_llm_credentials) AS credentials,
  (SELECT count(*) FROM runs)                  AS runs,
  (SELECT count(*) FROM run_events)            AS events,
  (SELECT count(*) FROM llm_calls)             AS llm_calls,
  (SELECT count(*) FROM agent_containers)      AS containers,
  (SELECT count(*) FROM agent_api_keys)        AS api_keys;"
echo "留下的是 builtin-echo —— 冒煙測試需要它（ZEN-010）。"
