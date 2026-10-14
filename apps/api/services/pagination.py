"""游標分頁的編解碼，契約 §0 的形狀。

**抽出來是為了只有一種游標。** 每條列表端點自己實作的話，格式遲早會分岔，
而前端拿到的游標長得一樣卻不能互換 —— 那種錯只會在換頁時出現，很難查。

游標是 `(created_at, id)` 的 base64：單看 `created_at` 不夠，同一毫秒建立的兩筆
會在換頁邊界重複出現或被跳過。
"""

import base64
import json
import uuid
from datetime import datetime

from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError


def encode(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created_at, row_id = json.loads(raw)
        return datetime.fromisoformat(created_at), uuid.UUID(row_id)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ZentraError(
            ErrorCode.VALIDATION_ERROR, "cursor is invalid", 422,
            details={"field": "cursor"},
        ) from exc
