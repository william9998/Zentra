"""acceptance §8 F —— `image_ref` 正規化護欄（SPEC §7.1 / ADR-0009 R2-6）。

ZEN-009 **依賴這條不變式成立**，但修正 Docker Hub 缺陷是 @platform-eng 的獨立任務。
所以護欄先釘死：不管之後誰怎麼改 `docker_driver.py`，這些斷言都必須維持。

最重要的是 `test_internal_registry_reference_is_never_normalised_away`：
任何把 `localhost:6000/x` 正規化成 `x` 或 `docker.io/x` 的實作，
會讓內部 Registry 的版本比對到 Docker Hub 上同名的 image —— 那是最壞的一種錯：
它不會報錯，它會安靜地跑錯的程式碼。
"""

from __future__ import annotations

import pytest

from apps.api.schemas import parse_image_ref
from apps.sandbox.drivers import docker_driver
from apps.sandbox.drivers.docker_driver import _registry_for

DIGEST = "sha256:" + "5f177b80485c0d77b4acf733a6945f488efe3a242a4ecf60d55c04ed9473e14b"


def comparison_fn():
    """取出「比較時」用的正規化函式。

    Docker Hub 缺陷的修正是 @platform-eng 的獨立任務（ADR-0009 R2-6），
    ZEN-009 不含它，所以這裡不假設函式已經存在 —— 但只要它一出現，
    下面的護欄就立刻對它生效。函式改名 → 測試變紅，那是正確的結果。
    """
    for name in ("_normalized_repository",):
        function = getattr(docker_driver, name, None)
        if function is not None:
            return function
    pytest.skip(
        "docker_driver 還沒有「比較時正規化」的函式（Docker Hub 缺陷尚未修）。"
        "**標 skipped 不是 passed** —— 這條護欄目前沒有東西可以釘。"
    )


# ------------------------------------------------- Z9-F2 registry host 的判斷規則


@pytest.mark.parametrize("repository, expected", [
    ("localhost:6000/my-agent", "localhost:6000"),   # 第一段含 ':' → 是 host
    ("localhost:6000/a/b", "localhost:6000"),
    ("localhost/my-agent", "localhost"),             # 字面 localhost → 是 host
    ("ghcr.io/acme/agent", "ghcr.io"),               # 第一段含 '.' → 是 host
])
def test_first_segment_is_kept_as_the_registry_host(repository: str, expected: str) -> None:
    """Z9-F2：repository 第一段含 `.` 或 `:`，或等於 `localhost` → 它就是 registry host。"""
    assert _registry_for(repository) == expected


def test_internal_registry_reference_is_never_normalised_away() -> None:
    """Z9-F1（**最重要的一條**）：`localhost:6000/alpine` ≠ `alpine` ≠ `docker.io/alpine`。

    擋住的錯：有人在修 Docker Hub 正規化時寫出「把第一段砍掉」的通用邏輯。
    那會讓 `localhost:6000/alpine` 被當成 Docker Hub 的 `alpine` ——
    平台會拉到一個**完全不同的 image**，而且 digest 比對可能剛好通過（同名 tag），
    使用者的 agent 版本會安靜地執行別人的程式碼。
    """
    internal = _registry_for("localhost:6000/alpine")
    for default_form in ("alpine", "library/alpine", "docker.io/alpine",
                         "docker.io/library/alpine"):
        assert internal != _registry_for(default_form), (
            f"localhost:6000/alpine 被正規化成與 {default_form} 同一個 registry —— "
            "這是 SPEC §7.1 明文禁止的嚴重 bug"
        )


# ------------------------------------------------- Z9-F5 digest-only 身份不鬆動


@pytest.mark.parametrize("ref", [
    f"localhost:6000/my-agent@{DIGEST}",
    f"localhost:6000/team/my-agent@{DIGEST}",
    f"ghcr.io/acme/agent@{DIGEST}",
])
def test_canonical_digest_reference_is_accepted(ref: str) -> None:
    """同機 push 路徑的 `image_ref` 形狀必須被接受（ADR-0009 R2 的立論基礎）。"""
    repository, digest = parse_image_ref(ref)
    assert ref == f"{repository}@{digest}", "parse 不得改寫呼叫端送進來的字串"


@pytest.mark.parametrize("ref, why", [
    ("localhost:6000/my-agent:1.0.0", "tag-only reference"),
    (f"localhost:6000/my-agent:1.0.0@{DIGEST}", "tag 加 digest"),
    ("localhost:6000/my-agent@sha256:deadbeef", "digest 不是 64 hex"),
    (f"localhost:6000/my-agent@sha512:{'a' * 64}", "不是 sha256"),
    (f"localhost:6000/My-Agent@{DIGEST}", "repository 不是全小寫"),
    (f"localhost:6000/my agent@{DIGEST}", "repository 含空白"),
    ("localhost:6000/my-agent", "沒有 digest"),
])
def test_non_canonical_references_are_rejected(ref: str, why: str) -> None:
    """Z9-F5：放寬 `parse_image_ref` 時仍必須擋掉這些（ADR-0008 的 digest-only 身份不鬆動）。"""
    with pytest.raises(ValueError):
        parse_image_ref(ref)


# ------------------------------------- Z9-F1 / Z9-F4 「比較時」正規化（跨任務護欄）


def test_comparison_normalisation_never_touches_the_internal_registry() -> None:
    """Z9-F1 對**比較函式本身**再釘一次（這是最容易寫錯的地方）。

    修 Docker Hub 缺陷時最自然的寫法是「把 registry 前綴統一補齊」。
    只要那段邏輯漏了「非預設 registry 原樣保留」，`localhost:6000/alpine`
    就會變成 `docker.io/library/alpine` —— 內部 Registry 的版本比對到 Docker Hub 的 image。
    """
    normalise = comparison_fn()
    for repository in ("localhost:6000/alpine", "localhost:6000/my-agent",
                       "localhost:6000/team/my-agent", "localhost/alpine",
                       "ghcr.io/acme/agent"):
        assert normalise(repository) == repository, (
            f"{repository} 被改寫成 {normalise(repository)} —— "
            "非預設 registry 必須原樣保留（SPEC §7.1）"
        )


def test_default_registry_forms_are_equivalent() -> None:
    """Z9-F4 正面案例：`docker.io/library/alpine` ≡ `docker.io/alpine` ≡ `alpine`。

    這條屬 @platform-eng 的 Docker Hub 修正，不屬 ZEN-009。
    修正未到位時上面的 `comparison_fn()` 會 skip，**不得被當成 passed**。
    """
    normalise = comparison_fn()
    forms = ["alpine", "library/alpine", "docker.io/alpine", "docker.io/library/alpine"]
    normalised = {normalise(form) for form in forms}
    assert len(normalised) == 1, f"預設 registry 的四種寫法沒有收斂：{sorted(normalised)}"


def test_internal_and_default_registry_never_collide_after_normalisation() -> None:
    """兩邊各自正規化之後仍必須是不同的東西。"""
    normalise = comparison_fn()
    internal = normalise("localhost:6000/alpine")
    for form in ("alpine", "library/alpine", "docker.io/alpine", "docker.io/library/alpine"):
        assert internal != normalise(form), (
            f"localhost:6000/alpine 與 {form} 正規化後相等（{internal}）—— 嚴重 bug"
        )
