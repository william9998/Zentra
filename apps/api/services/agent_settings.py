"""per-agent 的可變設定（ZEN-019 / api-contract §2.6）。

**設定不是機密。** 這份 JSON 回得出明文、看得到、而且會完整送進沙盒的
`ctx.config`。API key 與密碼要走 `agent_secrets`（§2.7）——
平台**不掃描內容**，一個猜得不準的警告比沒有警告更糟。

只有整份取代，沒有 `PATCH`：兩種寫入語意等於兩條要測、要寫文件的路徑，
而單租戶下沒有併發編輯的問題。
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.enums import ErrorCode
from apps.api.errors import ZentraError
from apps.api.models import AgentSettings

#: 契約 §2.6 第 3 點。長得像識別字，因為它最終會是 agent 程式碼裡的 `cfg["..."]`。
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
MAX_KEYS = 100
#: 這份文件**跟著每一筆 Run 走**，大文件是每筆 Run 都要付的成本。
MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class SettingsView:
    """`config` 這個 key 一定在，不會是 `null`（契約 §2.6）。"""

    config: dict
    updated_at: datetime | None


def _invalid(message: str, field: str = "config") -> ZentraError:
    return ZentraError(ErrorCode.VALIDATION_ERROR, message, 422, details={"field": field})


def validate_config(config: dict) -> dict:
    """契約 §2.6 第 3 點。**值可以是任何 JSON**，只有鍵名與大小有規則。"""
    if not isinstance(config, dict):
        raise _invalid("config must be a JSON object")
    if len(config) > MAX_KEYS:
        raise _invalid(f"config has {len(config)} top-level keys, the limit is {MAX_KEYS}")
    for key in config:
        if not isinstance(key, str) or not KEY_PATTERN.match(key):
            raise _invalid(
                f"config key {key!r} is invalid: keys must match {KEY_PATTERN.pattern}")
    encoded = json.dumps(config, separators=(",", ":")).encode()
    if len(encoded) > MAX_BYTES:
        raise _invalid(
            f"config is {len(encoded)} bytes, the limit is {MAX_BYTES} "
            "(this document travels with every single Run)")
    return config


async def describe(session: AsyncSession, agent_id: uuid.UUID) -> SettingsView:
    row = await session.get(AgentSettings, agent_id)
    if row is None:
        return SettingsView(config={}, updated_at=None)
    return SettingsView(config=dict(row.config or {}), updated_at=row.updated_at)


async def replace(session: AsyncSession, agent_id: uuid.UUID, config: dict) -> SettingsView:
    """整份取代。**立刻對所有版本生效，下一筆 Run 就是新值。**

    已經在跑的 Run 不受影響 —— 它的 `ctx.config` 在派工那一刻就定稿了。
    """
    validate_config(config)
    now = datetime.now(UTC)
    row = await session.get(AgentSettings, agent_id)
    if row is None:
        row = AgentSettings(agent_id=agent_id, created_at=now)
        session.add(row)
    row.config = config
    row.updated_at = now
    await session.commit()
    return SettingsView(config=dict(config), updated_at=now)


async def config_for(session: AsyncSession, agent_id: uuid.UUID) -> dict:
    """給派工用（bootstrap／工作項／validation）。沒設定過就是 `{}`。

    **每筆 Run 重算**，跟 `default_model` 同一條規則：中途改設定，下一筆就生效。
    """
    row = (await session.execute(
        select(AgentSettings.config).where(AgentSettings.agent_id == agent_id)
    )).scalar_one_or_none()
    return dict(row or {})
