"""流量測試共用的小工具。

這個資料夾的檔名**刻意不用 `test_` 開頭**：pyproject 的 testpaths 是整個 tests/，
用了的話 `make test` 每次都會順便壓一輪。要跑請手動執行，見 README.md。
"""

import math
import os
import subprocess
import sys
from pathlib import Path

# 讓 `python3 tests/load/xxx.py` 能直接 import 平台的枚舉常數 ——
# 狀態字串與錯誤 code 一律引用 apps/api/enums.py，不自己寫字面值。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BASE_URL = os.environ.get("ZENTRA_BASE_URL", "http://localhost:8000")

#: 內建 fixture builtin-echo。走一次性沙盒、不需要 LLM，輸入 {"question": str} 原樣回傳。
#: 它的 Run 會被 `make clean-test-data` 清掉，Agent 本身留著。
BUILTIN_AGENT_ID = "01930000-0000-7000-8000-000000000001"


def percentile(values: list[float], p: float) -> float:
    """nearest-rank，跟 tests/zen000/benchmark_cold_start.py 同一種算法。"""
    ordered = sorted(values)
    return ordered[max(math.ceil(p / 100 * len(ordered)) - 1, 0)]


def summarize_ms(values: list[float]) -> dict:
    """秒 → 毫秒的分位數摘要。"""
    return {
        "count": len(values),
        "min_ms": round(min(values) * 1000, 1),
        "p50_ms": round(percentile(values, 50) * 1000, 1),
        "p95_ms": round(percentile(values, 95) * 1000, 1),
        "p99_ms": round(percentile(values, 99) * 1000, 1),
        "max_ms": round(max(values) * 1000, 1),
    }


def sandbox_inflight() -> int | None:
    """Sandbox Manager 目前登記的併發沙盒數（Redis 的 sandbox:inflight）。

    透過 `docker compose exec` 讀，不從主機直連 6379 —— 主機上若另外跑著 redis
    會被遮蔽，跟 postgres 是同一個坑。讀不到回 None，不讓取樣失敗打斷壓測。
    """
    try:
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "redis",
             "redis-cli", "ZCARD", "sandbox:inflight"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=5, check=True,
        )
        return int(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        return None
