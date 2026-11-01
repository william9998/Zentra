#!/usr/bin/env bash
# 冒煙測試（ZEN-000 驗收閘門）。任何人都能重跑，不需要本機裝 Python 相依。
#   前置：make up
#   用法：bash scripts/verify-zen000.sh（= make smoke）
#
# 打的是 examples/probe-agent，先用 scripts/fixture.sh 走**使用者的正式路徑**註冊好
# （push → 驗證 → 發布），所以 Run 走的是常駐容器（specs/dispatch.md 路徑 A）。
# 隔離探測直接 exec 進**平台開出來的那個常駐容器**：量的是平台實際給的設定，
# 不是腳本自己照抄一份 docker run 參數（抄的那份和平台不一致時，測試照樣會過）。
set -uo pipefail

API=${API:-http://localhost:8000}

pass=0; fail=0; skip=0
ok()   { echo "  ✅ $1"; pass=$((pass+1)); }
no()   { echo "  ❌ $1"; fail=$((fail+1)); }
skipd(){ echo "  ⏭  SKIPPED（本架構不提供）：$1"; skip=$((skip+1)); }
chk()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1（期望 $3，實得 $2）"; fi; }
sql()  { docker compose exec -T postgres psql -U zentra -d zentra -t -A -c "$1"; }
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }

echo "═══ 準備測試 Agent ═══"
AGENT=$(bash scripts/fixture.sh) || { no "probe-agent 沒有發布（看上面的輸出）"; exit 1; }
ok "probe-agent 走 push → 驗證 → 發布"

echo "═══ A1/A3/A4 貫穿路徑 ═══"
RESP=$(curl -s -X POST "$API/v1/agents/$AGENT/runs" -H 'content-type: application/json' \
       -d '{"input":{"question":"verify"}}')
RID=$(echo "$RESP" | json 'd.get("run_id","")')
[ -n "$RID" ] && ok "POST /v1/agents/{id}/runs 回 run_id" || { no "建立 Run 失敗：$RESP"; exit 1; }

SSE=$(curl -sN --max-time 60 "$API/v1/runs/$RID/events")
EVENTS=$(echo "$SSE" | grep '^event:' | awk '{print $2}')
for want in run.status node.start node.end; do
  echo "$EVENTS" | grep -q "^$want$" && ok "SSE 有 $want 事件" || no "SSE 缺 $want 事件"
done
# A3：平台 compile 時注入 hook —— 使用者只交 StateGraph，卻拿得到逐節點事件。
# probe-agent 的 simulate 模式固定走 classify → wait → reply_short。
# 不數總數：SDK 目前連條件分支的判斷函式也回報成節點（BOARD SDK-ROUTER-EVENTS）。
nodes_of() {  # 列出某種事件（node.start / node.end）裡出現過的節點名，排序去重
  echo "$SSE" | awk -v want="event: $1" '$0 == want {getline; print}' \
    | sed -n 's/^data: //p' | python3 -c "
import sys, json
print(' '.join(sorted({json.loads(line)['node'] for line in sys.stdin if line.strip()})))"
}
for kind in node.start node.end; do
  GOT=$(nodes_of "$kind")
  MISSING=$(for n in classify wait reply_short; do [[ " $GOT " == *" $n "* ]] || echo -n "$n "; done)
  chk "A3 $kind 涵蓋 classify / wait / reply_short" "${MISSING:-none}" "none"
done

FINAL=$(curl -s "$API/v1/runs/$RID")
chk "Run 終局狀態" "$(echo "$FINAL" | json 'd["status"]')" "SUCCEEDED"
chk "output 對齊 entrypoint schema" "$(echo "$FINAL" | json 'd["output"]["echo"]')" "verify"

echo "═══ 常駐容器（路徑 A）═══"
read -r CID SBX <<<"$(sql "SELECT container_id, coalesce(sandbox_id, '-') FROM runs WHERE id = '$RID';" | tr '|' ' ')"
[ -n "$CID" ] && ok "Run 跑在常駐容器裡" || no "Run 沒有 container_id"
chk "沒有另外開一次性沙盒" "$SBX" "-"
R2=$(curl -s -X POST "$API/v1/agents/$AGENT/runs:invoke" -H 'content-type: application/json' \
     -d '{"input":{"question":"second"}}' | json 'd["run_id"]')
chk "第二筆 Run 用同一個容器（一個版本一個容器）" \
    "$(sql "SELECT container_id FROM runs WHERE id = '$R2';")" "$CID"
chk "容器狀態 READY" "$(sql "SELECT status FROM agent_containers WHERE id = '$CID';")" "READY"
# 結果是容器回報後再由 finalize 任務收尾，inflight 要稍等才會減回去
for _ in $(seq 1 20); do
  INFLIGHT=$(sql "SELECT inflight_runs FROM agent_containers WHERE id = '$CID';")
  [ "$INFLIGHT" = "0" ] && break; sleep 0.5
