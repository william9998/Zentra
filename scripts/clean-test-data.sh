#!/usr/bin/env bash
# 清掉平台上的測試 Agent 與執行紀錄。
#
# 這次有在平台上建 Agent / Run 才需要跑（CLAUDE.md §驗證）。測試套件與手動驗證都會在平台上
# 建立 Agent 與 Run，而且不會自己收 —— 不清的話 Console 會被塞滿到看不出哪個是真的。
#
#   bash scripts/clean-test-data.sh            # 清掉測試套件造的 Agent
#   bash scripts/clean-test-data.sh --dry-run  # 只看會刪什麼
#   bash scripts/clean-test-data.sh --all      # 連手動建的也清（只留內建 fixture）
#
# **預設只刪測試夾具的名字**（下面的 TEST_PREFIXES）。
# 這不是保守，是因為這支腳本原本會把**使用者自己建的 Agent 一起刪掉** ——
# 規則說的是「清掉自己造的測試資料」，不是「清空這個人的平台」。
# 實際踩過：跑完測試要收尾時，差點刪掉使用者手動建的 stock-agent。
#
# 手動驗證時建的 Agent 不會符合這些前綴，所以**收尾時請用 `--all`，或自己刪掉它們**。
#
# **內建 fixture 一定留著**：`builtin-echo`（01930000-…-0001）是
# scripts/verify-zen000.sh 與 9 個測試檔的承重結構，刪了冒煙測試直接掛（見 ZEN-010）。
set -euo pipefail

BUILTIN='01930000-0000-7000-8000-000000000001'

# 測試套件產生的 Agent 名稱前綴。新增測試夾具時要一起加，否則它會留在平台上。
TEST_PREFIXES="zen008-%|zen015-%|cred-unit-%|key-unit-%|usage-%|qa-%|slow-agent%|console-cred-e2e-%|console-keys-e2e-%"

DRY_RUN=0
CLEAN_ALL=0
case "${1:-}" in
  --dry-run) DRY_RUN=1 ;;
  --all)     CLEAN_ALL=1 ;;
esac

if [[ $CLEAN_ALL -eq 1 ]]; then
  WHERE="id <> '$BUILTIN'"
  SCOPE='全部（只留 builtin-echo）'
else
  WHERE="id <> '$BUILTIN' AND name SIMILAR TO '($TEST_PREFIXES)'"
  SCOPE='測試套件建的（手動建的請用 --all）'
fi

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
  echo "=== --dry-run：會刪掉這些 Agent（範圍：${SCOPE}）==="
  psql -c "SELECT name, created_at FROM agents WHERE $WHERE ORDER BY created_at LIMIT 40;"
  echo "=== 會留下的 ==="
  psql -c "SELECT name, created_at FROM agents WHERE NOT ($WHERE) ORDER BY created_at LIMIT 20;"
  exit 0
fi

# 常駐容器要先真的停掉再刪列（ZEN-015）。**順序不能反** ——
# 先刪 DB 的話那些容器就沒人認得了，它們會繼續佔記憶體直到有人手動 docker ps 發現。
STALE=$(psql -t -A -c "SELECT c.id FROM agent_containers c WHERE c.agent_id IN (SELECT id FROM agents WHERE $WHERE);")
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
DELETE FROM runs WHERE agent_id IN (SELECT id FROM agents WHERE $WHERE)
                    OR agent_id = '$BUILTIN';
-- builtin-echo 的 api key 也全刪：那個 fixture 不會有真的下游應用在用，
-- 掛在它身上的每一把都是開發時測出來的。
DELETE FROM agent_api_keys WHERE agent_id = '$BUILTIN'
                              OR agent_id IN (SELECT id FROM agents WHERE $WHERE);
DELETE FROM agent_containers WHERE agent_id IN (SELECT id FROM agents WHERE $WHERE);
DELETE FROM agent_versions   WHERE agent_id IN (SELECT id FROM agents WHERE $WHERE);
DELETE FROM agents           WHERE $WHERE;
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
echo "刪除範圍：${SCOPE}。builtin-echo 一定留著 —— 冒煙測試需要它（ZEN-010）。"
