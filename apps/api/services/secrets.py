"""靜態加密 —— 使用者交給平台保管的憑證（ZEN-012）。

這裡只做一件事：**把明文換成密文，把密文換回明文。** 誰能呼叫、什麼時候呼叫，
是上層的事。

用 Fernet（AES-128-CBC + HMAC-SHA256），因為它把「加密 + 驗證完整性 + 版本前綴」
綁成一個已經定型的格式 —— 自己拼 AES 是這類程式碼最常見的出錯點。

**沒設定 `ZENTRA_SECRET_KEY` 就拒絕寫入**，不是退回明文儲存。
一個「安全功能在沒設定時靜默降級成不安全」的設計，比沒有那個功能更糟：
使用者會以為金鑰被保護著。
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from apps.api.config import get_settings
from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError


_HOWTO = (
    'generate one with: python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


class SecretsNotConfigured(ZentraError):
    """平台沒設定加密金鑰。

    寫入與讀取都會走到這裡，但**兩邊的意思不一樣**，訊息必須分開講：
    寫入時是「不接受你的憑證」，讀取時是「你存過的憑證現在解不開」。
    先前兩邊共用「cannot store」這句話，讀取失敗時看起來像是使用者沒填 ——
    但他其實填過了，而且平台也收下了。
    """

    def __init__(self, *, reading: bool = False) -> None:
        if reading:
            message = (
                "a credential is stored for this agent but the platform cannot decrypt it: "
                "ZENTRA_SECRET_KEY is not set on the app service. "
                "If it was set before and has since changed or been lost, that credential is "
                f"permanently unreadable -- set a key and register the credential again ({_HOWTO})"
            )
        else:
            message = (
                "the platform cannot store credentials: set ZENTRA_SECRET_KEY on the app "
                f"service ({_HOWTO})"
            )
        super().__init__(ErrorCode.VALIDATION_ERROR, message, 422, details={"field": "api_key"})


@lru_cache
def _cipher(*, reading: bool = False) -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise SecretsNotConfigured(reading=reading)
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ZentraError(
            ErrorCode.INTERNAL_ERROR,
            "ZENTRA_SECRET_KEY is not a valid Fernet key (32 url-safe base64 bytes)",
            500,
        ) from exc


def is_configured() -> bool:
    return bool(get_settings().secret_key)


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """解不開就是錯誤，不是「當成空的繼續跑」。

    解不開通常表示 `ZENTRA_SECRET_KEY` 被換過。那時候該讓呼叫方看到明確的失敗，
    而不是靜靜地退回平台預設金鑰 —— 後者會讓使用者以為自己的金鑰還在用。
    """
    try:
        return _cipher(reading=True).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ZentraError(
            ErrorCode.INTERNAL_ERROR,
            "stored credential cannot be decrypted; ZENTRA_SECRET_KEY may have changed",
            500,
        ) from exc


def last_four(plaintext: str) -> str:
    """給 UI 顯示用的尾碼。短到不足四碼就回傳等長的遮罩，不洩漏長度以外的資訊。"""
    return plaintext[-4:] if len(plaintext) >= 4 else "*" * len(plaintext)
