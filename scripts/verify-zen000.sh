#!/usr/bin/env bash
# ZEN-000 驗收閘門。任何人都能重跑，不需要本機裝 Python 相依。
#   前置：docker compose up -d && make migrate
#   用法：bash scripts/verify-zen000.sh
set -uo pipefail

API=${API:-http://localhost:8000}
AGENT=01930000-0000-7000-8000-000000000001
RUNTIME_IMAGE=${SANDBOX_IMAGE:-zentra/agent-runtime:dev}
SBX_ARGS=(--network zentra-sandbox-net --cap-drop ALL --security-opt no-new-privileges
          --user 65534:65534 --read-only --tmpfs /workspace:rw,size=512m,mode=1777
          --tmpfs /tmp:rw,size=64m,mode=1777 --pids-limit 256 --memory 2g --memory-swap 2g)

pass=0; fail=0; skip=0
ok()   { echo "  ✅ $1"; pass=$((pass+1)); }
no()   { echo "  ❌ $1"; fail=$((fail+1)); }
skipd(){ echo "  ⏭  SKIPPED（本架構不提供）：$1"; skip=$((skip+1)); }
chk()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1（期望 $3，實得 $2）"; fi; }
wait_sandbox_gone() {
  local run_id=$1 ids count
  for ((attempt=0; attempt<40; attempt++)); do
    ids=$(docker ps -aq --filter label=zentra.role=sandbox --filter "label=zentra.run_id=$run_id") \
      || { echo docker-error; return; }
    [ -z "$ids" ] && { echo 0; return; }
    sleep 0.25
  done
  count=$(printf '%s\n' "$ids" | wc -l | tr -d ' ')
  echo "$count"
}

echo "═══ A1/A3/A4 貫穿路徑 ═══"
RESP=$(curl -s -X POST "$API/v1/agents/$AGENT/runs" -H 'content-type: application/json' \
       -d '{"input":{"question":"verify"}}')
RID=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("run_id",""))')
[ -n "$RID" ] && ok "POST /v1/agents/{id}/runs 回 run_id" || { no "建立 Run 失敗：$RESP"; exit 1; }

EVENTS=$(curl -sN --max-time 60 "$API/v1/runs/$RID/events" | grep '^event:' | awk '{print $2}')
for want in run.status node.start node.end; do
  echo "$EVENTS" | grep -q "^$want$" && ok "SSE 有 $want 事件" || no "SSE 缺 $want 事件"
done
# A3：平台 compile 注入 hook —— 使用者只交 StateGraph，卻拿得到逐節點事件
NODES=$(echo "$EVENTS" | grep -c '^node.start$')
chk "A3 平台注入事件 hook（2 個節點）" "$NODES" "2"
ENDS=$(echo "$EVENTS" | grep -c '^node.end$')
chk "A3 每個節點都有結束事件" "$ENDS" "2"

FINAL=$(curl -s "$API/v1/runs/$RID")
chk "Run 終局狀態" "$(echo "$FINAL" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')" "SUCCEEDED"
chk "output 對齊 entrypoint schema" "$(echo "$FINAL" | python3 -c 'import sys,json;print(json.load(sys.stdin)["output"]["echo"])')" "verify"

echo "═══ A1 沙盒生命週期 ═══"
chk "首個 Run 沙盒零殘留" "$(wait_sandbox_gone "$RID")" "0"
R2=$(curl -s -X POST "$API/v1/agents/$AGENT/runs" -H 'content-type: application/json' -d '{"input":{"question":"second"}}' \
     | python3 -c 'import sys,json;print(json.load(sys.stdin)["run_id"])')
curl -sN --max-time 60 "$API/v1/runs/$R2/events" >/dev/null
IDS=$(docker compose exec -T postgres psql -U zentra -d zentra -t -A \
      -c "SELECT count(DISTINCT sandbox_id) FROM runs WHERE id IN ('$RID','$R2');")
