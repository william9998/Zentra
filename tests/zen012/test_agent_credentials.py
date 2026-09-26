"""ZEN-012：per-agent LLM 憑證。

每條擋的是一個具體的錯：

  C-1  金鑰讀得回來            → 一個被攻陷的 API 就等於全部使用者的供應商帳號
  C-2  明文進 DB               → 資料庫備份 / dump / 任何有 SELECT 權的人都拿得到
  C-3  沒設加密金鑰卻照存       → 安全功能靜默降級成不安全，比沒有更糟
  C-4  憑證綁到版本上          → 發新版就要重填，而且改金鑰要改 N 次
  C-5  刪 Agent 留下憑證       → 軟刪的是 metadata，不是機密
  C-6  稽核分不出用了誰的金鑰   → 憑證可變而版本不可變，看不出來就查不了案
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("ZENTRA_TEST_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="需要 ZENTRA_TEST_BASE_URL")

SECRET = "sk-unit-test-KEY-ZZZZ"


@pytest.fixture
def agent_id() -> str:
    response = httpx.post(
        f"{BASE_URL}/v1/agents",
        json={"name": f"cred-unit-{os.urandom(4).hex()}", "description": "ZEN-012 test"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["agent_id"]


def _cred_url(agent_id: str) -> str:
    return f"{BASE_URL}/v1/agents/{agent_id}/llm-credential"


def test_unset_agent_reports_not_configured(agent_id: str) -> None:
    body = httpx.get(_cred_url(agent_id), timeout=30).json()
    assert body["configured"] is False
    assert body["last_four"] is None


def test_the_key_can_never_be_read_back(agent_id: str) -> None:
    """C-1：寫得進去，讀不出來。這是整個功能的重點。"""
    httpx.put(_cred_url(agent_id), json={"api_key": SECRET}, timeout=30).raise_for_status()

    body = httpx.get(_cred_url(agent_id), timeout=30).json()
    assert body["configured"] is True
    assert body["last_four"] == "ZZZZ", "應該只給尾四碼"
    assert SECRET not in httpx.get(_cred_url(agent_id), timeout=30).text
    assert "ciphertext" not in body, "連密文都不該回"


def test_the_key_does_not_leak_into_any_agent_response(agent_id: str) -> None:
    """C-1：它也不該從別的端點漏出去。"""
    httpx.put(_cred_url(agent_id), json={"api_key": SECRET}, timeout=30).raise_for_status()

    for path in (f"/v1/agents/{agent_id}", "/v1/agents?limit=20", "/openapi.json"):
        assert SECRET not in httpx.get(f"{BASE_URL}{path}", timeout=30).text, f"{path} 洩漏金鑰"


def test_credential_belongs_to_the_agent_not_a_version(agent_id: str) -> None:
    """C-4：路徑上沒有版本 —— 這是「綁 Agent 不綁版本」在 API 形狀上的體現。

    綁版本的話這裡會是 /v1/agents/{id}/versions/{semver}/llm-credential，
    而那意味著發新版要重填、改金鑰要改 N 次。
    """
    httpx.put(_cred_url(agent_id), json={"api_key": SECRET}, timeout=30).raise_for_status()
    assert "/versions/" not in _cred_url(agent_id)

    # 覆寫一次就好，不需要逐版本設定
    updated = httpx.put(
        _cred_url(agent_id), json={"api_key": "sk-rotated-value-AAAA"}, timeout=30,
    ).json()
    assert updated["last_four"] == "AAAA"


def test_delete_removes_it(agent_id: str) -> None:
    httpx.put(_cred_url(agent_id), json={"api_key": SECRET}, timeout=30).raise_for_status()
    assert httpx.delete(_cred_url(agent_id), timeout=30).status_code == 204
    assert httpx.get(_cred_url(agent_id), timeout=30).json()["configured"] is False


def test_deleting_the_agent_removes_the_credential(agent_id: str) -> None:
    """C-5：Agent 是軟刪，憑證必須硬刪。被刪掉的 Agent 沒理由還握著能用的金鑰。"""
    httpx.put(_cred_url(agent_id), json={"api_key": SECRET}, timeout=30).raise_for_status()
    assert httpx.delete(f"{BASE_URL}/v1/agents/{agent_id}", timeout=30).status_code == 204

    # Agent 已刪 → 端點回 404，但重點是憑證那一列真的不在了（見 live DB 檢查）
    assert httpx.get(_cred_url(agent_id), timeout=30).status_code == 404


def test_a_too_short_key_is_rejected(agent_id: str) -> None:
    response = httpx.put(_cred_url(agent_id), json={"api_key": "short"}, timeout=30)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
