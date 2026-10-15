"""ZEN-019：設定與機密名字怎麼進沙盒（api-contract §4.1）。

**三條路，三個獨立的測試函式。** 只有 bootstrap 綠 = 不通過。

  Z19-04  路徑一：一次性沙盒 `GET /internal/runs/{id}/bootstrap`
  Z19-05  路徑二：常駐容器的工作項（worker push → `GET /internal/agents/{id}/work`）
  Z19-06  路徑三：validation 沙盒 —— **一律空的**（v0.12.1 U2）

> 這是第三次踩同一個坑：ZEN-012 的 `default_model` 只改了 bootstrap、
> ZEN-017 的 `ctx.llm()` 又一次。常駐容器是**預設路徑**，漏掉它的症狀跟
> 「設定沒存進去」一模一樣。

路徑二額外用**三個角度**驗，因為「跑一個真的常駐容器」需要 docker CLI，
而測試映像裡沒有（見交付說明）：

  a. worker 用的那兩個同步查詢函式對真 DB 回對的值
  b. `execute_run` 組出來的工作項**字面上**就有那兩個 key（AST 檢查）
  c. 工作項經 Redis → `GET /internal/agents/{id}/work` 原樣回來
"""

from __future__ import annotations

import ast
import json
import os
import pathlib

import pytest

