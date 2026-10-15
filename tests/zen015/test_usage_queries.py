"""ZEN-015 §3.3：per-key 用量與逐筆除錯查詢。

每條擋的是一個具體的錯：

  U-1  沒歸戶的用量被濾掉      → 總計對不起來，而「這些是誰打的」正是在查的東西
  U-2  金額變成浮點            → 0.30000000000000004。這是帳
  U-3  key 篩選沒生效          → 報表把所有人的用量算到一個人頭上
  U-4  errors_only 濾錯        → 除錯的第一個動作就失效
  U-5  分頁邊界重複或漏        → 翻頁時某幾筆看不到，而那可能正是出錯的那幾筆
  U-6  查得到別的 Agent 的用量  → 跨 Agent 洩漏

這裡的 `llm_calls` 是直接寫進 DB 的。用真的 LLM 呼叫來造資料會把「查詢對不對」
和「供應商今天通不通」綁在一起，那是兩件事。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import httpx
import pytest

BASE_URL = os.environ.get("ZENTRA_TEST_BASE_URL")
DB_URL = os.environ.get("ZENTRA_TEST_DB_URL")
pytestmark = pytest.mark.skipif(
    not BASE_URL or not DB_URL, reason="需要 ZENTRA_TEST_BASE_URL 與 ZENTRA_TEST_DB_URL")

BUILTIN_AGENT = "01930000-0000-7000-8000-000000000001"
BUILTIN_INPUT = {"entrypoint": "default", "input": {"question": "zen015-usage"}}

#: 四筆呼叫，剛好夠分辨所有切面：兩個 model、一個失敗、一筆沒歸戶。
SEEDED = [
    ("gpt-4o-mini", 100, 50, "0.000150", 200, None),
    ("gpt-4o-mini", 200, 80, "0.000300", 200, None),
    ("gpt-4o", 10, 0, "0.000000", 429, "rate limited"),
]
ANON = ("gpt-4o-mini", 30, 10, "0.000045", 200, None)


async def _seed(run_id: str, rows) -> None:
    import asyncpg

    from apps.api.config import normalize_database_url

    conn = await asyncpg.connect(normalize_database_url(DB_URL))
    try:
        async with conn.transaction():
            for model, tin, tout, cost, code, error in rows:
                await conn.execute(
                    "INSERT INTO llm_calls (id, run_id, model, input_tokens, output_tokens,"
                    " cost_usd, latency_ms, status_code, error, credential_source)"
                    " VALUES ($1, $2, $3, $4, $5, $6, 100, $7, $8, 'AGENT')",
                    uuid.uuid4(), run_id, model, tin, tout, cost, code, error)
    finally:
        await conn.close()


def _start_run(key: str | None) -> str:
    headers = {"authorization": f"Bearer {key}"} if key else {}
    response = httpx.post(f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/runs",
                          json=BUILTIN_INPUT, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()["run_id"]


@pytest.fixture(scope="module")
def seeded() -> dict:
    """一把 key、三筆歸戶的呼叫、一筆沒歸戶的。回傳查詢要用的 id。

    **module 範圍，不是每個測試一次。** 每建一個 Run 就要起一個沙盒容器，
    而這台機器的 Docker 只有不到 2 GB —— 12 個測試各開兩個容器會把 `app` 直接
    OOM 掉（實際發生過，exit 137）。下面的測試全是唯讀查詢，共用一份資料沒有問題。
    """
    issued = httpx.post(f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/api-keys",
                        json={"name": f"usage-{os.urandom(3).hex()}"}, timeout=30).json()
    keyed_run = _start_run(issued["key"])
    anon_run = _start_run(None)
    # 同步 fixture 不在事件迴圈裡，所以這裡可以自己開一個
    asyncio.run(_seed(keyed_run, SEEDED))
    asyncio.run(_seed(anon_run, [ANON]))
    yield {"key_id": issued["key_id"], "keyed_run": keyed_run, "anon_run": anon_run}
    httpx.delete(f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/api-keys/{issued['key_id']}", timeout=30)


def _usage(**params) -> dict:
    return httpx.get(f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/usage",
                     params=params, timeout=30).json()


def _calls(**params) -> dict:
    return httpx.get(f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/llm-calls",
                     params=params, timeout=30).json()


def _row_for(body: dict, key_id: str | None) -> dict | None:
    return next((r for r in body["by_key"] if r["key_id"] == key_id), None)


def test_unattributed_usage_is_a_row_not_a_hole(seeded) -> None:
    """U-1：沒帶 key 的呼叫是 `key_id: null` 的一列，不是被濾掉。"""
    body = _usage()
    anonymous = _row_for(body, None)
    assert anonymous is not None, "沒歸戶的用量不見了——總計就對不起來了"
    assert anonymous["name"] is None

    # 總計必須等於各列之和，否則「濾掉了什麼」就查不出來
    assert sum(r["llm_calls"] for r in body["by_key"]) == body["totals"]["llm_calls"]
    assert (sum(Decimal(r["cost_usd"]) for r in body["by_key"])
            == Decimal(body["totals"]["cost_usd"]))


def test_cost_is_a_decimal_string_not_a_float(seeded) -> None:
    """U-2：金額經過 float 就會變成對不起來的數字。"""
    body = _usage()
    assert isinstance(body["totals"]["cost_usd"], str)
    for row in body["by_key"] + body["by_model"]:
        assert isinstance(row["cost_usd"], str), row
    # 字串直接進 Decimal 不會有誤差
    Decimal(body["totals"]["cost_usd"])


def test_filtering_by_key_narrows_every_facet(seeded) -> None:
    """U-3：篩了 key 之後，三個切面都只剩那把 key 的東西。"""
    body = _usage(api_key_id=seeded["key_id"])
    assert body["totals"]["llm_calls"] == len(SEEDED)
    assert body["totals"]["input_tokens"] == sum(r[1] for r in SEEDED)
    assert [r["key_id"] for r in body["by_key"]] == [seeded["key_id"]]
    assert {r["model"] for r in body["by_model"]} == {r[0] for r in SEEDED}


def test_model_filter_applies_to_the_breakdown(seeded) -> None:
    body = _usage(api_key_id=seeded["key_id"], model="gpt-4o")
    assert [r["model"] for r in body["by_model"]] == ["gpt-4o"]
    assert body["totals"]["llm_calls"] == 1


def test_errors_only_finds_the_failing_call(seeded) -> None:
    """U-4：「這把 key 的哪一筆出錯了」是除錯的第一個動作。"""
    body = _calls(api_key_id=seeded["key_id"], errors_only="true")
    assert len(body["items"]) == 1, body
    failing = body["items"][0]
    assert failing["status_code"] == 429
    assert failing["error"] == "rate limited"
    # 每一列都要帶 run_id，不然查到了也跳不過去
    assert failing["run_id"] == seeded["keyed_run"]
    assert failing["api_key_id"] == seeded["key_id"]


def test_status_code_filter(seeded) -> None:
    assert len(_calls(api_key_id=seeded["key_id"], status_code=429)["items"]) == 1
    assert len(_calls(api_key_id=seeded["key_id"], status_code=200)["items"]) == 2


def test_paging_covers_every_row_exactly_once(seeded) -> None:
    """U-5：翻頁時漏掉的那幾筆，可能正是出錯的那幾筆。"""
    everything = {i["call_id"] for i in _calls(api_key_id=seeded["key_id"], limit=100)["items"]}
    assert len(everything) == len(SEEDED)

    seen, cursor, pages = [], None, 0
    while pages < 10:
        params = {"api_key_id": seeded["key_id"], "limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = _calls(**params)
        seen.extend(i["call_id"] for i in page["items"])
        cursor = page["next_cursor"]
        pages += 1
        if not cursor:
            break
    assert len(seen) == len(set(seen)), "翻頁翻出重複的列"
    assert set(seen) == everything, "翻頁漏了列"


def test_next_cursor_key_is_always_present(seeded) -> None:
    """契約 §0：無下一頁是 `null`，不是把 key 拿掉讓前端猜。"""
    body = _calls(api_key_id=seeded["key_id"], limit=100)
    assert "next_cursor" in body
    assert body["next_cursor"] is None


def test_usage_is_scoped_to_one_agent(seeded) -> None:
    """U-6：查別的 Agent 看不到這些用量。"""
    other = httpx.post(f"{BASE_URL}/v1/agents",
                       json={"name": f"usage-scope-{os.urandom(3).hex()}"}, timeout=30).json()
    body = httpx.get(f"{BASE_URL}/v1/agents/{other['agent_id']}/usage", timeout=30).json()
    assert body["totals"]["llm_calls"] == 0
    assert body["by_key"] == []
    httpx.delete(f"{BASE_URL}/v1/agents/{other['agent_id']}", timeout=30)


def test_runs_can_be_filtered_by_key(seeded) -> None:
    body = httpx.get(f"{BASE_URL}/v1/runs",
                     params={"agent_id": BUILTIN_AGENT, "api_key_id": seeded["key_id"]},
                     timeout=30).json()
    assert [r["run_id"] for r in body["items"]] == [seeded["keyed_run"]]


def test_invalid_key_id_is_a_validation_error() -> None:
    for path in ("usage", "llm-calls"):
        response = httpx.get(f"{BASE_URL}/v1/agents/{BUILTIN_AGENT}/{path}",
                             params={"api_key_id": "not-a-uuid"}, timeout=30)
        assert response.status_code == 422, path
        assert response.json()["error"]["details"]["field"] == "api_key_id"


def test_unknown_agent_is_not_found() -> None:
    missing = "01930000-0000-7000-8000-00000000dead"
    assert httpx.get(f"{BASE_URL}/v1/agents/{missing}/usage", timeout=30).status_code == 404
    assert httpx.get(f"{BASE_URL}/v1/agents/{missing}/llm-calls", timeout=30).status_code == 404
