# 監控（Prometheus + Grafana）

這個資料夾是**維運用的監控**，跟平台本身分開：不屬於平台的五個長駐服務，平台程式也不依賴它。
監控停了，平台照跑；平台倒了，監控還要能告警 —— 所以它應該跑在**獨立的監控主機**上。

## 兩個名詞

- **Prometheus**：記錄員。每 15 秒去各台主機問一次數字（CPU、記憶體、佇列長度……），連同時間存下來，並依規則判斷要不要告警。
- **Grafana**：儀表板。去 Prometheus 拿數字畫成圖。
- **exporter**：裝在被監控的主機上的小程式，把那台主機的狀態吐成 Prometheus 看得懂的數字。

## 誰裝在哪裡

```
┌─ 監控主機 ─────────────── monitoring/server/
│   prometheus :9090   抓數字、存、算告警
│   grafana    :3000   儀表板
│   blackbox           從外面打 AP 的 /healthz
│
│ 每 15 秒去抓 ↓
│
├─ AP 主機 ──────────────── monitoring/app-server/
│   node-exporter  :9100  主機 CPU / 記憶體 / 磁碟
│   cadvisor       :8081  每個容器（含 agent 常駐容器）的資源、重啟、OOM
│   redis-exporter :9121  Redis 狀態、Celery 佇列與 agent_work:* 佇列長度
│
└─ DB 主機 ──────────────── monitoring/db-server/
    node-exporter      :9100
    postgres-exporter  :9187  連線數、交易、鎖
```

DB 還沒拆出去之前，PostgreSQL 在 AP 主機上，`postgres-exporter` 也先裝在 AP 主機（見下方步驟 2）。

## 部署步驟

### 1. AP 主機

平台要先跑起來（`make up`），因為 redis-exporter 要加入平台的 `zentra-net`。

```bash
cd monitoring/app-server
docker compose up -d
```

### 2. PostgreSQL 的 exporter

先在資料庫建一個**只能看、不能改**的監控帳號（`pg_monitor` 是 PostgreSQL 內建的唯讀監控角色）：

```bash
docker compose exec -T postgres psql -U zentra -d zentra -c \
  "CREATE ROLE zentra_monitor LOGIN PASSWORD '換成強密碼'; GRANT pg_monitor TO zentra_monitor;"
```

```bash
cd monitoring/db-server
cp env.example .env              # 填 PG_MONITOR_PASSWORD
# DB 還在 AP 主機上：只起 postgres-exporter（node-exporter 步驟 1 已經起了）
docker compose up -d postgres-exporter
# DB 拆到獨立主機後：把這個資料夾放到 DB 主機，.env 的 PG_HOST 改掉，然後
# docker compose up -d
```

### 3. 監控主機

```bash
cd monitoring/server
cp env.example .env              # 填 GRAFANA_ADMIN_PASSWORD
vi prometheus/prometheus.yml     # 把 host.docker.internal 全部換成 AP / DB 主機的 IP
docker compose up -d
```

打開 `http://<監控主機>:3000`，登入後在 **Dashboards → Zentra → Zentra 總覽**。

改了 `prometheus.yml` 或告警規則之後，不用重啟：

```bash
curl -X POST localhost:9090/-/reload
```

### 4. 防火牆（一定要做）

exporter 和 Prometheus **都沒有登入機制**，誰連得到誰就看得到。
AP / DB 主機上的 exporter port 只開給監控主機。RHEL 系（firewalld）的例子：

```bash
# 在 AP 主機上，監控主機 IP 假設是 10.0.0.50
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=10.0.0.50 port port=9100 protocol=tcp accept'
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=10.0.0.50 port port=8081 protocol=tcp accept'
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=10.0.0.50 port port=9121 protocol=tcp accept'
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=10.0.0.50 port port=9187 protocol=tcp accept'
sudo firewall-cmd --reload
```

⚠️ Docker 發布的 port 會繞過 firewalld 的一般規則，正式環境要跟公司 infra 確認他們怎麼處理
（常見做法是在 `DOCKER-USER` chain 加規則，或用網路層的防火牆）。

## 看哪裡、怎麼查

| 想知道 | 去哪看 |
|---|---|
| 現在整體好不好 | Grafana「Zentra 總覽」最上面一排 |
| 哪個監控目標連不到 | Prometheus 網頁 → Status → Targets |
| 現在有哪些告警 | Prometheus 網頁 → Alerts，或 Grafana → Alerting |
| 告警條件是什麼 | `server/prometheus/rules/alerts.yml`（每條都有中文說明與排查第一步） |

Prometheus 網頁預設只綁監控主機本機，從自己電腦看要開 SSH tunnel：

```bash
ssh -L 9090:localhost:9090 <監控主機>
# 然後開 http://localhost:9090
```

## 告警目前只會「亮燈」，不會通知人

告警會顯示在 Prometheus 和 Grafana，但**還不會寄信或發 Teams**。
要通知人得再加一個 Alertmanager，並填公司的 SMTP 或 Teams webhook —— 這些要先問 infra 團隊。
（如果公司已經有統一的監控平台，可能是把 Prometheus 接過去，而不是自己發通知。）

## 本機練習

三份 compose 可以全部起在同一台電腦上，`prometheus.yml` 的預設值就是給這個情境的。
但在 macOS 上有幾個跟正式環境不同的地方：

- node-exporter 看到的是 Docker Desktop 的 Linux VM，不是 Mac 本身（記憶體會顯示成 VM 的大小）。
- 本機若有別的程式占了 9100 / 5432 / 8000，`host.docker.internal:<port>` 會連到那個程式。
  node-exporter 可以用 `NODE_EXPORTER_PORT=9110 docker compose up -d` 換 port（`prometheus.yml` 的目標要一起改）。
- 本機的監控帳號可以先用平台的 `zentra` / `zentra`。

收掉：在三個資料夾各跑一次 `docker compose down`（加 `-v` 會連歷史資料一起刪）。