from tests.zen019.conftest import (
    auth, insert_agent, insert_container, issue_run_token,
    make_published_version, make_run, new_id, purge_agent, requires_db,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
CONFIG = {"symbols": ["BTCUSDT", "ETHUSDT"], "interval": "1h",
          "nested": {"a": [1, 2, None]}, "flag": True, "nothing": None}


async def _agent_with_settings():
    from apps.api.services import agent_secrets, agent_settings

    agent = await insert_agent(name=f"zen019-dispatch-{os.urandom(5).hex()}")
    from apps.api import db

    async with db.transaction() as conn:
        await agent_settings.replace(conn, agent.id, dict(CONFIG))
        # 刻意用非字母序存，驗 `secret_names` 真的排序過。
        await agent_secrets.set_secret(conn, agent.id, "SMTP_PASSWORD", "pw-value")
        await agent_secrets.set_secret(conn, agent.id, "BINANCE_KEY", "key-value")
    return agent


# ── Z19-04：路徑一 —— 一次性沙盒的 bootstrap ──────────────────────────

@requires_db
async def test_path_one_oneshot_sandbox_bootstrap(app_client, redis_client):
    agent = await _agent_with_settings()
    try:
        version = await make_published_version(agent.id)
        run = await make_run(agent.id, version.id)
        token = await issue_run_token(redis_client, run.id)

        response = await app_client.get(
            f"/internal/runs/{run.id}/bootstrap", headers=auth(token))
        assert response.status_code == 200, response.text
        body = response.json()

        # **頂層欄位**，與 PUT 的文件深度相等
        assert body["config"] == CONFIG
        assert body["secret_names"] == ["BINANCE_KEY", "SMTP_PASSWORD"], "要排序過"
        # Z19-09：**不得污染 manifest**
        assert "config" not in body["manifest"]
        # Z19-17：名字清單只有純字串，沒有值、沒有長度、沒有尾四碼
        assert all(isinstance(name, str) for name in body["secret_names"])
        assert "pw-value" not in response.text and "key-value" not in response.text
        assert "ciphertext" not in response.text and "last_four" not in response.text
    finally:
        await purge_agent(agent.id)


@requires_db
async def test_changing_config_takes_effect_on_the_next_bootstrap(
    app_client, redis_client,
):
    """Z19-07 的 bootstrap 版：**改設定不用重發版本、不用重啟任何東西。**"""
    from apps.api.services import agent_settings

    agent = await _agent_with_settings()
    try:
        version = await make_published_version(agent.id)
        first = await make_run(agent.id, version.id)
        first_token = await issue_run_token(redis_client, first.id)
        before = (await app_client.get(
            f"/internal/runs/{first.id}/bootstrap", headers=auth(first_token))).json()
        assert before["config"]["interval"] == "1h"

        from apps.api import db

        async with db.transaction() as conn:
            await agent_settings.replace(conn, agent.id, {"interval": "4h"})

        second = await make_run(agent.id, version.id)
        second_token = await issue_run_token(redis_client, second.id)
        after = (await app_client.get(
            f"/internal/runs/{second.id}/bootstrap", headers=auth(second_token))).json()
        assert after["config"] == {"interval": "4h"}, "整份取代，不是合併"

        # Z19-08：**已經派工的那一筆不受影響** —— 它的 bootstrap 早就取過了。
        # 這裡驗的是「同一筆 Run 不會因為設定變了就拿到不同的 manifest」，
        # config 本身每次取都重算（契約 §2.6 第 4 點），所以不斷言 before == after。
        assert before["manifest"] == after["manifest"]
    finally:
        await purge_agent(agent.id)


# ── Z19-05：路徑二 —— 常駐容器的工作項 ────────────────────────────────

@requires_db
def test_path_two_worker_helpers_read_the_same_values(session_sync_agent):
    """(a) worker 用的那兩個同步查詢對真 DB 回對的值。"""
    from apps.worker.tasks.run import _agent_config, _secret_names

    agent_id = session_sync_agent
    assert _agent_config(agent_id) == CONFIG
    assert _secret_names(agent_id) == ["BINANCE_KEY", "SMTP_PASSWORD"]


def test_path_two_work_item_literally_carries_both_fields():
    """(b) `execute_run` 組出來的工作項**字面上**就有那兩個 key。

    這條是「只改 bootstrap」的靜態護欄。它不需要 DB，也不需要 docker ——
    刪掉那兩行就會紅，而那正是 ZEN-012 與 ZEN-017 各踩過一次的錯。
    """
    source = (REPO / "apps/worker/tasks/run.py").read_text()
    tree = ast.parse(source)

    items: list[ast.Dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if {"run_id", "run_token", "manifest"} <= keys:
                items.append(node)
    assert items, "找不到常駐容器的工作項字面量"

    for item in items:
        keys = {k.value for k in item.keys if isinstance(k, ast.Constant)}
        assert "config" in keys, "工作項少了 config —— 常駐容器永遠拿不到設定"
        assert "secret_names" in keys, "工作項少了 secret_names"

    # 值要來自那兩個查詢函式，不是寫死的 {} / []
    assert "_agent_config(run.agent_id)" in source
    assert "_secret_names(run.agent_id)" in source


@requires_db
async def test_path_two_work_item_survives_the_gateway(
    app_client, redis_client,
):
    """(c) 工作項經 Redis → `GET /internal/agents/{id}/work` **原樣**回來。

    Gateway 這一段刻意不重組工作項（它只是把 Redis 裡的那個物件原樣吐出來），
    所以這裡驗的是「不會在中途被吃掉」。
    """
    import os as _os

    from redis import Redis as SyncRedis

    from apps.api.services import containertoken, work
    from apps.worker.tasks.run import _agent_config, _secret_names

    agent = await _agent_with_settings()
    sync = SyncRedis.from_url(
        _os.environ.get("ZENTRA_TEST_REDIS_URL", "redis://redis:6379/0"),
        decode_responses=True)
    try:
        version = await make_published_version(agent.id)
        run = await make_run(agent.id, version.id)
        token = await issue_run_token(redis_client, run.id)

        container = await insert_container(agent.id, version.id)
        container_token = containertoken.issue_sync(sync, agent.id, 300, container.id)

        # worker 端做的事：用同一組 helper 組工作項再 push。
        item = {
            "run_id": str(run.id), "run_token": token,
            "entrypoint": "default", "input": {},
            "manifest": version.manifest or {},
            "default_model": "gpt-4o-mini",
            "config": _agent_config(agent.id),
            "secret_names": _secret_names(agent.id),
        }
        work.push_sync(sync, agent.id, version.id, item)

        # Redis 裡的原文也要有那兩個欄位
        raw = sync.lindex(work.queue_key(agent.id, version.id), 0)
        assert "config" in json.loads(raw) and "secret_names" in json.loads(raw)

        response = await app_client.get(
            f"/internal/agents/{agent.id}/work", headers=auth(container_token))
        assert response.status_code == 200, response.text
        taken = response.json()
        assert taken["config"] == CONFIG
        assert taken["secret_names"] == ["BINANCE_KEY", "SMTP_PASSWORD"]
        assert "config" not in taken["manifest"]
        assert "pw-value" not in response.text
    finally:
        sync.delete(f"agent_work:{agent.id}:{new_id()}")
        sync.close()
        await purge_agent(agent.id)


# ── Z19-06：路徑三 —— validation 沙盒一律空的（U2）────────────────────

def test_path_three_validation_builds_an_empty_context():
    """**v0.12.1 U2 是行為反轉**：validation 沙盒不取當下設定。

    `validate()` 在容器內直接建 `ZentraContext`，沒有「去跟平台要」這個動作，
    而 env 是精確集合不能加變數 —— 硬要帶設定只能發明一條契約外的路由。

    空 config 驗證**比帶設定更嚴格**：一個空 config 就 build 不起來的 agent
    應該發布失敗，否則使用者哪天清掉設定，它的每一筆 Run 都會死在 build 階段
    而版本狀態仍是 `PUBLISHED`。
    """
    source = (REPO / "packages/sdk/zentra_sdk/image_runtime.py").read_text()
    tree = ast.parse(source)
    validate = next(node for node in tree.body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "validate")

    constructions = [node for node in ast.walk(validate)
                     if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name)
                     and node.func.id == "ZentraContext"]
    assert len(constructions) == 1
    kwargs = {keyword.arg for keyword in constructions[0].keywords}
    assert "config" not in kwargs, "validation 沙盒不得帶 config（U2）"
    assert "secret_names" not in kwargs

    # 也不准偷偷加一條 internal 路由去拿
    assert "/versions/" not in source or "/validation" in source
    assert "bootstrap" not in source.split("async def validate")[1].split(
        "async def _execute")[0]


def test_default_context_is_empty_not_none():
    """validate 期間 `ctx.config` 是 `{}`、`ctx.secret_names` 是 `[]`，
    **不是 None、不丟例外** —— agent 的 `.get()` 才寫得下去。"""
    import sys

    sys.path.insert(0, str(REPO / "packages/sdk"))
    from zentra_sdk.context import ZentraContext

    ctx = ZentraContext("v1", {"name": "x"}, lambda *_: None)
    assert ctx.config == {}
    assert ctx.secret_names == []


def test_no_version_bootstrap_route_exists():
    """U2：**不新增任何路由。** 有的話就是契約漂移。"""
    from apps.api.routers import internal

    paths = {getattr(route, "path", "") for route in internal.router.routes}
    assert "/internal/versions/{version_id}/bootstrap" not in paths
    assert "/internal/versions/{version_id}/validation" in paths


# ── Z19-10 / Z19-11：SDK 的 ctx ───────────────────────────────────────

def test_ctx_config_reads_the_top_level_field_not_the_manifest():
    """Z19-10。manifest 裡**故意**放一份毒的 config，它不准被讀到。"""
    import sys

    sys.path.insert(0, str(REPO / "packages/sdk"))
    from zentra_sdk.context import ZentraContext

    ctx = ZentraContext(
        "run-1", {"config": {"poisoned": True}}, lambda *_: None,
        config={"real": 1}, secret_names=["B", "A"])
    assert ctx.config == {"real": 1}
    # 回的是副本：改它不影響下一次讀取
    ctx.config["real"] = 999
    assert ctx.config == {"real": 1}
    assert ctx.secret_names == ["A", "B"], "排序穩定"
    ctx.secret_names.append("C")
    assert ctx.secret_names == ["A", "B"]


def test_there_is_no_way_to_read_a_secret_value():
    """Z19-11。`ctx.secrets` 不存在 —— 那不是還沒做，是契約禁止。"""
    import sys

    sys.path.insert(0, str(REPO / "packages/sdk"))
    from zentra_sdk.context import ZentraContext

    ctx = ZentraContext("run-1", {}, lambda *_: None, secret_names=["A"])
    assert not hasattr(ctx, "secrets")
    leaky = [name for name in dir(ctx)
             if "secret" in name.lower() and name != "secret_names"
             and not name.startswith("_")]
    assert leaky == []


def test_sdk_no_longer_reads_config_out_of_the_manifest():
    """Z19-09 的靜態部分。"""
    import subprocess

    result = subprocess.run(
        ["grep", "-rn", r'manifest\.get("config"', "-e", r'manifest\["config"\]',
         str(REPO / "packages/sdk"), str(REPO / "apps")],
        capture_output=True, text=True)
    assert result.stdout.strip() == "", result.stdout


# ── 給路徑二 (a) 的同步 session ───────────────────────────────────────

@pytest.fixture
async def session_sync_agent():
    """建好資料之後把 agent_id 交給 worker 的同步查詢函式。

    worker 是同步的（Celery），所以那兩個函式也必須是同步的 ——
    用 async session 測等於測了另一條不存在的路徑。
    """
    agent = await _agent_with_settings()
    try:
        yield agent.id
    finally:
        await purge_agent(agent.id)
