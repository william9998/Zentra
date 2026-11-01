#!/usr/bin/env bash
# 壓測時在旁邊跑：每隔幾秒記一次各處的記憶體與排隊狀況，Ctrl-C 停。
#   用法：bash memwatch.sh [輸出檔，預設 results/mem.csv] [間隔秒數，預設 2]
#
# 記的是「Run 在哪裡佔記憶體」的每一站：
#   serve_mb   常駐容器（同時最多 50 筆在這裡等）
#   app_mb     app（Gateway 代打 LLM 時，整段回覆先收在這裡）
#   worker_mb  worker（派工）
#   redis_mb   Redis 實際用量（排隊中的工作帶著完整輸入，存在這裡）
#   queued     排隊中、還沒進容器的筆數
#   running    資料庫裡 RUNNING 的筆數
set -uo pipefail
cd "$(dirname "$0")/../.."

OUT=${1:-test/performance/results/mem.csv}
EVERY=${2:-2}
mkdir -p "$(dirname "${OUT}")"

# 一次 docker stats 查完所有容器（一個一個查，每次取樣要多花好幾秒）。
# 輸出 "名稱 MB"；"123.4MiB / 2GiB" 換算成 MB。
stats() {
  docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' "$@" 2>/dev/null | awk '{
    v=$2; u=v; gsub(/[0-9.]/,"",u); gsub(/[A-Za-z]/,"",v)
    if (u=="GiB") v*=1024; else if (u=="KiB") v/=1024; else if (u=="B") v/=1048576
    printf "%s %.1f\n", $1, v }'
}
pick() { awk -v n="$1" '$1 ~ n {print $2; exit}' <<<"${STATS}"; }

echo "time,serve_mb,app_mb,worker_mb,redis_mb,queued,running" | tee "${OUT}"
while true; do
  # 同一時間應該只有一個常駐容器（一個版本一個；壓測只打一個版本）
  SERVE=$(docker ps --format '{{.Names}}' --filter name=zentra-serve- | head -1)
  STATS=$(stats ${SERVE} zentra-app-1 zentra-worker-1)
  REDIS=$(docker compose exec -T redis sh -c '
    redis-cli INFO memory | awk -F: "/^used_memory:/{printf \"%.1f\", \$2/1048576}"
    echo -n " "
    n=0; for k in $(redis-cli --scan --pattern "agent_work:*"); do n=$((n + $(redis-cli LLEN "$k"))); done; echo $n')
  RUNNING=$(docker compose exec -T postgres psql -U zentra -d zentra -t -A \
            -c "SELECT count(*) FROM runs WHERE status = 'RUNNING'")
  echo "$(date +%H:%M:%S),$(pick zentra-serve-),$(pick zentra-app-1),$(pick zentra-worker-1),${REDIS% *},${REDIS#* },${RUNNING}" \
    | tee -a "${OUT}"
  sleep "${EVERY}"
done
