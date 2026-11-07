#!/usr/bin/env bash
# 部署 stock-suite 的五個 Agent：build → push（= 註冊）→ 等發布 → 設 egress 規則。
#
#   前置：make up（在 repo 根目錄）
#   用法：examples/stock-suite/deploy.sh                    # 全部
#         examples/stock-suite/deploy.sh stock-technical    # 只做一個
#         FINMIND_TOKEN='Bearer xxx' examples/stock-suite/deploy.sh   # 順便設 FinMind token
#
# 重跑是安全的：image 沒變 → digest 相同，平台直接回現役版本；規則已存在就跳過。
# 最後印出 名稱<TAB>agent_id，到 Console 的 MCP 頁把這五個勾進同一把 key。
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
API=${API:-http://localhost:8000}
REGISTRY=${REGISTRY:-localhost:6000}
VERSION=${VERSION:-1.0.0}
WAIT_SECONDS=${WAIT_SECONDS:-240}
ALL=(stock-news stock-chart stock-technical stock-fundamental portfolio-advisor)
[ $# -gt 0 ] && AGENTS=("$@") || AGENTS=("${ALL[@]}")

say() { echo "$@" >&2; }

# 每個 Agent 需要的 egress 規則：host|path_prefix|說明
rules_for() {
  local yahoo_chart="query1.finance.yahoo.com|/v8/finance/chart/|Yahoo 日線（行情、技術面、K 線、匯率）"
  local yahoo_ts="query2.finance.yahoo.com|/ws/fundamentals-timeseries/|Yahoo 美股財報"
  local finmind="api.finmindtrade.com|/api/v4/data|FinMind 台股財報、月營收、本益比、股利"
  case "$1" in
    stock-news)        ;;   # 新聞走模型的網路搜尋，不需要 egress
    stock-chart|stock-technical) echo "$yahoo_chart" ;;
    stock-fundamental) echo "$finmind"; echo "$yahoo_ts" ;;
    portfolio-advisor) echo "$yahoo_chart"; echo "$finmind"; echo "$yahoo_ts" ;;
    *) say "✖ 不認得的 Agent：$1（可用：${ALL[*]}）"; exit 1 ;;
  esac
}

find_agent() {
  python3 - "$API" "$1" <<'PY'
import json, sys, urllib.parse, urllib.request
api, name = sys.argv[1], sys.argv[2]
cursor = None
while True:
    query = {"limit": 100, **({"cursor": cursor} if cursor else {})}
    with urllib.request.urlopen(f"{api}/v1/agents?{urllib.parse.urlencode(query)}") as resp:
        page = json.load(resp)
    hit = next((a["agent_id"] for a in page["items"] if a["name"] == name), None)
    if hit or not page.get("next_cursor"):
        print(hit or "")
        break
    cursor = page["next_cursor"]
PY
}

ensure_rules() {   # $1 agent_id  $2 name
  local existing
  existing=$(curl -sf "$API/v1/agents/$1/egress-rules" | python3 -c "
import json, sys
body = json.load(sys.stdin)
items = body.get('items', body) if isinstance(body, dict) else body
print(' '.join(r['host'] for r in items))")
  if [ "$2" = "stock-fundamental" ] || [ "$2" = "portfolio-advisor" ]; then
    if [ -n "${FINMIND_TOKEN:-}" ]; then
      curl -sf -X PUT "$API/v1/agents/$1/secrets/FINMIND_TOKEN" -H 'content-type: application/json' \
        -d "$(python3 -c 'import json,os; print(json.dumps({"value": os.environ["FINMIND_TOKEN"]}))')" >/dev/null
      say "  ✔ 機密 FINMIND_TOKEN 已設定"
    fi
  fi
  while IFS='|' read -r host prefix desc; do
    [ -n "$host" ] || continue
    if [[ " $existing " == *" $host "* ]]; then
      say "  · 規則已存在：$host"
      continue
    fi
    local auth='"auth_secret_name": null, "auth_header": null'
    if [ "$host" = "api.finmindtrade.com" ] && [ -n "${FINMIND_TOKEN:-}" ]; then
      auth='"auth_secret_name": "FINMIND_TOKEN", "auth_header": "Authorization"'
    fi
    curl -sf -X POST "$API/v1/agents/$1/egress-rules" -H 'content-type: application/json' \
      -d "{\"host\": \"$host\", \"methods\": [\"GET\"], \"path_prefix\": \"$prefix\", $auth, \"description\": \"$desc\"}" \
      >/dev/null || { say "  ✖ 新增規則失敗：$host"; exit 1; }
    say "  ✔ 新增規則：$host $prefix"
  done < <(rules_for "$2")
}

say "▸ build zentra/agent-base:1.0"
docker build -q -t zentra/agent-base:1.0 -f "$ROOT/docker/agent-base.Dockerfile" "$ROOT" >/dev/null

for NAME in "${AGENTS[@]}"; do
  rules_for "$NAME" >/dev/null
  REF="${REGISTRY}/${NAME}:${VERSION}"
  say "▸ ${NAME}：build、push"
  docker build -q --build-arg AGENT="$NAME" -t "$REF" "$HERE" >/dev/null
  docker push -q "$REF" >/dev/null
  DIGEST=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$REF" \
           | grep "^${REGISTRY}/${NAME}@" | head -1 | cut -d@ -f2)
  [ -n "$DIGEST" ] || { say "✖ 取不到 $REF 的 digest"; exit 1; }

  say "  等發布（最多 ${WAIT_SECONDS}s）"
  deadline=$((SECONDS + WAIT_SECONDS))
  AGENT=""
  STATUS=""
  while (( SECONDS < deadline )); do
    [ -n "$AGENT" ] || AGENT=$(find_agent "$NAME" || true)
    if [ -n "$AGENT" ]; then
      read -r STATUS GOT MESSAGE < <(curl -sf "$API/v1/agents/$AGENT/versions/$VERSION" | python3 -c "
import sys, json
v = json.load(sys.stdin)
print(v['status'], v['artifact']['digest'], (v.get('error') or {}).get('message', '').replace(chr(10), ' ')[:300])" || echo "- - -")
      if [ "$GOT" = "$DIGEST" ]; then
        [ "$STATUS" = PUBLISHED ] && break
        [ "$STATUS" = FAILED ] && { say "  ✖ 驗證失敗：$MESSAGE"; exit 1; }
      fi
    fi
    sleep 2
  done
  [ "$STATUS" = PUBLISHED ] || { say "  ✖ ${WAIT_SECONDS}s 內沒有發布（make logs S=app）"; exit 1; }
  say "  ✔ 已發布 agent_id=$AGENT"
  ensure_rules "$AGENT" "$NAME"
  printf '%s\t%s\n' "$NAME" "$AGENT"
done
