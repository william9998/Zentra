# Zentra —— 開發便利指令。
#
# 這裡**沒有任何產品行為**：每一條都只是把 README 裡已經寫著的指令包起來，
# 讓你不用記。真正的規格在 specs/，真正的部署是 compose.yaml。
#
# 為什麼是 Makefile 而不是在 compose 裡加一個 build-only 服務：
# compose 只幫「它要跑的服務」build image，而 agent-base 沒有東西會跑它。
# 硬塞一個 profiles 擋住的假服務，會讓 compose 宣告一個不存在的依賴 ——
# 那正是 ZEN-009 的 Z9-A6 在防的事。長駐服務就是五個，不多不少。

.DEFAULT_GOAL := help
.PHONY: help up down ps logs images base runtime migrate smoke clean-test-data console rebuild-app

API      := http://localhost:8000
REGISTRY := 127.0.0.1:6000

help:  ## 列出所有指令
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ── 起停 ──────────────────────────────────────────────────────────────

up: base  ## 起整套：五個服務 + base image + migration，最後檢查健康
	docker compose up -d --build
	$(MAKE) migrate
	@printf 'healthz: ' && curl -s $(API)/healthz && echo
	@printf 'registry: ' && (curl -sI http://$(REGISTRY)/v2/ \
		| grep -i docker-distribution-api-version || echo '⚠️ 不是 registry —— 查誰佔著 6000')
	@echo 'Console → $(API)/console/agents'

down:  ## 停掉所有服務（資料留著）
	docker compose down

ps:  ## 看服務狀態
	docker compose ps

logs:  ## 追 log，例：make logs S=worker
	docker compose logs -f $(S)

# ── 映像 ──────────────────────────────────────────────────────────────

images: base runtime  ## build 兩個平台映像

base:  ## build agent-base:1.0（你的 agent image 的 FROM）
	docker build -t zentra/agent-base:1.0 -f docker/agent-base.Dockerfile .

runtime:  ## build agent-runtime:dev（只有冒煙測試的內建 fixture 需要，見 ZEN-010）
	docker build -t zentra/agent-runtime:dev -f docker/agent-runtime.Dockerfile .

# ── 資料庫 ────────────────────────────────────────────────────────────

migrate:  ## 升 schema。既有庫先：bash scripts/adopt-legacy-schema.sh --dry-run
	docker compose exec -T app python -m apps.api.migrations

# ── 驗證 ──────────────────────────────────────────────────────────────

smoke: runtime  ## 冒煙測試：一次貫穿 + 隔離探測（預期 27 通過 / 0 失敗 / 2 跳過）
	bash scripts/verify-zen000.sh

clean-test-data:  ## 有在平台上建測試 Agent / Run 才需要（留 builtin fixture）
	bash scripts/clean-test-data.sh

# ── 其他 ──────────────────────────────────────────────────────────────

console:  ## 用瀏覽器打開 Console
	open $(API)/console/agents

rebuild-app:  ## 改過 apps/console/ 之後要跑這個，瀏覽器才看得到
	docker compose up -d --build app
