# 監控 SOP（Prometheus + Grafana）

這個資料夾是**維運用的監控**，跟平台本身分開：不屬於平台的五個長駐服務，平台程式也不依賴它。
監控停了，平台照跑；平台倒了，監控還要能告警 —— 所以正式環境它跑在**獨立的監控主機**上。

- [先懂三個名詞](#先懂三個名詞)
- [SOP A：Mac 本機練習](#sop-amac-本機練習)
- [SOP B：正式部署](#sop-b正式部署)
- [SOP C：日常維運](#sop-c日常維運)

---

## 先懂三個名詞

- **exporter**：裝在**被監控的主機**上的小程式，把那台主機的狀態變成一串數字，開一個網址等人來讀。
- **Prometheus**：裝在**監控主機**上。每 15 秒主動去各台主機的 exporter 讀一次數字、連同時間存起來，並依規則判斷要不要告警。
- **Grafana**：裝在**監控主機**上。去 Prometheus 拿數字畫成儀表板。

**重點：數字是監控主機「主動來拿」的，不是 AP / DB 主機「推過去」的。**
所以 AP / DB 主機上**不用寫任何程式、不用改平台程式碼、不用設定要送去哪**，
只要把 exporter 跑起來、讓監控主機連得到它的 port 就好。這跟 log 拋轉到 NFS 是相反的方向。

```
┌─ 監控主機 ─────────────── monitoring/server/
│   prometheus :9090   去各台拿數字、存、算告警
│   grafana    :3000   儀表板
│   blackbox           從外面打 AP 的 /healthz，確認平台對外活著
│
│ 每 15 秒主動去拿 ↓
│
├─ AP 主機 ──────────────── monitoring/app-server/
│   node-exporter      :9100  主機 CPU / 記憶體 / 磁碟
│   cadvisor           :8081  每個容器（含 agent 常駐容器）的資源、重啟、OOM
│   redis-exporter     :9121  Redis 狀態、Celery 佇列與 agent_work:* 佇列長度
│   postgres-exporter  :9187  （DB 還在這台時才起：--profile db）
│
└─ DB 主機（拆出去之後）──── monitoring/db-server/
    node-exporter      :9100
    postgres-exporter  :9187
```

---

## SOP A：Mac 本機練習

三份全部起在同一台 Mac 上，`prometheus.yml` 的預設值（`host.docker.internal`）就是給這個情境的。

### A-0. 先知道 Mac 跟正式環境不一樣的地方

Mac 上的 Docker（Colima 或 Docker Desktop）其實跑在一台 Linux 虛擬機裡：

- node-exporter 看到的是那台**虛擬機**，不是 Mac 本身（例如記憶體會顯示成虛擬機的 2 GB）。
- `host.docker.internal` 指回 Mac 本身。Mac 上如果有別的程式占著 8000 / 9100 等 port，
  Prometheus 會連到那個程式，而不是 Zentra 的。
- 先檢查 port 有沒有被占：`lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(8000|8081|9090|9100|9121|9187|3000) '`

### A-1. 起平台

```bash
make up
curl -s localhost:8000/healthz          # 要回 200
```

### A-1.5. 建唯讀監控帳號（建議練習）

跟正式環境的 [B-1](#b-1-建-postgresql-唯讀監控帳號一次就好) 一樣的指令，本機先練一次，上正式環境就不是第一次做。

```bash
# 在 repo 根目錄
docker compose exec -T postgres psql -U zentra -d zentra -c \
  "CREATE ROLE zentra_monitor LOGIN PASSWORD 'monitor-local'; GRANT pg_monitor TO zentra_monitor;"
```

再給它兩張表**幾個欄位**的唯讀權限 —— 告警「Agent 常駐容器已放棄」要靠它（查詢在 `monitoring/postgres-queries.yaml`）：

```bash
docker compose exec -T postgres psql -U zentra -d zentra -c \
  "GRANT SELECT (id, name) ON agents TO zentra_monitor;
   GRANT SELECT (agent_id, agent_version_id, status, restarts_in_window, created_at) ON agent_containers TO zentra_monitor;"
```

只給欄位、不給整張表：它看得到「哪個 Agent 的容器是什麼狀態」，看不到 Agent 的說明、Run 的輸入輸出等任何業務資料。

確認它真的「只能看統計、不能看資料」：

```bash
# 看得到連線統計 → 會列出數字
docker compose exec -T postgres psql -U zentra_monitor -d zentra -c "SELECT count(*) FROM pg_stat_activity;"
# 讀不到業務資料 → 要出現 permission denied（SELECT * 包含沒授權的欄位）
docker compose exec -T postgres psql -U zentra_monitor -d zentra -c "SELECT * FROM agents LIMIT 1;"
```

帳號已經建過會出現 `role "zentra_monitor" already exists`，可以直接往下走。

> **想省事可以跳過這步，直接用 `zentra` / `zentra`，但正式環境不可以。**
> 原因：`zentra` 是資料庫的最高權限帳號，能看、能改、能刪所有資料；監控程式的密碼放在主機的 `.env` 裡，
> 一旦外洩就等於整個資料庫被拿走。唯讀帳號外洩，最多只是被看到統計數字。

### A-2. 起 AP 端的 exporter

```bash
cd monitoring/app-server
cp env.example .env                     # 填 A-1.5 的 zentra_monitor / monitor-local（跳過 A-1.5 就填 zentra / zentra）
docker compose --profile db up -d
```

確認每個 exporter 都吐得出數字（有輸出就對了）：

```bash
curl -s localhost:9100/metrics | grep ^node_memory_MemTotal_bytes
curl -s localhost:8081/metrics | grep -m1 ^container_memory_working_set_bytes
curl -s localhost:9121/metrics | grep -E '^redis_up|^redis_key_size'
curl -s localhost:9187/metrics | grep ^pg_up                 # 要是 1
```

9100 被占了（例如 MinIO）→ 在 `.env` 設 `NODE_EXPORTER_PORT=9110`，重跑上面的 up，
再把 `server/prometheus/prometheus.yml` 裡 node 的目標改成 `host.docker.internal:9110`。

### A-3. 起監控端

```bash
cd monitoring/server
cp env.example .env                     # 填 GRAFANA_ADMIN_PASSWORD
docker compose up -d
```

### A-4. 驗收

1. 開 `http://localhost:9090/targets` → 每一列都是綠色 **UP**。
2. 開 `http://localhost:3000`，用 admin 和 `.env` 的密碼登入 → Dashboards → Zentra → **Zentra 總覽**，最上面四顆燈都是綠的。
3. 開 `http://localhost:9090/alerts` → 正常情況下沒有紅色（Firing）的告警。

### A-5. 收掉

```bash
(cd monitoring/server     && docker compose down)
(cd monitoring/app-server && docker compose --profile db down)
# 加 -v 會連 Prometheus / Grafana 的歷史資料一起刪
```

---

## SOP B：正式部署

順序：**DB 監控帳號 → AP 主機 → (DB 主機) → 監控主機 → 防火牆 → 驗收**。
每台主機都需要 Docker 與 docker compose，並把這個 repo 的**整個** `monitoring/` 資料夾放上去（`app-server` / `db-server` 會用到上一層的 `postgres-queries.yaml`）。

### B-1. 建 PostgreSQL 唯讀監控帳號（一次就好）

`pg_monitor` 是 PostgreSQL 內建的角色：只能**看**統計資訊，不能讀業務資料、不能改任何東西。

```bash
# 在平台主機的 repo 目錄
docker compose exec -T postgres psql -U zentra -d zentra -c \
  "CREATE ROLE zentra_monitor LOGIN PASSWORD '換成強密碼'; GRANT pg_monitor TO zentra_monitor;
   GRANT SELECT (id, name) ON agents TO zentra_monitor;
   GRANT SELECT (agent_id, agent_version_id, status, restarts_in_window, created_at) ON agent_containers TO zentra_monitor;"
```

後兩句是**欄位級**的唯讀權限，給告警「Agent 常駐容器已放棄」用（查詢在 `monitoring/postgres-queries.yaml`）。
它只看得到「哪個 Agent 的容器是什麼狀態」，看不到任何業務資料。

⚠️ postgres-exporter 啟動時會印 `The extended queries.yaml config is DEPRECATED`：自訂查詢這個功能官方已標為**將來會移除**。
目前固定在 `v0.17.1`，可以正常用。**升級 postgres-exporter 前先確認新版還支援**，不支援就要改用 sql_exporter 跑同一條查詢。

DB 拆到獨立主機後，改在那台用 DBA 帳號執行同一句 SQL，並確認 `pg_hba.conf` 允許本機連線。

### B-2. AP 主機

```bash
# 平台要先跑著（make up）
cd monitoring/app-server
cp env.example .env                     # 填 B-1 的監控帳號密碼

# DB 還在這台（現在）：
docker compose --profile db up -d
# DB 已經拆出去：
docker compose up -d
```

用 SOP A-2 的四行 curl 確認每個 exporter 都有數字（DB 拆出去後，9187 那行改到 DB 主機上跑）。

### B-3. DB 主機（DB 拆出去之後才做）

```bash
cd monitoring/db-server
cp env.example .env                     # 填監控帳號密碼；PostgreSQL 在本機就維持 PG_HOST=localhost
docker compose up -d

curl -s localhost:9100/metrics | grep ^node_memory_MemTotal_bytes
curl -s localhost:9187/metrics | grep ^pg_up                 # 要是 1
```

然後回 AP 主機把它的 postgres-exporter 停掉，避免兩邊重複：

```bash
cd monitoring/app-server && docker compose --profile db stop postgres-exporter && docker compose --profile db rm -f postgres-exporter
```

### B-4. 監控主機

```bash
cd monitoring/server
cp env.example .env                     # 填 GRAFANA_ADMIN_PASSWORD
vi prometheus/prometheus.yml            # 見下方
docker compose up -d
```

`prometheus.yml` 要改的只有目標位址，把每個 `host.docker.internal` 換成實際 IP：

| job | 改成 | DB 拆出去之後 |
|---|---|---|
| node | `<AP IP>:9100` | 再打開註解的 `<DB IP>:9100`（server: db） |
| cadvisor | `<AP IP>:8081` | 不變 |
| redis | `<AP IP>:9121` | 不變 |
| postgres | `<AP IP>:9187` | 改成 `<DB IP>:9187`，`server` 標籤改 `db` |
| app-health | `http://<AP IP>:8000/healthz` | 不變 |

### B-5. 防火牆（一定要做）

exporter 和 Prometheus **都沒有登入機制**，誰連得到誰就看得到。
AP / DB 主機上的 exporter port **只開給監控主機**。RHEL 系（firewalld）的例子，監控主機 IP 假設是 10.0.0.50：

```bash
# AP 主機：9100 8081 9121 9187（DB 拆出去後 9187 改在 DB 主機開）
for port in 9100 8081 9121 9187; do
  sudo firewall-cmd --permanent --add-rich-rule="rule family=ipv4 source address=10.0.0.50 port port=$port protocol=tcp accept"
done
sudo firewall-cmd --reload
```

⚠️ Docker 發布的 port 會繞過 firewalld 的一般規則。正式環境要跟公司 infra 確認怎麼處理
（常見做法是在 `DOCKER-USER` chain 加規則，或在網路層的防火牆擋）。

AP 主機的 8000（平台本身）也要讓監控主機連得到，`/healthz` 檢查才會通。

### B-6. 驗收

在**監控主機**上：

```bash
# 每個目標都要是 up
curl -s localhost:9090/api/v1/targets | python3 -c \
  'import json,sys; [print(t["labels"]["job"], t["health"], t["lastError"]) for t in json.load(sys.stdin)["data"]["activeTargets"]]'
```

再用瀏覽器開 `http://<監控主機>:3000` → Zentra 總覽，四顆燈都綠。
從自己電腦看 Prometheus 網頁（它預設只綁監控主機本機）：

```bash
ssh -L 9090:localhost:9090 <監控主機>   # 然後開 http://localhost:9090
```

---

## SOP C：日常維運

| 想知道 / 想做 | 怎麼做 |
|---|---|
| 現在整體好不好 | Grafana「Zentra 總覽」最上面一排 |
| 哪個目標連不到 | Prometheus → Status → Targets，看 Error 欄 |
| 現在有哪些告警 | Prometheus → Alerts，或 Grafana → Alerting |
| 告警是什麼意思、先查什麼 | `server/prometheus/rules/alerts.yml`，每條都有中文說明與排查第一步 |
| 改了 `prometheus.yml` 或告警規則 | 不用重啟：`curl -X POST localhost:9090/-/reload` |
| 在 Grafana 網頁上改了儀表板想保留 | 儀表板右上 Export → JSON，蓋回 `server/grafana/dashboards/zentra-overview.json` 並 commit |
| 加一台新主機 | 新主機起 `app-server` 或 `db-server` → 開防火牆 → `prometheus.yml` 加一行目標 → reload |
| 升級 exporter / Prometheus / Grafana | 改 compose 裡的版本號 → `docker compose up -d`（地端環境要先把新 image 搬進公司的 registry） |

### 某個目標變紅（DOWN）時的排查順序

1. 監控主機 `ping <主機 IP>` —— 不通 = 主機或網路的問題。
2. 那台主機 `docker compose ps` —— exporter 容器還在嗎？`docker compose logs --tail 50 <服務名>` 看錯誤。
3. 那台主機 `curl localhost:<port>/metrics` —— 本機讀得到、監控主機讀不到 = 防火牆。
4. postgres-exporter 特有：`pg_up` 是 0 → 帳號密碼錯，或 `pg_hba.conf` 不讓它連。

### 告警目前只會「亮燈」，不會通知人

告警會顯示在 Prometheus 和 Grafana，但**還不會寄信或發 Teams**。
要通知人得再加一個 Alertmanager，並填公司的 SMTP 或 Teams webhook —— 這些要先問 infra 團隊。
（如果公司已經有統一的監控平台，可能是把 Prometheus 接過去，而不是自己發通知。）
