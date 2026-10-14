"""ZEN-019：純函式層。**不碰網路、不碰 DB。**

每一條擋的是一個具體的錯：

  P-1  位址分類漏一個網段        → SSRF：沙盒經 `app` 觸達 postgres / redis
  P-2  只看第一個解析結果        → round-robin DNS 把內網位址藏在第二筆
  P-3  host 白名單有逃生門       → 白名單的價值全部來自「預設拒絕」
  P-4  method 悄悄正規化         → 「我明明寫了」與「實際存了什麼」對不起來
  P-5  `last_four` 洩漏短值      → 遮罩必須等長且不是明文
  P-6  config 鍵名／大小沒守住   → 這份文件跟著每一筆 Run 走
"""

from __future__ import annotations

import json

import pytest

from apps.api.errors import ZentraError


# ── P-1 / P-2：位址分類（Z19-28 的純函式版）─────────────────────────────

BLOCKED_ADDRESSES = [
    "127.0.0.1",          # loopback
    "10.0.0.5",           # private A
    "172.17.0.2",         # private B —— docker 預設橋接網段，postgres 就在這種位址上
    "192.168.1.1",        # private C
    "169.254.169.254",    # cloud metadata
    "169.254.0.1",        # link-local
    "::1",                # IPv6 loopback
    "fd00::1",            # IPv6 ULA
    "fe80::1",            # IPv6 link-local
    "::ffff:127.0.0.1",   # IPv4-mapped loopback —— 不攤平就會被當成普通 IPv6 放行
    "::ffff:10.0.0.5",
    "0.0.0.0",
    "100.64.0.1",         # CGNAT（is_private 涵蓋）
    "not-an-ip",          # 解析不出來的東西一律當不安全
]

PUBLIC_ADDRESSES = [
    "1.1.1.1", "8.8.8.8", "104.16.0.1", "2606:4700:4700::1111",
]


@pytest.mark.parametrize("address", BLOCKED_ADDRESSES)
def test_private_and_special_addresses_are_not_public(address: str) -> None:
    """P-1。proxy 是從 `app` 發出去的，而 `app` 同時掛在 `zentra-net` 上 ——
    這張表漏一列就是把資料層開給沙盒。"""
    from apps.api.services.egress import is_public_address

    assert is_public_address(address) is False, address


@pytest.mark.parametrize("address", PUBLIC_ADDRESSES)
def test_public_addresses_pass(address: str) -> None:
    """反向斷言。少了它，一個「全部拒絕」的實作也會全綠。"""
    from apps.api.services.egress import is_public_address

    assert is_public_address(address) is True, address


def test_any_unsafe_address_blocks_the_whole_resolution() -> None:
    """P-2。round-robin 的 DNS 可以把內網位址藏在第二筆。"""
    from apps.api.services.egress import first_blocked_address

    assert first_blocked_address(["1.1.1.1", "8.8.8.8"]) is None
    assert first_blocked_address(["1.1.1.1", "127.0.0.1"]) == "127.0.0.1"
    assert first_blocked_address(["10.0.0.1", "1.1.1.1"]) == "10.0.0.1"
    assert first_blocked_address([]) is None      # 空清單由呼叫端另外處理


# ── P-3：host 白名單（Z19-20）──────────────────────────────────────────

@pytest.mark.parametrize("host", [
    "*.binance.com", "*", "https://api.binance.com", "api.binance.com:443",
    "api.binance.com/api", "API.Binance.com", "1.1.1.1", "2606:4700::1111",
    "", "a" * 254, "nodot", "-bad.example.com",
])
def test_invalid_hosts_are_rejected(host: str) -> None:
    from apps.api.services.egress_rules import validate_host

    with pytest.raises(ZentraError) as exc:
        validate_host(host)
    assert exc.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize("host", ["api.binance.com", "www.okx.com", "smtp.gmail.com"])
def test_valid_hosts_pass(host: str) -> None:
    from apps.api.services.egress_rules import validate_host

    assert validate_host(host) == host


def test_ipv6_literal_says_it_is_an_ip_not_a_port() -> None:
    """IPv6 字面值本身有冒號。先判 port 的話訊息會指向完全錯的方向。"""
    from apps.api.services.egress_rules import validate_host

    with pytest.raises(ZentraError) as exc:
        validate_host("2606:4700::1111")
    assert "IP literal" in exc.value.message


# ── P-4：methods（Z19-21）──────────────────────────────────────────────

def test_methods_default_to_get_only() -> None:
    from apps.api.services.egress_rules import validate_methods

    assert validate_methods(None) == ["GET"]


