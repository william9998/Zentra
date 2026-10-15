"""ZEN-015：per-agent 下游 API key。

每條擋的是一個具體的錯：

  K-1  明文讀得回來            → key 列表變成一份可直接使用的憑證清單
  K-2  明文進 DB               → 資料庫備份 / dump / 任何有 SELECT 權的人都能冒用下游
  K-3  撤銷後還能用            → 「撤銷」變成畫面上的字，不是實際效果
  K-4  A 的 key 能打 B         → 一把 key 外洩就等於整個平台外洩
  K-5  帶錯 key 被當成沒帶      → 呼叫端以為用量有記到，帳其實掛在 NULL 上
  K-6  硬刪 key                → 這把 key 過去的 Run 變成無主的帳，查不了案
  K-7  用量沒歸戶              → `runs.api_key_id` 是 §2.3 唯一的歸戶依據，沒有它整個除錯查詢都空轉
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("ZENTRA_TEST_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="需要 ZENTRA_TEST_BASE_URL")

#: ZEN-000 內建 fixture：唯一一個一定發布過、一定跑得動的 Agent。
BUILTIN_AGENT = "01930000-0000-7000-8000-000000000001"
BUILTIN_INPUT = {"entrypoint": "default", "input": {"question": "zen015"}}


def _new_agent() -> str:
    response = httpx.post(
        f"{BASE_URL}/v1/agents",
        json={"name": f"key-unit-{os.urandom(4).hex()}", "description": "ZEN-015 test"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["agent_id"]


def _keys_url(agent_id: str) -> str:
    return f"{BASE_URL}/v1/agents/{agent_id}/api-keys"


def _issue(agent_id: str, name: str = "downstream") -> dict:
    response = httpx.post(_keys_url(agent_id), json={"name": name}, timeout=30)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def agent_id() -> str:
    return _new_agent()


def test_issue_returns_the_key_exactly_once(agent_id: str) -> None:
    """K-1：明文只在建立的回應裡出現，之後沒有任何端點讀得到。"""
    issued = _issue(agent_id)
    plaintext = issued["key"]
    assert plaintext.startswith("zk_live_")
    assert issued["key_prefix"] == plaintext[:12]
    assert issued["revoked_at"] is None

    listed = httpx.get(_keys_url(agent_id), timeout=30).json()["items"]
    assert [row["key_id"] for row in listed] == [issued["key_id"]]
    # 整份列表的任何角落都不該出現明文
    assert plaintext not in httpx.get(_keys_url(agent_id), timeout=30).text
    assert "key" not in listed[0]


def test_valid_key_is_attributed_on_the_run() -> None:
    """K-7：帶 key 呼叫 → 這筆 Run 認得出是哪一把打的。"""
    issued = _issue(BUILTIN_AGENT, "attribution")
    response = httpx.post(
        f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/runs",
        json=BUILTIN_INPUT,
        headers={"authorization": f"Bearer {issued['key']}"},
        timeout=30,
    )
    assert response.status_code == 202, response.text

    # 用過的 key 會記下 last_used_at——「這把還有人在用嗎」靠它回答
    row = next(r for r in httpx.get(_keys_url(BUILTIN_AGENT), timeout=30).json()["items"]
               if r["key_id"] == issued["key_id"])
    assert row["last_used_at"] is not None

    httpx.delete(f"{_keys_url(BUILTIN_AGENT)}/{issued['key_id']}", timeout=30)


def test_missing_key_is_allowed_and_unattributed() -> None:
    """沒帶 key 放行，是契約 §2.3 寫明的預設行為，不是漏洞。

    Console 目前沒有帶 key 的能力；這條一旦變成 401，Console 會整個不能用。
    要改成強制請設 `ZENTRA_REQUIRE_AGENT_API_KEY`，並同時處理 Console。
    """
    response = httpx.post(
        f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/runs", json=BUILTIN_INPUT, timeout=30)
    assert response.status_code == 202, response.text


def test_invalid_key_is_rejected_not_silently_anonymous() -> None:
    """K-5：「帶了但打錯」跟「沒帶」是不同的意圖。"""
    response = httpx.post(
        f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/runs",
        json=BUILTIN_INPUT,
        headers={"authorization": "Bearer zk_live_not-a-real-key"},
        timeout=30,
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_key_cannot_be_used_on_another_agent(agent_id: str) -> None:
    """K-4：跨 Agent 使用是這把 key 唯一有意義的濫用方式。"""
    issued = _issue(BUILTIN_AGENT, "cross-agent")
    response = httpx.post(
        f"{BASE_URL}/v1/agents/{agent_id}/runs",
        json=BUILTIN_INPUT,
        headers={"authorization": f"Bearer {issued['key']}"},
        timeout=30,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    httpx.delete(f"{_keys_url(BUILTIN_AGENT)}/{issued['key_id']}", timeout=30)


def test_revoked_key_stops_working_but_stays_listed() -> None:
    """K-3 + K-6：撤銷要真的失效，但那一列必須留著。"""
    issued = _issue(BUILTIN_AGENT, "to-revoke")
    deleted = httpx.delete(f"{_keys_url(BUILTIN_AGENT)}/{issued['key_id']}", timeout=30)
    # 軟撤銷所以是 200 帶 revoked_at，不是 204
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["revoked_at"] is not None

    response = httpx.post(
        f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/runs",
        json=BUILTIN_INPUT,
        headers={"authorization": f"Bearer {issued['key']}"},
        timeout=30,
    )
    assert response.status_code == 401

    listed = httpx.get(_keys_url(BUILTIN_AGENT), timeout=30).json()["items"]
    assert issued["key_id"] in [row["key_id"] for row in listed], "撤銷的 key 不該從列表消失"


def test_revoke_is_idempotent(agent_id: str) -> None:
    issued = _issue(agent_id)
    url = f"{_keys_url(agent_id)}/{issued['key_id']}"
    first = httpx.delete(url, timeout=30).json()
    second = httpx.delete(url, timeout=30).json()
    assert first["revoked_at"] == second["revoked_at"], "重複撤銷不該更新時間"


def test_revoking_another_agents_key_is_not_found(agent_id: str) -> None:
    """不分「不存在」與「別人的」——分開回答等於洩漏哪些 key id 真的存在。"""
    issued = _issue(BUILTIN_AGENT, "leak-probe")
    response = httpx.delete(f"{_keys_url(agent_id)}/{issued['key_id']}", timeout=30)
    assert response.status_code == 404
    httpx.delete(f"{_keys_url(BUILTIN_AGENT)}/{issued['key_id']}", timeout=30)


def test_unknown_agent_is_not_found() -> None:
    missing = "01930000-0000-7000-8000-00000000dead"
    assert httpx.get(_keys_url(missing), timeout=30).status_code == 404
    assert httpx.post(_keys_url(missing), json={"name": "x"}, timeout=30).status_code == 404


def test_name_is_required(agent_id: str) -> None:
    assert httpx.post(_keys_url(agent_id), json={}, timeout=30).status_code == 422
    assert httpx.post(_keys_url(agent_id), json={"name": ""}, timeout=30).status_code == 422


# ── 資料庫層的證據 ────────────────────────────────────────────────────────
# 上面那些走 API，只能證明「API 沒有洩漏」。K-2 與 K-7 是關於 DB 裡實際存了什麼，
# 只有直接查表能證明。


def _db_url() -> str:
    value = os.environ.get("ZENTRA_TEST_DB_URL")
    if not value:
        pytest.skip("需要 ZENTRA_TEST_DB_URL 才能查表")
    return value


@pytest.mark.asyncio
async def test_plaintext_never_reaches_the_database(agent_id: str) -> None:
    """K-2：DB 裡存的是雜湊。備份或 dump 外洩也還原不出可用的 key。"""
    import hashlib

    import asyncpg

    from apps.api.config import normalize_database_url

    issued = _issue(agent_id, "db-evidence")
    plaintext = issued["key"]

    conn = await asyncpg.connect(normalize_database_url(_db_url()))
    try:
        row = await conn.fetchrow(
            "SELECT key_hash, key_prefix FROM agent_api_keys WHERE id = $1",
            issued["key_id"])
    finally:
        await conn.close()

    key_hash, key_prefix = row["key_hash"], row["key_prefix"]
    assert key_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    assert plaintext not in key_hash
    # key_prefix 是辨識用的，短到不能用來冒充
    assert plaintext.startswith(key_prefix)
    assert len(key_prefix) < len(plaintext) / 2


@pytest.mark.asyncio
async def test_run_records_which_key_triggered_it() -> None:
    """K-7：`runs.api_key_id` 是 §2.3 全部除錯查詢的歸戶依據。

    沒帶 key 的 Run 必須是 NULL —— 那不是錯誤，是「Console 發起的」。
    """
    import asyncpg

    from apps.api.config import normalize_database_url

    issued = _issue(BUILTIN_AGENT, "run-attribution")
    keyed = httpx.post(
        f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/runs", json=BUILTIN_INPUT,
        headers={"authorization": f"Bearer {issued['key']}"}, timeout=30,
    ).json()["run_id"]
    anon = httpx.post(
        f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/runs", json=BUILTIN_INPUT, timeout=30,
    ).json()["run_id"]

    conn = await asyncpg.connect(normalize_database_url(_db_url()))
    try:
        found = await conn.fetch(
            "SELECT id::text, api_key_id::text FROM runs WHERE id = ANY($1::uuid[])",
            [keyed, anon])
        rows = {row["id"]: row["api_key_id"] for row in found}
    finally:
        await conn.close()
        httpx.delete(f"{_keys_url(BUILTIN_AGENT)}/{issued['key_id']}", timeout=30)

    assert rows[keyed] == issued["key_id"]
    assert rows[anon] is None
