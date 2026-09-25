"""ZEN-002：對跑著的 Gateway 做的憑證邊界驗證。

需要 `ZENTRA_TEST_BASE_URL`。這些是 P4 的斷言，不是功能測試 ——
它們問的是「拿不到的東西真的拿不到嗎」。
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("ZENTRA_TEST_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="需要 ZENTRA_TEST_BASE_URL")

LLM_PATH = "/internal/llm/v1/chat/completions"
BODY = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}


def _post(token: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.post(f"{BASE_URL}{LLM_PATH}", json=BODY, headers=headers, timeout=30)


def test_no_token_is_unauthenticated() -> None:
    """沒有憑證就打得到 LLM 等於平台在幫任何人代付。"""
    response = _post(None)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_bogus_token_is_unauthenticated() -> None:
    response = _post("not-a-real-token")
    assert response.status_code == 401, response.text


def test_the_llm_route_is_not_in_the_public_openapi() -> None:
    """`/internal/*` 不得出現在對外契約裡 —— 它是沙盒的介面，不是產品介面。"""
    spec = httpx.get(f"{BASE_URL}/openapi.json", timeout=30).json()
    internal = [path for path in spec.get("paths", {}) if path.startswith("/internal")]
    assert internal == [], f"Internal Gateway 洩漏進 OpenAPI：{internal}"


def test_the_platform_never_exposes_the_provider_key() -> None:
    """金鑰不該出現在任何對外回應裡。

    這條看起來多餘，但它擋的是「為了除錯把設定 dump 到某個端點」這種事 ——
    那是真的會發生的退化。
    """
    for path in ("/healthz", "/openapi.json"):
        body = httpx.get(f"{BASE_URL}{path}", timeout=30).text
        assert "sk-" not in body, f"{path} 疑似洩漏金鑰"
        assert "ZENTRA_LLM_API_KEY" not in body