chk "沙盒不重用（兩個不同容器）" "$IDS" "2"
chk "第二個 Run 沙盒零殘留" "$(wait_sandbox_gone "$R2")" "0"

echo "═══ P1 使用者程式碼不在平台行程內 ═══"
if grep -rqn "^from apps.sandbox.runtime\|^import apps.sandbox.runtime" apps/worker/ 2>/dev/null
then no "worker 原始碼 import 了 runtime"; else ok "worker 原始碼不 import runtime"; fi
docker compose exec -T worker sh -c 'ls apps/sandbox/runtime' >/dev/null 2>&1 \
  && no "worker 映像內含 runtime 程式碼" || ok "worker 映像內不含 runtime 程式碼"
docker compose exec -T worker python -c "import langgraph" >/dev/null 2>&1 \
  && no "worker 映像裝了 langgraph" || ok "worker 映像未裝 langgraph"

echo "═══ P4 憑證邊界 ═══"
chk "無 run token 存取 Internal Gateway" \
    "$(curl -s -o /dev/null -w '%{http_code}' "$API/internal/runs/$RID/bootstrap")" "401"

echo "═══ L1 網路隔離 ═══"
NET=$(docker run --rm "${SBX_ARGS[@]}" "$RUNTIME_IMAGE" python -c "
import socket
def p(h,q):
    try: socket.create_connection((h,q),timeout=3); return 'OPEN'
    except Exception: return 'BLOCKED'
print(p('postgres',5432),p('redis',6379),p('1.1.1.1',53),p('169.254.169.254',80),p('app',8000))")
read -r PG RD NET1 META APIR <<<"$NET"
chk "沙盒 → postgres 不通" "$PG" "BLOCKED"
chk "沙盒 → redis 不通"    "$RD" "BLOCKED"
chk "沙盒 → 外網不通"      "$NET1" "BLOCKED"
chk "沙盒 → 雲端 metadata 不通" "$META" "BLOCKED"
chk "沙盒 → api 通（唯一出口）" "$APIR" "OPEN"

echo "═══ L2/L3/L4 隔離 ═══"
ISO=$(docker run --rm "${SBX_ARGS[@]}" "$RUNTIME_IMAGE" python -c "
import os, subprocess
def w(p):
    try: open(os.path.join(p,'probe'),'w').close(); return 'WRITABLE'
    except OSError: return 'BLOCKED'
print(os.getuid(), w('/'), w('/usr/lib'), w('/workspace'),
      'BLOCKED' if subprocess.run(['mount','-t','proc','proc','/workspace'],capture_output=True).returncode else 'OPEN')")
read -r UID_ ROOTW LIBW WSW MNT <<<"$ISO"
chk "L2 非 root 執行" "$UID_" "65534"
chk "L2 mount 被擋"   "$MNT" "BLOCKED"
chk "L4 / 唯讀"       "$ROOTW" "BLOCKED"
chk "L4 /usr/lib 唯讀" "$LIBW" "BLOCKED"
chk "L4 /workspace 可寫" "$WSW" "WRITABLE"
skipd "L2 核心隔離（gVisor）—— capabilities() 回報 l2_kernel_isolation=false，見 ADR-0005"
skipd "容器逃逸測試 —— 本架構不防這一類，只適用於信任的程式碼"

echo "═══ 契約錯誤路徑 ═══"
chk "未知 agent → 404" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/v1/agents/01930000-0000-7000-8000-000000000099/runs" -H 'content-type: application/json' -d '{"input":{"question":"x"}}')" "404"
chk "input 不符 schema → 422" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/v1/agents/$AGENT/runs" -H 'content-type: application/json' -d '{"input":{}}')" "422"

echo
echo "═══════════════════════════════════════"
echo "  通過 $pass ／ 失敗 $fail ／ 跳過 $skip"
[ "$skip" -gt 0 ] && echo "  ⚠ 跳過的項目是 skipped，**不是 passed**（ADR-0005）"
echo "═══════════════════════════════════════"
[ "$fail" -eq 0 ]
