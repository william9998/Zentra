"""RFC 9562 UUIDv7 for Python 3.12 (no agent/runtime imports)."""

import secrets
import time
from uuid import UUID


def uuid7() -> UUID:
    return UUID(int=(
        ((time.time_ns() // 1_000_000) << 80)
        | (7 << 76) | (secrets.randbits(12) << 64)
        | (2 << 62) | secrets.randbits(62)
    ))
