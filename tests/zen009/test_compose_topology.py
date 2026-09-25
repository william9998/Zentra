"""acceptance §3 A —— compose 拓撲（@platform-eng 的主體交付）。

**這個檔案在 @platform-eng 交付之前是紅的，那正是它的用途**：它是開工閘門，
不是回歸測試。每一條的失敗訊息都直接寫出該怎麼改。

每條測試擋住的是一個具體的錯，不是「檢查一下設定」：

  Z9-A2  registry 接上 zentra-sandbox-net  → 直接打穿 P4，沙盒拿得到 image 來源
  Z9-A3  綁 0.0.0.0                        → 無 TLS 無認證的 registry 上網
  Z9-A4  delete.enabled=true               → 誤刪 PUBLISHED 版本的 manifest 變成可能
  Z9-A5  uploads volume                    → R2 撤回的 tar 上傳偷偷長回來
  Z9-A6  depends_on: registry              → 宣告一個不存在的依賴，掩蓋「平台不連它」這個事實
  Z9-A7  registry 掛 docker.sock           → 等同把主機 root 給了一個沒有認證的服務
"""

from __future__ import annotations

import pytest

from tests.zen009.conftest import compose_service

SANDBOX_NET = "zentra-sandbox-net"
PLATFORM_NET = "zentra-net"


def test_registry_service_exists_and_uses_the_stock_image(compose) -> None:
    """Z9-A1：第 5 個長駐服務，image 固定 `registry:2`（ADR-0009 R1 §1，R2 未改）。"""
    registry = compose_service(compose, "registry")
    assert registry.get("image") == "registry:2", (
        f"registry 必須是上游的 registry:2，不要自己 build 或換基底：{registry.get('image')!r}"
    )
    assert "build" not in registry, "不要為 registry 加 build context（spec §2「明確不做」）"


def test_registry_is_only_on_the_platform_network(compose) -> None:
    """Z9-A2：**絕對不接 `zentra-sandbox-net`**（P4 / SPEC §5 硬規則 3）。

    擋住的錯：有人為了「讓沙盒也能拉 image」把 registry 接上沙盒網段。
    沙盒的 image 是 daemon 給的，沙盒自己不拉 image —— 接上去只會製造一條逃逸路徑。
    """
    registry = compose_service(compose, "registry")
    networks = registry.get("networks")
    assert networks == [PLATFORM_NET], (
        f"registry.networks 必須恰為 ['{PLATFORM_NET}']，實際 {networks!r}。"
        f"接上 {SANDBOX_NET} 是違反 P4 的 bug，不是設定選項。"
    )


def test_registry_publishes_loopback_only(compose) -> None:
    """Z9-A3：只綁 `127.0.0.1:6000`（主機端 6000，容器內仍是 5000）。

    擋住的錯：為了「從筆電也能 push」改成 `6000:5000`（等同 0.0.0.0）。
    內部 Registry 沒有 TLS、沒有認證 —— loopback 綁定本身就是「只限同機」的執行手段。
    要遠端 push 需新 ADR，且該 ADR 必須先解決 TLS 與認證（ADR-0009 R2-1 硬規則 4）。
    """
    registry = compose_service(compose, "registry")
    ports = registry.get("ports")
    assert ports == ["127.0.0.1:6000:5000"], (
        f"registry.ports 必須恰為 ['127.0.0.1:6000:5000']，實際 {ports!r}"
    )


def test_registry_cannot_delete_anything(compose) -> None:
    """Z9-A4：`delete.enabled=false` 寫死 —— 連誤刪都做不到。"""
    registry = compose_service(compose, "registry")
    environment = registry.get("environment") or {}
    if isinstance(environment, list):                      # compose 允許 list 形式
        environment = dict(item.split("=", 1) for item in environment if "=" in item)
    value = environment.get("REGISTRY_STORAGE_DELETE_ENABLED")
    assert str(value).lower() == "false", (
        f"REGISTRY_STORAGE_DELETE_ENABLED 必須是 \"false\"，實際 {value!r}"
    )


def test_registry_data_volume_exists_and_uploads_does_not(compose) -> None:
    """Z9-A5 / Z9-E6：有 `registrydata`，**沒有** `uploads`（R2 撤回 tar 上傳）。"""
    registry = compose_service(compose, "registry")
    mounts = registry.get("volumes") or []
    assert any(str(item).startswith("registrydata:/var/lib/registry") for item in mounts), (
        f"registry 必須把 named volume registrydata 掛在 /var/lib/registry：{mounts!r}"
    )

    declared = compose.get("volumes") or {}
    assert "registrydata" in declared, "頂層 volumes 要宣告 registrydata"
    assert "uploads" not in declared, (
        "`uploads` volume 屬於 ADR-0009 R1 的 tar 上傳路徑，R2 已**撤回**（不是延後）"
    )


@pytest.mark.parametrize("service", ["api", "worker"])
def test_platform_services_do_not_depend_on_the_registry(compose, service: str) -> None:
    """Z9-A6：**沒有任何平台服務會連 registry**（ADR-0009 R2-5 括號段）。

    擋住的錯：加了 `depends_on: registry` 之後，下一個人會合理地以為
    「api 或 worker 會去打 registry」，然後真的寫出一個代理端點 —— 那是 §6 D 要擋的事。
    """
    spec = compose_service(compose, service)
    depends = spec.get("depends_on") or {}
    keys = set(depends) if isinstance(depends, dict) else set(depends)
    assert "registry" not in keys, (
        f"{service} 不得 depends_on registry：registry 的 client 只有主機 daemon "
        f"與使用者 CLI，兩者都走 loopback"
    )


def test_registry_does_not_mount_the_docker_socket(compose) -> None:
    """Z9-A7：registry 不掛 docker.sock；`api` 也仍然沒掛（未退化）。"""
    registry = compose_service(compose, "registry")
    for mount in registry.get("volumes") or []:
        assert "docker.sock" not in str(mount), f"registry 掛了 docker.sock：{mount!r}"

    api = compose_service(compose, "api")
    for mount in api.get("volumes") or []:
        assert "docker.sock" not in str(mount), f"api 掛了 docker.sock（P1/P4 退化）：{mount!r}"


def test_no_upload_era_environment_variables_anywhere(compose) -> None:
    """Z9-S5：R2 取消的三個 env 一個都不准回來。

    平台**不需要知道 registry 位址**（它不連它）。出現這些 env 就表示有人在寫
    「平台去打 registry」的程式碼。
    """
    blob = str(compose)
    for name in ("ZENTRA_INTERNAL_REGISTRY", "ZENTRA_UPLOAD_DIR",
                 "ZENTRA_MAX_IMAGE_UPLOAD_BYTES"):
        assert name not in blob, f"compose 出現已取消的 env {name}（ADR-0009 R2）"