@pytest.mark.parametrize("methods", [[], ["PUT"], ["DELETE"], ["PATCH"], ["get"], ["Post"]])
def test_invalid_methods_are_rejected(methods) -> None:
    """`["get"]` 也要擋。悄悄大寫化會讓規則存的值與使用者寫的不一樣。"""
    from apps.api.services.egress_rules import validate_methods

    with pytest.raises(ZentraError):
        validate_methods(methods)


def test_get_and_post_pass() -> None:
    from apps.api.services.egress_rules import validate_methods

    assert validate_methods(["GET", "POST"]) == ["GET", "POST"]


# ── 收件人白名單（Z19-39 的純函式部分）─────────────────────────────────

@pytest.mark.parametrize("recipients", [
    [], ["*@example.com"], ["example.com"], ["a@b.com"] * 11, ["no-at-sign"],
])
def test_invalid_recipients_are_rejected(recipients) -> None:
    from apps.api.services.egress_rules import validate_recipients

    with pytest.raises(ZentraError):
        validate_recipients(recipients)


def test_exact_recipients_pass() -> None:
    from apps.api.services.egress_rules import validate_recipients

    assert validate_recipients(["a@b.com", "c@d.com"]) == ["a@b.com", "c@d.com"]


# ── P-5：last_four（Z19-13 / 契約 §2.7 第 7 點）────────────────────────

def test_last_four_is_the_last_four_characters() -> None:
    from apps.api.services import secrets

    assert secrets.last_four("abcdefghmnop") == "mnop"


@pytest.mark.parametrize("value,expected", [("ab", "**"), ("a", "*"), ("abc", "***")])
def test_short_values_get_an_equal_length_asterisk_mask(value, expected) -> None:
    """契約把既有行為寫死：`*`（U+002A）重複「值的長度」次。"""
    from apps.api.services import secrets

    assert secrets.last_four(value) == expected
    assert secrets.last_four(value) != value


# ── P-6：config 邊界（Z19-03 的純函式部分）─────────────────────────────

def test_config_accepts_any_json_value() -> None:
    from apps.api.services.agent_settings import validate_config

    document = {
        "a": None, "b": True, "c": 1.5, "d": "text", "e": [1, 2, None],
        "f": {"g": {"h": [{"i": 1}]}},
    }
    assert validate_config(document) is document


@pytest.mark.parametrize("key", ["1abc", "a-b", "", "x" * 65, "a b"])
def test_invalid_config_keys_are_rejected(key: str) -> None:
    from apps.api.services.agent_settings import validate_config

    with pytest.raises(ZentraError):
        validate_config({key: 1})


def test_config_key_count_and_size_limits() -> None:
    from apps.api.services.agent_settings import validate_config

    validate_config({f"k{i}": i for i in range(100)})
    with pytest.raises(ZentraError):
        validate_config({f"k{i}": i for i in range(101)})

    big = {"blob": "x" * (64 * 1024)}
    assert len(json.dumps(big)) > 64 * 1024
    with pytest.raises(ZentraError):
        validate_config(big)


@pytest.mark.parametrize("name", ["1abc", "a-b", "", "x" * 65])
def test_invalid_secret_names_are_rejected(name: str) -> None:
    from apps.api.services.agent_secrets import validate_name

    with pytest.raises(ZentraError):
        validate_name(name)


# ── reason 枚舉：不得自創，也不得留下已被刪掉的那個 ────────────────────

def test_reason_enum_matches_the_contract() -> None:
    """`HEADER_NOT_ALLOWED` 在 v0.12.1（U1）被移出這張表 ——
    自帶 header 是 `422 VALIDATION_ERROR`，不是 `EGRESS_BLOCKED`。"""
    from apps.api.enums import EgressReason

    assert {reason.value for reason in EgressReason} == {
        "NO_RULES", "HOST_NOT_ALLOWED", "METHOD_NOT_ALLOWED", "PATH_NOT_ALLOWED",
        "SCHEME_NOT_ALLOWED", "PRIVATE_ADDRESS", "REDIRECT",
        "REQUEST_TOO_LARGE", "RESPONSE_TOO_LARGE", "RUN_CALL_LIMIT",
        "SENDER_NOT_CONFIGURED", "RECIPIENT_NOT_ALLOWED", "BODY_TOO_LARGE",
        "RUN_EMAIL_LIMIT", "HOURLY_EMAIL_LIMIT", "SMTP_AUTH", "TLS_REQUIRED",
    }


def test_egress_blocked_is_not_retryable_and_failed_is() -> None:
    """分界就是「該不該重試」：政策不會因為再打一次就改變。"""
    from apps.api.enums import ErrorCode
    from apps.api.errors import ZentraError as Error

    blocked = Error(ErrorCode.EGRESS_BLOCKED, "x", 403)
    failed = Error(ErrorCode.EGRESS_FAILED, "x", 502)
    assert blocked.body()["error"]["retryable"] is False
    assert failed.body()["error"]["retryable"] is True
