#!/usr/bin/env bash
# 準備平台的測試 Agent `probe-agent`（examples/probe-agent），印出它的 agent_id。
#
#   前置：make up
#   用法：AGENT=$(bash scripts/fixture.sh)      # 進度在 stderr，stdout 只有 agent_id
#
# 走的是使用者的正式路徑：build（FROM zentra/agent-base）→ push 到內部 Registry（push 就是註冊）
# → 平台驗證 → PUBLISHED。重跑是安全的：image 沒變時 digest 相同，平台直接回現役那筆、
# 不重新驗證；agent.py 改了則是同版號取代，驗證通過才切過去（specs/agent-registry.md）。
set -euo pipefail

API=${API:-http://localhost:8000}
REGISTRY=${REGISTRY:-localhost:6000}
NAME=probe-agent
VERSION=1.1.0
REF="${REGISTRY}/${NAME}:${VERSION}"
WAIT_SECONDS=${WAIT_SECONDS:-180}

say() { echo "$@" >&2; }

say "▸ build zentra/agent-base:1.0"
docker build -q -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile . >/dev/null
say "▸ build ${REF}"
docker build -q -t "${REF}" examples/probe-agent >/dev/null
say "▸ push ${REF}（push 就是註冊）"
docker push -q "${REF}" >/dev/null

# push 之後才有 RepoDigests。同一個 image 可能被推到多個 repo，挑這個 repo 的那筆。
DIGEST=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${REF}" \
         | grep "^${REGISTRY}/${NAME}@" | head -1 | cut -d@ -f2)
[ -n "${DIGEST}" ] || { say "✖ 取不到 ${REF} 的 digest"; exit 1; }
say "  digest ${DIGEST}"

# 平台收到 registry 的通知才會建 Agent、註冊版本，所以要輪詢。
say "▸ 等 ${NAME} ${VERSION} 發布（最多 ${WAIT_SECONDS}s）"
deadline=$((SECONDS + WAIT_SECONDS))
AGENT=""
while (( SECONDS < deadline )); do
  if [ -z "${AGENT}" ]; then
    # 沒有依名稱查的端點，只能翻頁找（每頁上限 100）
    AGENT=$(python3 - "$API" "${NAME}" <<'PY'
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
) || AGENT=""
  fi
  if [ -n "${AGENT}" ]; then
    STATE=$(curl -sf "$API/v1/agents/${AGENT}/versions/${VERSION}" | python3 -c "
import sys, json
v = json.load(sys.stdin)
print(v['status'], v['artifact']['digest'], (v.get('error') or {}).get('message', '').replace(chr(10), ' ')[:300])") || STATE=""
    read -r STATUS GOT MESSAGE <<<"$STATE"
    if [ "${GOT:-}" = "${DIGEST}" ]; then
      case "${STATUS:-}" in
        PUBLISHED) say "✔ ${NAME} ${VERSION} 已發布（agent_id=${AGENT}）"; echo "${AGENT}"; exit 0 ;;
        FAILED)    say "✖ 驗證失敗：${MESSAGE}"; exit 1 ;;
      esac
    fi
  fi
  sleep 2
done
say "✖ ${WAIT_SECONDS}s 內沒有發布。看 app 的 log（make logs S=app）：registry 通知送不到時，重跑一次這支腳本會再 push 一次。"
exit 1
