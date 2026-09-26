"""ZEN-012：靜態加密本身。不需要服務。"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from apps.api.config import get_settings
from apps.api.errors import ZentraError
from apps.api.services import secrets


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "secret_key", Fernet.generate_key().decode(), raising=False)
    secrets._cipher.cache_clear()
    yield
    secrets._cipher.cache_clear()


def test_round_trip(configured) -> None:
    assert secrets.decrypt(secrets.encrypt("sk-abc-123")) == "sk-abc-123"


def test_ciphertext_does_not_contain_the_plaintext(configured) -> None:
    """C-2：這就是「DB dump 拿不到金鑰」的全部理由。"""
    assert "sk-abc-123" not in secrets.encrypt("sk-abc-123")


def test_the_same_plaintext_encrypts_differently_each_time(configured) -> None:
    """Fernet 帶隨機 IV。相同密文會讓人能比對出「這兩個 agent 用同一把金鑰」。"""
    assert secrets.encrypt("same") != secrets.encrypt("same")


def test_storing_is_refused_when_no_secret_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """C-3：沒設定就拒絕，**不是**退回明文儲存。

    一個在沒設定時靜默降級成不安全的安全功能，比沒有那個功能更糟 ——
    使用者會以為金鑰被保護著。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "secret_key", "", raising=False)
    secrets._cipher.cache_clear()

    assert secrets.is_configured() is False
    with pytest.raises(ZentraError) as excinfo:
        secrets.encrypt("sk-anything")
    assert excinfo.value.http_status == 422
    assert "ZENTRA_SECRET_KEY" in excinfo.value.message
    secrets._cipher.cache_clear()


def test_a_tampered_or_foreign_ciphertext_is_an_error_not_a_silent_fallback(configured) -> None:
    """換過加密金鑰時要明確失敗，不能靜靜退回平台預設金鑰。

    靜默退回會讓使用者以為自己的金鑰還在用，而帳其實記在平台頭上。
    """
    other = Fernet(Fernet.generate_key()).encrypt(b"sk-someone-else").decode()
    with pytest.raises(ZentraError) as excinfo:
        secrets.decrypt(other)
    assert excinfo.value.http_status == 500


def test_last_four_does_not_leak_short_keys(configured) -> None:
    assert secrets.last_four("sk-abcdWXYZ") == "WXYZ"
    assert secrets.last_four("ab") == "**", "太短的不該洩漏內容"
