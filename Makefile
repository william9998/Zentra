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
.PHONY: help up down ps logs images base runtime migrate smoke test clean-test-data console rebuild-app

API      := http://localhost:8000
REGISTRY := 127.0.0.1:6000
SUITE    ?=

# 預設排除瀏覽器 E2E：它們要 node + Chrome，而 app 映像裡沒有，
# 掃進來會變成一整片 error 而不是有用的結果。要跑就 make test IGNORE=
# （並確認主機有 node 與 Chrome）。
IGNORE   ?= --ignore=tests/zen008/test_zen008_console_e2e.py \
            --ignore=tests/zen006a/test_run_observation_e2e.py

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

migrate:  ## 把資料庫結構升到最新
	docker compose exec -T app alembic upgrade head

# ── 驗證 ──────────────────────────────────────────────────────────────

smoke: runtime  ## 冒煙測試：一次貫穿 + 隔離探測（預期 27 通過 / 0 失敗 / 2 跳過）
	bash scripts/verify-zen000.sh

test:  ## 跑指定 suite，例：make test SUITE=tests/zen008（不含瀏覽器 E2E）
	@if [ -z "$(SUITE)" ]; then \
		echo '要指定 SUITE，只跑你改到的：make test SUITE=tests/zen009'; \
		echo '全套回歸要明講：make test SUITE=tests'; exit 2; \
	fi
	@if command -v uv >/dev/null 2>&1; then \
		echo '用主機的 uv'; \
		uv run pytest $(SUITE) $(IGNORE) -q; \
	else \
		echo '⚠️  主機沒有 uv —— 改在 app 映像裡跑（compose 網段內）。'; \
		echo '    映像裡沒有 docker CLI，所以要開沙盒或推 image 的測試會 ERROR，'; \
		echo '    那是環境限制不是程式壞掉。要完整跑：curl -LsSf https://astral.sh/uv/install.sh | sh'; \
		echo '    平常建議只跑受影響的：make test SUITE=tests/zen009'; \
		echo ''; \
		docker run --rm --network zentra-net -v "$$PWD:/srv" -w /srv \
			-e PYTHONPATH=/srv \
			-e ZENTRA_TEST_BASE_URL=http://app:8000 \
			-e ZENTRA_TEST_DB_URL=postgresql+asyncpg://zentra:zentra@postgres:5432/zentra \
			zentra-app:latest \
			sh -c "pip install -q pytest pytest-asyncio anyio 2>/dev/null; pytest $(SUITE) $(IGNORE) -q"; \
	fi

clean-test-data:  ## 有在平台上建測試 Agent / Run 才需要（留 builtin fixture）
	bash scripts/clean-test-data.sh

# ── 其他 ──────────────────────────────────────────────────────────────

console:  ## 用瀏覽器打開 Console
	open $(API)/console/agents

rebuild-app:  ## 改過 apps/console/ 之後要跑這個，瀏覽器才看得到
	docker compose up -d --build app
