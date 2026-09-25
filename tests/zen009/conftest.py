"""ZEN-009 驗收套件的共用 fixture。

範圍見 `specs/features/ZEN-009/acceptance.md`。本輪（實作開始前）只寫得出
**靜態 + 單元**那一半；活服務與隔離斷言（acceptance §4 §5 §6 的執行期部分）
要等 @platform-eng 把 `registry` 加進 compose 之後才寫得出證據。

**這裡最重要的一個設計決定**（acceptance §0）：

    「registry 活著」不得以「TCP 連得上」或「HTTP 有回應」判定。

已在驗收機實測：macOS 的 AirPlay Receiver（ControlCenter）LISTEN 在 `*:5000`，
對 `GET /v2/` 回 `HTTP/1.1 403 Forbidden` + `Server: AirTunes/935.7.1`。
**產品 registry 的主機端因此改用 6000**（ADR-0009 R3），但這條探測規則不因此作廢 ——
任何 port 上都可能有別的東西在回應，於是：

  * 正向探測（「registry 在」）必須看到 `Docker-Distribution-Api-Version: registry/2.0`；
  * 反向探測（「非 loopback 連不到」）不得斷言 connection refused ——
    綁 `*` 的服務從 LAN IP 連一樣會有回應（AirPlay 實測 192.168.0.101 回 403）。
    正確的反向斷言是「**得不到 Zentra Registry**」。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "compose.yaml"

#: 產品 registry 的固定位址（spec §5.1：port 寫死，不做成設定項）。
#: 主機端 6000、容器內 5000 —— 見 ADR-0009 R3。
PRODUCT_REGISTRY = os.getenv("ZENTRA_TEST_REGISTRY", "localhost:6000")

#: 驗收自己推進產品 registry 的 repository 前綴。交接單要列出來，讓維運者知道
#: `registrydata` 裡多了什麼（delete.enabled=false + 無 GC ⇒ 刪不掉）。
ACCEPT_REPO_PREFIX = "zentra-zen009-accept"

REGISTRY_API_HEADER = "docker-distribution-api-version"
REGISTRY_API_VALUE = "registry/2.0"


# --------------------------------------------------------------- compose (靜態)


def compose_document() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def compose() -> dict[str, Any]:
    return compose_document()


def compose_service(document: dict[str, Any], name: str) -> dict[str, Any]:
    services = document.get("services") or {}
    if name not in services:
        pytest.fail(
            f"compose.yaml 沒有 `{name}` 服務。ZEN-009 的主體就是把它加進來"
            f"（acceptance §3 Z9-A1）。現有服務：{sorted(services)}"
        )
    return services[name]


# --------------------------------------------------------------- registry 探測


def registry_probe(address: str = PRODUCT_REGISTRY, *, timeout: float = 5.0):
    """回 (is_zentra_registry, status_code, headers)。

    判定條件是 `200` **且** `Docker-Distribution-Api-Version: registry/2.0`。
    少一個都不算 —— 見模組 docstring 的 AirPlay 實測。
    """
    import httpx

    try:
        response = httpx.get(f"http://{address}/v2/", timeout=timeout)
    except Exception as exc:                                  # noqa: BLE001
        return False, None, {"__error__": type(exc).__name__ + ": " + str(exc)}
    headers = {key.lower(): value for key, value in response.headers.items()}
    ok = response.status_code == 200 and headers.get(REGISTRY_API_HEADER) == REGISTRY_API_VALUE
    return ok, response.status_code, headers


def require_product_registry() -> None:
    ok, status, headers = registry_probe()
    if ok:
        return
    server = headers.get("server", "")
    if server and "registry" not in server.lower():
        pytest.fail(
            f"{PRODUCT_REGISTRY} 上有東西在回應，但它不是 Zentra Registry"
            f"（Server: {server}，status={status}）。有別的服務佔著這個 port —— "
            "先找出它是誰（`lsof -nP -iTCP:6000 -sTCP:LISTEN`）。"
            "**這是 fail，不是 skip**：它同時表示 compose 的 127.0.0.1:6000 綁不上去。"
        )
    pytest.skip(
        f"{PRODUCT_REGISTRY} 沒有 OCI Registry（status={status}, "
        f"{REGISTRY_API_HEADER}={headers.get(REGISTRY_API_HEADER)!r}）。"
        "ZEN-009 的 compose registry 尚未上線 → 執行期證據無法產生。"
    )


# --------------------------------------------------------------- docker / 活服務


def docker_available() -> bool:
    probe = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True)
    return probe.returncode == 0


def docker_json(args: list[str]) -> Any:
    raw = subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout
    return json.loads(raw) if raw.strip() else None


def base_url() -> str:
    value = os.getenv("ZENTRA_TEST_BASE_URL")
    if not value:
        pytest.skip("Set ZENTRA_TEST_BASE_URL to run live ZEN-009 tests")
    return value.rstrip("/")


@pytest.fixture(scope="session")
def api():
    import httpx

    with httpx.Client(base_url=base_url(), timeout=90) as client:
        yield client


# --------------------------------------------------------------- 原始碼檢索


#: 註解與 docstring 也要看。`Z9-D1` 的規則是「不得有 URL 常數」，
#: 「只是註解」不是豁免理由 —— 但純說明文字要由人判讀，所以這裡回原始命中行。
def grep_tree(root: Path, pattern: str, *, suffixes: tuple[str, ...]) -> list[str]:
    regex = re.compile(pattern)
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    return hits


PY = (".py",)
WEB = (".js", ".html", ".css", ".mjs")
