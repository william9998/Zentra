# 維運說明：內部 Registry

> 對象是跑這台主機的人。平台**不在 push 路徑上**——`docker push` 是使用者 CLI 與主機
> Docker daemon 之間的事，`api` 與 `worker` 完全看不到。所以下面每一件事都是你的責任，
> 不是平台的功能（[ADR-0009](../specs/adr/ADR-0009-internal-registry-image-upload.md) §R2-4）。

## 一句話先講

**`registry garbage-collect --delete-untagged` 絕對不准用。** 理由在 §3。

---

## 1. 這個 Registry 是什麼

`compose.yaml` 的第 5 個長駐服務，上游原版 `registry:2`：

| | |
|---|---|
| 綁定 | `127.0.0.1:5000` —— **只限同機**，這不是文件規定，是網路事實 |
| TLS / 認證 | **沒有**。在只限同機的前提下它們買不到任何東西 |
| 刪除 | `REGISTRY_STORAGE_DELETE_ENABLED=false` 寫死，**連誤刪都做不到** |
| 資料 | named volume `registrydata` → `/var/lib/registry` |
| 誰會連它 | 只有主機的 Docker daemon（pull）與使用者的 CLI（push）。**沒有任何平台服務會連它** |

### macOS：port 5000 會被 AirPlay 佔走

`ControlCenter` 的 AirPlay Receiver 預設在 `*:5000` 聽，會蓋掉 registry。症狀是
`curl localhost:5000/v2/` 回 `403` 且 header 是 `Server: AirTunes/...`：

```
系統設定 → 一般 → AirDrop 與 Handoff → AirPlay 接收器 → 關閉
```

**port 刻意不做成設定項。** 做成設定項就需要一個端點讓 Console 知道實際值，
那是為一個環境細節新增 API 表面。

---

## 2. 備份

**`registrydata` 與 `pgdata` 同等重要，要一起備份。**

DB 存的是 digest，image 本體在 registry 裡。只備份其中一個，還原之後
`PUBLISHED` 的版本會拉不到 image ——「不可變」就退化成「不可修復」。

```bash
docker run --rm -v zentra_registrydata:/data -v "$PWD:/out" alpine \
  tar czf /out/registrydata-$(date +%F).tar.gz -C /data .
docker run --rm -v zentra_pgdata:/data -v "$PWD:/out" alpine \
  tar czf /out/pgdata-$(date +%F).tar.gz -C /data .
```

---

## 3. 垃圾回收

### ⚠️ 絕對不准用 `--delete-untagged`

使用者用同一個 tag 推第二次，前一個 manifest 會變成 untagged ——
**但它可能正是某個 `PUBLISHED` 版本的 `image_digest`**。
這個 flag 會把它刪掉，那個版本立刻永久不可執行。

平台註冊的是 digest 不是 tag，所以 tag 被覆寫對 P5 沒有影響
（`delete.enabled=false` 保證舊 manifest 不會被移除，仍以 digest 可定址）。
**唯一的危險就是這個 flag。**

### 正確的回收程序

未被任何 `agent_versions` 引用的 repository，**平台不刪、不列、不管** ——
它無從分辨那是垃圾、還是使用者五分鐘後要註冊的東西、還是刻意放的 base image。
所以回收是你手動做的，而且要照這個順序：

```bash
# 1. 列出 registry 裡有什麼
curl -s http://localhost:5000/v2/_catalog | jq -r '.repositories[]'

# 2. 列出 DB 裡還在用的 digest
docker compose exec -T postgres psql -U zentra -d zentra -At -c \
  "SELECT image_ref FROM agent_versions WHERE status = 'PUBLISHED';"

# 3. 人工比對。只動「兩邊都確認過」的 repository —— 不要寫腳本自動刪
# 4. 停機
docker compose stop registry

# 5. 暫時開啟 delete，跑 GC（注意：沒有 --delete-untagged）
docker compose run --rm -e REGISTRY_STORAGE_DELETE_ENABLED=true registry \
  registry garbage-collect /etc/docker/registry/config.yml

# 6. 關回去
docker compose up -d registry
```

第 6 步不能忘。`delete.enabled=false` 是「誤刪做不到」這個保證的實作，
留在 `true` 等於把保證拿掉。

---

## 4. 磁碟滿掉會怎樣

- 使用者的 `docker push` 在 client 端失敗（registry 自己的錯誤訊息）
- **平台不會顯示任何訊號** —— 它不在 push 路徑上，看不到這件事
- 已經 `PUBLISHED` 的版本**不受影響**，照常執行

平台不做配額、不做磁碟預檢。宣稱能管理自己看不到的東西才是不誠實。

---

## 5. `/v2/_catalog` 這類原生端點

**不遮蔽**（上面的回收程序需要它），但**平台不得代理、轉發、嵌入或連結它們**。
這是硬規則：`api` 上不准出現任何一條打 registry 的路由。

它對主機上任何行程可見。這不是「沒有暴露」，是「暴露範圍等於能碰這台主機的人」——
而那些人本來就能碰 `docker.sock`，那已經等同 root。

---

## 6. 什麼情況要回去改設計

這些都不是調參數能解決的，要開新 ADR：

| 情況 | 為什麼 |
|---|---|
| 要從遠端主機 push | TLS 與認證從「不成立」變成前置條件，**必須先解決才能開 `0.0.0.0`**，順序不可顛倒。而且 `localhost:5000` 這個已寫進 DB 的名字會失效，是一次痛苦的遷移 |
| 出現第二個租戶 | 無認證的 Registry 沒有租戶隔離，任何能碰主機的人都能覆寫任何 tag |
| 要跑不可信的 image | 沒有簽章、沒有掃描、`docker.sock` 等同 root。回去讀 [ADR-0001](../specs/adr/ADR-0001-sandbox-isolation.md) |
| 垃圾成長到受不了 | **先做「版本刪除」這個產品功能，再做 GC。** 順序不可顛倒 |
| 使用者開始用遠端 `DOCKER_HOST` | `localhost:5000` 會指向錯的機器，push 成功、pull 失敗。那已經不是單機部署了 |