done
chk "跑完 inflight 歸零" "$INFLIGHT" "0"

SERVE="zentra-serve-$CID"
docker inspect "$SERVE" >/dev/null 2>&1 || { no "找不到常駐容器 $SERVE，後面的隔離探測無法進行"; exit 1; }
inspect() { docker inspect --format "$1" "$SERVE"; }
in_box()  { docker exec "$SERVE" python -c "$1"; }

echo "═══ P1 使用者程式碼不在平台行程內 ═══"
if grep -rqnE '^\s*(from|import) (zentra_sdk|langgraph)' apps/worker apps/api 2>/dev/null
then no "平台原始碼 import 了 SDK 或 langgraph"; else ok "平台原始碼不 import SDK / langgraph"; fi
docker compose exec -T worker python -c "import zentra_sdk" >/dev/null 2>&1 \
  && no "worker 映像內含 zentra_sdk" || ok "worker 映像內不含 zentra_sdk"
docker compose exec -T worker python -c "import langgraph" >/dev/null 2>&1 \
  && no "worker 映像裝了 langgraph" || ok "worker 映像未裝 langgraph"

echo "═══ P4 憑證邊界 ═══"
chk "無 run token 存取 Internal Gateway" \
    "$(curl -s -o /dev/null -w '%{http_code}' "$API/internal/runs/$RID/bootstrap")" "401"
# env 是精確集合：扣掉 image 自己帶的（PATH、PYTHON_VERSION…），剩下的必須剛好是平台給的那四個。
IMAGE_ENV=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$(inspect '{{.Image}}')" | cut -d= -f1 | sort)
BOX_ENV=$(inspect '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1 | sort)
EXTRA=$(comm -13 <(echo "$IMAGE_ENV") <(echo "$BOX_ENV") | tr '\n' ' ' | sed 's/ $//')
chk "常駐容器 env 只有平台給的四個（沒有金鑰）" "$EXTRA" \
    "ZENTRA_AGENT_ID ZENTRA_CONTAINER_TOKEN ZENTRA_GATEWAY_URL ZENTRA_MAX_CONCURRENT_RUNS"

echo "═══ L1 網路隔離 ═══"
read -r PG RD NET1 META APIR <<<"$(in_box "
import socket
def p(h,q):
    try: socket.create_connection((h,q),timeout=3); return 'OPEN'
    except Exception: return 'BLOCKED'
print(p('postgres',5432),p('redis',6379),p('1.1.1.1',53),p('169.254.169.254',80),p('app',8000))")"
chk "沙盒 → postgres 不通" "$PG" "BLOCKED"
chk "沙盒 → redis 不通"    "$RD" "BLOCKED"
chk "沙盒 → 外網不通"      "$NET1" "BLOCKED"
chk "沙盒 → 雲端 metadata 不通" "$META" "BLOCKED"
chk "沙盒 → api 通（唯一出口）" "$APIR" "OPEN"
chk "掛在 internal 沙盒網段" "$(inspect '{{.HostConfig.NetworkMode}}')" "zentra-sandbox-net"
chk "沒有對外開 port" "$(inspect '{{len .HostConfig.PortBindings}}')" "0"

echo "═══ L2/L3/L4 隔離 ═══"
read -r UID_ ROOTW LIBW WSW MNT <<<"$(in_box "
import os, subprocess
def w(p):
    try: open(os.path.join(p,'probe'),'w').close(); return 'WRITABLE'
    except OSError: return 'BLOCKED'
print(os.getuid(), w('/'), w('/usr/lib'), w('/workspace'),
      'BLOCKED' if subprocess.run(['mount','-t','proc','proc','/workspace'],capture_output=True).returncode else 'OPEN')")"
chk "L2 非 root 執行" "$UID_" "65534"
chk "L2 mount 被擋"   "$MNT" "BLOCKED"
chk "L2 丟掉所有 capability" "$(inspect '{{.HostConfig.CapDrop}}')" "[ALL]"
chk "L2 非 privileged" "$(inspect '{{.HostConfig.Privileged}}')" "false"
chk "L3 記憶體上限 2 GB" "$(inspect '{{.HostConfig.Memory}}')" "2147483648"
chk "L3 沒有 swap（memory-swap = memory）" "$(inspect '{{.HostConfig.MemorySwap}}')" "2147483648"
chk "L3 行程數上限 256" "$(inspect '{{.HostConfig.PidsLimit}}')" "256"
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
echo "  ℹ 未涵蓋：一次性沙盒（路徑 B）只在常駐容器冷啟動失敗時才會用到，這支腳本不會觸發它"
echo "═══════════════════════════════════════"
[ "$fail" -eq 0 ]
