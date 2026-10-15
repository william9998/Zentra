"""Console 靜態稽核：ui-contract v0.5.3 / api-contract v0.5.0（ZEN-012 更新）。

本檔原本是 ZEN-006A 的範圍護欄（只准兩頁、只准三個端點）。ZEN-008 依契約把
Console 擴成五頁、把 Agent/Version 端點接上，所以**上限跟著擴**——
但它仍然是精確集合與白名單，不是「至少包含」：多一個畫面、多一條端點、
多一個執行期函式庫都要紅。ZEN-006A 三條偏離（`/` 轉址、dialog、localStorage 鷹架）
在 ZEN-008 全部收斂，對應斷言改成「必須已經收斂」。

These tests need no running service: they audit the delivered source for the
ui-contract §6.3 hard rules, §2 status language, §3 sprite rules and the
api-contract enums. Execution-level evidence lives in test_console_routes.py
(live HTTP) and test_console_e2e.py (headless Chrome); a green run here is
necessary but never sufficient.

    PYTHONPATH=. pytest tests/zen006a/test_console_source_contract.py -v
"""

import json
import re
import unicodedata
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
CONSOLE = REPO / "apps" / "console"
UI_CONTRACT = REPO / "specs" / "ui-contract.md"
API_CONTRACT = REPO / "specs" / "api-contract.md"

EXPECTED_FILES = {
    "agent.html",
    "agents.html",
    "run.html",
    "runs.html",
    "version-new.html",
    "static/css/base.css",
    "static/css/layout.css",
    "static/css/tokens.css",
    "static/icons.svg",
    "static/js/api.js",
    # ZEN-019（ui-contract §5.16–§5.19）
    "static/js/components/agent-config-card.js",
    "static/js/components/agent-secrets-card.js",
    "static/js/components/egress-card.js",
    "static/js/components/egress-calls-panel.js",
    "static/js/components/agent-list.js",
    "static/js/components/agent-version-list.js",
    "static/js/components/api-keys-card.js",
    "static/js/components/containers-card.js",
    "static/js/components/create-agent-dialog.js",
    "static/js/components/delete-agent-dialog.js",
    "static/js/components/edit-agent-dialog.js",
    "static/js/components/endpoints-card.js",
    "static/js/components/llm-credential-card.js",
    "static/js/components/run-graph.js",
    "static/js/components/recent-runs.js",
    "static/js/components/run-composer.js",
    "static/js/components/run-timeline.js",
    "static/js/components/usage-panel.js",
    "static/js/components/version-registration.js",
    "static/js/dom.js",
    "static/js/pages/agent.js",
    "static/js/pages/agents.js",
    "static/js/pages/run.js",
    "static/js/pages/runs.js",
    "static/js/pages/version-new.js",
    "static/js/shell.js",
    "static/js/store.js",
}

# api-contract.md §0 / §1 and SPEC §9. Written out so the test fails loudly if
# anyone edits the contract without telling the Console.
CONTRACT_RUN_STATUS = {
    "QUEUED", "SCHEDULING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED",
}
CONTRACT_EVENT_TYPES = {
    "run.status", "node.start", "node.end", "token", "log", "custom", "error",
}
CONTRACT_ERROR_CODES = {
    "UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND", "VALIDATION_ERROR",
    "QUOTA_EXCEEDED", "AGENT_NOT_PUBLISHED", "VERSION_CONFLICT", "BUILD_FAILED",
    "SANDBOX_TIMEOUT", "SANDBOX_ERROR", "LLM_ERROR", "EGRESS_BLOCKED",
    # ZEN-019：政策放行了但上游打不通。與 EGRESS_BLOCKED 的分界是「該不該重試」。
    "EGRESS_FAILED",
    "USER_CODE_ERROR", "INTERNAL_ERROR",
}
# ui-contract §2 status labels, character for character.
CONTRACT_STATUS_LABELS = {
    "QUEUED": "排隊中", "SCHEDULING": "配置中", "RUNNING": "執行中",
    "SUCCEEDED": "已完成", "FAILED": "失敗", "CANCELLED": "已取消",
}
# ui-contract §3 sprite: exactly these symbols, "不要多抓".
CONTRACT_ICON_IDS = {
    "i-bot", "i-activity", "i-box", "i-upload", "i-copy",
    "i-chevron-right", "i-chevron-down", "i-rotate-cw", "i-square", "i-search",
    "i-sun", "i-moon",
}
# Values removed in SPEC/api-contract v0.2.0; nobody may render or assert them.
REMOVED_VALUES = ["WAITING_INPUT", "tool.call", "tool.result", "interrupt", "CONSOLE"]

# ZEN-008 範圍上限：api-contract v0.3.1 裡 Console 用得到的端點，白名單制。
ALLOWED_V1_PATTERNS = [
    re.compile(r"^/agents$"),
    re.compile(r"^/agents/\{?[^/]*\}?$"),
    re.compile(r"^/agents/\{?[^/]*\}?/runs$"),
    re.compile(r"^/agents/\{?[^/]*\}?/versions$"),
    re.compile(r"^/agents/\{?[^/]*\}?/versions/\{?[^/]*\}?$"),
    # ZEN-012：per-agent LLM 憑證（api-contract v0.5.0 §2.1）
    re.compile(r"^/agents/\{?[^/]*\}?/llm-credential$"),
    # ZEN-015：下游 API key 與用量／除錯查詢（api-contract v0.7.0 §2.3、v0.8.0 §2.4）
    re.compile(r"^/agents/\{?[^/]*\}?/api-keys$"),
    re.compile(r"^/agents/\{?[^/]*\}?/api-keys/\{?[^/]*\}?$"),
    re.compile(r"^/agents/\{?[^/]*\}?/usage$"),
    re.compile(r"^/agents/\{?[^/]*\}?/llm-calls$"),
    re.compile(r"^/agents/\{?[^/]*\}?/containers$"),
    re.compile(r"^/agents/\{?[^/]*\}?/containers/\{?[^/]*\}?$"),
    re.compile(r"^/agents/\{?[^/]*\}?/containers/\{?[^/]*\}?/restart$"),
    # ZEN-019：設定／機密／出口白名單／寄件者／稽核（api-contract v0.12.0 §2.6–§2.10）
    re.compile(r"^/agents/\{?[^/]*\}?/config$"),
    re.compile(r"^/agents/\{?[^/]*\}?/secrets$"),
    re.compile(r"^/agents/\{?[^/]*\}?/secrets/\{?[^/]*\}?$"),
    re.compile(r"^/agents/\{?[^/]*\}?/egress-rules$"),
    re.compile(r"^/agents/\{?[^/]*\}?/egress-rules/\{?[^/]*\}?$"),
    re.compile(r"^/agents/\{?[^/]*\}?/email-sender$"),
    re.compile(r"^/agents/\{?[^/]*\}?/egress-calls$"),
    re.compile(r"^/runs$"),
    re.compile(r"^/runs/\{?[^/]*\}?$"),
    re.compile(r"^/runs/\{?[^/]*\}?/events$"),
    # ZEN-008-D5：api.js 定義了 cancelRun()，但 `POST /v1/runs/{id}/cancel` 屬 ZEN-004
    # 尚未實作（實測 404）。目前沒有任何畫面呼叫它，所以列為已知死碼而非阻擋項；
    # 下面 test_cancel_client_targets_an_endpoint_that_exists 讓它保持可見。
    re.compile(r"^/runs/\{?[^/]*\}?/cancel$"),
]


def _files(*suffixes):
    return sorted(
        path for path in CONSOLE.rglob("*")
        if path.is_file() and (not suffixes or path.suffix in suffixes)
    )


def _rel(path: Path) -> str:
    return path.relative_to(CONSOLE).as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_comments(source: str) -> str:
    """Drop // and /* */ comments so audits look at code, not prose."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[\s;)}])//[^\n]*", "", source)


JS_FILES = [path for path in _files(".js")]
CSS_FILES = [path for path in _files(".css")]
HTML_FILES = [path for path in _files(".html")]


# ---------------------------------------------------------------- inventory


def test_delivered_file_set_is_exactly_the_contract_five_pages() -> None:
    """ui-contract 只定義五個畫面。第六頁或多餘元件混進來就該紅。

    例外（**使用者於 2026-09-22 直接授權**，ui-contract 尚未追認）：
    `edit-agent-dialog.js` / `delete-agent-dialog.js` 是 Agent 詳情頁的 metadata
    編輯與刪除，走既有的 `PATCH` / `DELETE /v1/agents/{id}`。沒有新增畫面、沒有新增
    route、沒有新端點。@ui-designer 補進 §5 之前，這兩行就是這條偏離的唯一記號。
    """
    assert {_rel(path) for path in _files()} == EXPECTED_FILES


def test_no_build_step_artifacts() -> None:
    """ui-contract §6.3 rule 2 / ADR-0006: zero build."""
    banned = [
        "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
        "node_modules", "tsconfig.json", "vite.config.js", "webpack.config.js",
    ]
    found = [name for name in banned if (CONSOLE / name).exists()]
    assert found == [], f"build artifacts present: {found}"
    bad_suffixes = [
        _rel(path) for path in _files()
        if path.suffix in {".ts", ".tsx", ".jsx", ".vue", ".scss", ".less"}
    ]
    assert bad_suffixes == []


# ------------------------------------------------- §6.3 rule 1: no libraries


def test_html_loads_no_external_or_third_party_script() -> None:
    for path in HTML_FILES:
        text = _read(path)
        for src in re.findall(r"<script[^>]*\bsrc=\"([^\"]+)\"", text):
            assert src.startswith("/console/static/"), f"{_rel(path)}: {src}"
        assert "<script>" not in text, f"{_rel(path)}: inline script"
        for href in re.findall(r"<link[^>]*\bhref=\"([^\"]+)\"", text):
            assert href.startswith("/console/static/"), f"{_rel(path)}: {href}"


def test_js_imports_are_relative_only() -> None:
    """No bare specifiers (needs a bundler) and no URL imports (needs a CDN)."""
    for path in JS_FILES:
        source = _strip_js_comments(_read(path))
        specs = re.findall(r"""(?:^|\s)(?:import|export)[^;]*?from\s+['"]([^'"]+)['"]""", source)
        specs += re.findall(r"""import\s*\(\s*['"]([^'"]+)['"]""", source)
        for spec in specs:
            assert spec.startswith("./") or spec.startswith("../"), f"{_rel(path)}: {spec}"


def test_no_runtime_library_names_anywhere() -> None:
    banned = ["react", "vue", "alpine", "htmx", "jquery", "lodash", "preact", "svelte"]
    for path in _files():
        lowered = _read(path).lower()
        hits = [name for name in banned if name in lowered]
        assert hits == [], f"{_rel(path)}: {hits}"


# ---------------------------------------------- §6.3 rule 3: innerHTML / XSS


def test_innerhtml_assignments_are_static_constants_only() -> None:
    """The vanilla XSS trap: innerHTML may only receive an interpolation-free constant."""
    for path in JS_FILES:
        source = _strip_js_comments(_read(path))
        for match in re.finditer(r"\.(innerHTML|outerHTML)\s*=\s*([^;]+);", source):
            rhs = match.group(2).strip()
            assert re.fullmatch(r"[A-Z][A-Z0-9_]*", rhs), (
                f"{_rel(path)}: {match.group(1)} assigned non-constant expression {rhs!r}"
            )
            const = re.search(
                rf"const\s+{re.escape(rhs)}\s*=\s*`(.*?)`;", source, flags=re.S
            )
            assert const, f"{_rel(path)}: {rhs} is not a module-level template constant"
            assert "${" not in const.group(1), (
                f"{_rel(path)}: template constant {rhs} interpolates a value"
            )


def test_no_html_injection_sinks() -> None:
    sinks = ["insertAdjacentHTML", "document.write", "eval(", "new Function(", "srcdoc"]
    for path in JS_FILES + HTML_FILES:
        source = _strip_js_comments(_read(path)) if path.suffix == ".js" else _read(path)
        hits = [sink for sink in sinks if sink in source]
        assert hits == [], f"{_rel(path)}: {hits}"


# --------------------------------------------- §6.3 rule 4: no front-end router


def test_no_client_side_router() -> None:
    banned = ["history.pushState", "history.replaceState", "popstate", "hashchange"]
    for path in JS_FILES:
        source = _strip_js_comments(_read(path))
        hits = [name for name in banned if name in source]
        assert hits == [], f"{_rel(path)}: {hits}"


# ------------------------------------ §6.3 rule 5 + §1: tokens are the only source


def _css_tokens(text: str) -> dict:
    tokens = {}
    for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", text):
        tokens[name] = re.sub(r"/\*.*?\*/", "", value).strip()
    return tokens


LIGHT_SELECTOR = ':root[data-theme="light"]'


def _contract_tokens_section() -> str:
    contract = _read(UI_CONTRACT)
    return contract[contract.index("## 1. 設計 Token"):contract.index("## 2. Run 狀態")]


def _without_light(text: str) -> str:
    """去掉淺色覆寫區塊。

    `_css_tokens` 是扁平的（後面的同名 token 會蓋掉前面的），所以不先拿掉淺色區塊
    的話，深色的值就完全沒有被比對到 —— 改壞了也不會有人發現。
    """
    return re.sub(re.escape(LIGHT_SELECTOR) + r"\s*\{[^{}]*\}", "", text, flags=re.S)


def _css_block(text: str, selector: str) -> dict:
    """取出某一個 `selector { … }` 區塊的 token。CSS 與 markdown 圍欄都吃得下。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    match = re.search(re.escape(selector) + r"\s*\{([^{}]*)\}", text)
    assert match, f"block {selector!r} not found"
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", match.group(1))
    }


def test_tokens_css_matches_ui_contract_section_1() -> None:
    expected = _css_tokens(_without_light(_contract_tokens_section()))
    actual = _css_tokens(_without_light(_read(CONSOLE / "static/css/tokens.css")))
    assert expected, "failed to parse ui-contract §1 tokens"
    assert set(actual) == set(expected), (
        f"missing={set(expected) - set(actual)} extra={set(actual) - set(expected)}"
    )
    mismatched = {k: (expected[k], actual[k]) for k in expected if expected[k] != actual[k]}
    assert mismatched == {}


def test_light_theme_block_matches_the_contract() -> None:
    expected = _css_block(_contract_tokens_section(), LIGHT_SELECTOR)
    actual = _css_block(_read(CONSOLE / "static/css/tokens.css"), LIGHT_SELECTOR)
    assert expected and actual == expected


def test_light_theme_covers_every_colour_token() -> None:
    """每一個顏色 token 都要有淺色的值。

    少了一個不會壞掉，它只是**保持深色** —— 在淺色版面上那看起來像一塊莫名其妙的
    暗色，不像少了設定，所以不會有人去找設定。這條就是替那件事出聲。
    """
    css = _read(CONSOLE / "static/css/tokens.css")
    dark = _css_block(css, ":root")
    light = _css_block(css, LIGHT_SELECTOR)
    colours = {
        name for name, value in dark.items()
        if re.match(r"^(#|rgba?\()", value)
    }
    assert colours, "沒有解析到任何顏色 token —— 是解析壞了，不是程式對了"
    assert colours - set(light) == set(), f"缺少淺色值：{sorted(colours - set(light))}"
    assert set(light) - colours == set(), f"淺色多定義了非顏色 token：{sorted(set(light) - colours)}"


def test_non_token_css_has_no_literal_colors_or_sizes() -> None:
    """Only hairline borders, the focus ring and the single media-query breakpoint may be literal."""
    allowed_px = {"1px", "2px", "-1px", "768px"}
    for path in CSS_FILES:
        if path.name == "tokens.css":
            continue
        text = re.sub(r"/\*.*?\*/", "", _read(path), flags=re.S)
        assert re.search(r"#[0-9a-fA-F]{3,8}\b", text) is None, f"{_rel(path)}: literal hex color"
        assert re.search(r"\brgba?\(", text) is None, f"{_rel(path)}: literal rgb() color"
        literals = {value for value in re.findall(r"-?\d+(?:\.\d+)?px", text)}
        assert literals <= allowed_px, f"{_rel(path)}: unexpected px literals {literals - allowed_px}"


# ----------------------------------------- §6.3 rule 6: destroy() really exists


@pytest.mark.parametrize("name", ["shell.js", "components/run-timeline.js",
                                  "components/api-keys-card.js",
                                  "components/usage-panel.js",
                                  "components/containers-card.js"])
def test_components_expose_destroy(name: str) -> None:
    source = _read(CONSOLE / "static/js" / name)
    assert re.search(r"export function mount\(", source), name
    assert re.search(r"\bdestroy\s*\(", source), name


def test_pages_release_resources_on_unload() -> None:
    for name in ["pages/run.js", "pages/runs.js"]:
        source = _read(CONSOLE / "static/js" / name)
        assert "beforeunload" in source and "pagehide" in source, name
        assert re.search(r"const destroy = \(\) =>", source), name
    run_page = _read(CONSOLE / "static/js/pages/run.js")
    assert "subscription.close()" in run_page
    assert "timers.clearAll()" in run_page
    dom = _read(CONSOLE / "static/js/dom.js")
    assert "clearInterval" in dom and "clearTimeout" in dom


def test_event_source_is_closed_on_terminal_status() -> None:
    """Back end ends the stream at terminal state and ignores Last-Event-ID (ZEN-004);
    not closing means infinite reconnect + replay from seq 0."""
    api = _strip_js_comments(_read(CONSOLE / "static/js/api.js"))
    assert "isTerminalStatus(event.status)" in api
    assert re.search(r"isTerminalStatus\(event\.status\)\)\s*\{\s*close\(\);", api), api


# --------------------------------------------------- §2 / §3 visual language


# Covers the ui-contract §0 examples (⏳ U+23F3, ⚙️ U+2699, ✋ U+270B, ✅ U+2705,
# ❌ U+274C, 🔴 U+1F534) plus the wider pictograph blocks. U+2014/U+2026/U+2190/U+2192
# are punctuation and arrows, which the contract itself uses in prose, so they stay legal.
EMOJI_RANGES = [
    (0x2300, 0x23FF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
    (0xFE0F, 0xFE0F), (0x1F000, 0x1FAFF), (0x1F1E6, 0x1F1FF),
]


def test_no_emoji_anywhere_in_console() -> None:
    """ui-contract §0: emoji as icons are absolutely forbidden."""
    for path in _files():
        for index, char in enumerate(_read(path)):
            code = ord(char)
            assert not any(low <= code <= high for low, high in EMOJI_RANGES), (
                f"{_rel(path)}: emoji {char!r} (U+{code:04X}, {unicodedata.name(char, '?')}) at {index}"
            )


def test_status_labels_match_ui_contract_section_2() -> None:
    source = _read(CONSOLE / "static/js/pages/run.js")
    for status, label in CONTRACT_STATUS_LABELS.items():
        assert re.search(
            rf"\[RUN_STATUS\.{status}\]:\s*'{label}'", source
        ), f"missing or wrong label for {status}"


def test_icon_sprite_matches_ui_contract_section_3() -> None:
    sprite = _read(CONSOLE / "static/icons.svg")
    ids = set(re.findall(r'<symbol[^>]*\bid="([^"]+)"', sprite))
    assert ids == CONTRACT_ICON_IDS, f"extra={ids - CONTRACT_ICON_IDS} missing={CONTRACT_ICON_IDS - ids}"
    for block in re.findall(r"<symbol\b.*?</symbol>", sprite, flags=re.S):
        head = block.split(">", 1)[0]
        assert 'viewBox="0 0 24 24"' in head, head
        assert 'stroke="currentColor"' in head, head
        assert 'stroke-width="1.5"' in head, head
        assert "fill=\"none\"" in head, head


def test_status_dot_is_never_colour_only() -> None:
    """§2: every status renders a dot AND a text label."""
    source = _read(CONSOLE / "static/js/pages/run.js")
    assert "status-dot status-dot--" in source
    assert "STATUS_LABEL[run.status]" in source
    layout = _read(CONSOLE / "static/css/base.css") + _read(CONSOLE / "static/css/layout.css")
    assert "--radius-full" in layout


# --------------------------------------------------- api-contract conformance


def _frozen_enum(source: str, name: str) -> dict:
    block = re.search(rf"export const {name} = Object\.freeze\(\{{(.*?)\}}\);", source, re.S)
    assert block, f"{name} not found"
    return dict(re.findall(r"([A-Z_]+):\s*'([^']+)'", block.group(1)))


def test_api_js_enums_match_api_contract() -> None:
    source = _read(CONSOLE / "static/js/api.js")
    assert set(_frozen_enum(source, "RUN_STATUS").values()) == CONTRACT_RUN_STATUS
    assert set(_frozen_enum(source, "EVENT_TYPE").values()) == CONTRACT_EVENT_TYPES
    assert set(_frozen_enum(source, "ERROR_CODE").values()) == CONTRACT_ERROR_CODES


def test_contract_enums_still_say_what_this_test_asserts() -> None:
    """Guard against the contract moving underneath us without the Console noticing."""
    contract = _read(API_CONTRACT)
    # 這是絆線：契約版本一動就該有人回來看 Console 有沒有跟上。
    # v0.4.0–v0.6.0 之間沒人更新它，所以它紅了好幾個版本都沒被當一回事。
    assert "| 契約版本 | `v0.12.1` |" in contract
    for code in CONTRACT_ERROR_CODES:
        assert f"`{code}`" in contract, code
    for status in CONTRACT_RUN_STATUS:
        assert status in contract, status


def _code_only(path: Path) -> str:
    """Strip comments: a comment saying 'this value was removed' is not a usage."""
    text = _read(path)
    if path.suffix == ".js":
        return _strip_js_comments(text)
    if path.suffix == ".css":
        return re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


@pytest.mark.parametrize("value", REMOVED_VALUES)
def test_removed_contract_values_are_absent(value: str) -> None:
    for path in _files():
        assert value not in _code_only(path), (
            f"{_rel(path)} still uses removed value {value}"
        )


def test_config_tier_is_absent() -> None:
    for path in _files():
        assert not re.search(r"\btier\b", _code_only(path)), (
            f"{_rel(path)}: config.tier was removed in v0.2.0"
        )


def test_status_waiting_token_is_declared_but_never_used() -> None:
    for path in CSS_FILES + JS_FILES + HTML_FILES:
        text = _read(path)
        if path.name == "tokens.css":
            assert "--status-waiting" in text
            continue
        assert "--status-waiting" not in text, f"{_rel(path)} uses a removed status token"


def test_contract_field_names_only_appear_inside_api_js() -> None:
    """ui-contract §6.2: snake_case contract fields are read in api.js and nowhere else."""
    snake_member = re.compile(r"\.\s*([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
    for path in JS_FILES:
        if path.name == "api.js":
            continue
        source = _strip_js_comments(_read(path))
        hits = set(snake_member.findall(source))
        assert hits == set(), f"{_rel(path)}: reads contract fields directly: {sorted(hits)}"


# --------------------------------------------------------- scope ceiling (S1)


def _collapse_concat(source: str) -> str:
    """'/agents/' + f(x) + '/runs'  ->  '/agents/{}/runs'  (so paths can be matched whole)."""
    previous = None
    while previous != source:
        previous = source
        source = re.sub(r"'\s*\+\s*[^'\";\n]+?\s*\+\s*'", "{}", source)
    return re.sub(r"'([^'\n]*)'\s*\+\s*[A-Za-z_$][\w.$]*\([^()]*\)", r"'\1{}'", source)


def test_all_network_calls_go_through_api_js() -> None:
    """ui-contract §6.2: api.js is the only place that talks to /v1."""
    for path in JS_FILES:
        source = _strip_js_comments(_read(path))
        if path.name == "api.js":
            continue
        assert "fetch(" not in source, f"{_rel(path)}: direct fetch outside api.js"
        assert "EventSource" not in source, f"{_rel(path)}: direct EventSource outside api.js"
        assert "XMLHttpRequest" not in source, f"{_rel(path)}: XHR outside api.js"
    api = _strip_js_comments(_read(CONSOLE / "static/js/api.js"))
    assert len(re.findall(r"\bfetch\(", api)) == 1
    assert len(re.findall(r"new EventSource\(", api)) == 1


def test_only_whitelisted_v1_endpoints_are_referenced() -> None:
    """Console 不得自創端點，也不得打尚未實作的端點。"""
    api = _collapse_concat(_strip_js_comments(_read(CONSOLE / "static/js/api.js")))
    paths = set(re.findall(r"req\(\s*'([^']+)'", api))
    paths.update(re.findall(r"new EventSource\(\s*BASE\s*\+\s*'([^']+)'", api))
    assert paths, "no request paths found -- the extraction regex is broken, not the code"
    for path in sorted(paths):
        stripped = path.split("?")[0].rstrip("/")
        assert any(pattern.match(stripped) for pattern in ALLOWED_V1_PATTERNS), (
            f"out-of-scope endpoint referenced: {path}"
        )
    assert paths == {
        "/agents", "/agents/{}", "/agents/{}/runs", "/agents/{}/versions",
        "/agents/{}/versions/{}", "/agents/{}/llm-credential",   # ZEN-012
        "/agents/{}/api-keys", "/agents/{}/api-keys/{}",         # ZEN-015
        "/agents/{}/usage", "/agents/{}/llm-calls",              # ZEN-015
        "/agents/{}/containers", "/agents/{}/containers/{}",     # ZEN-015
        "/agents/{}/containers/{}/restart",
        "/agents/{}/config",                                     # ZEN-019
        "/agents/{}/secrets", "/agents/{}/secrets/{}",           # ZEN-019
        "/agents/{}/egress-rules", "/agents/{}/egress-rules/{}", # ZEN-019
        "/agents/{}/email-sender", "/agents/{}/egress-calls",    # ZEN-019
        "/runs", "/runs/{}", "/runs/{}/events",
        "/runs/{}/cancel",   # ZEN-008-D5：死碼，見下方 xfail
    }, paths


def test_unimplemented_endpoints_are_not_called() -> None:
    """`/cancel` 是 ZEN-004；`/mcp/` 是 MCP 協定端點，瀏覽器永遠不該打它。

    （`/versions` 與 `/openapi.json` 在 ZEN-008 已經實作，從這張清單移除；
      `endpoints-card` 只是把 openapi URL **顯示**出來，不是自己去呼叫。）"""
    banned = ["fetch('/mcp", 'fetch("/mcp', "new EventSource('/mcp", "/internal/"]
    for path in JS_FILES + HTML_FILES:
        source = _strip_js_comments(_read(path)) if path.suffix == ".js" else _read(path)
        hits = [name for name in banned if name in source]
        assert hits == [], f"{_rel(path)}: {hits}"


def test_no_page_or_component_calls_the_unimplemented_cancel_client() -> None:
    """真正的風險是「畫面上有一顆按鈕，按下去 404」。定義著沒人用，只是死碼。"""
    callers = [
        _rel(path) for path in JS_FILES
        if path.name != "api.js" and re.search(r"\bcancelRun\b", _strip_js_comments(_read(path)))
    ]
    assert callers == [], f"這些畫面呼叫了尚未實作的 cancel 端點：{callers}"


@pytest.mark.xfail(strict=True, reason=(
    "ZEN-008-D5（次要）：api.js 匯出 cancelRun()，但 POST /v1/runs/{id}/cancel "
    "在 ZEN-008 未實作（實測 404，不在 OpenAPI）。目前無人呼叫。"
    "ZEN-004 補上端點後這條會變 XPASS。"))
def test_cancel_client_targets_an_endpoint_that_exists() -> None:
    api = _strip_js_comments(_read(CONSOLE / "static/js/api.js"))
    openapi_backed = "/cancel" not in api
    assert openapi_backed, "cancelRun() 指向一個後端還沒有的端點"


# ------------------------------------------------------- P4 / isolation (X4-X5)


def test_console_never_references_internal_gateway_or_data_layer() -> None:
    banned = ["/internal", "postgres", "redis", ":5432", ":6379", "ZENTRA_RUN_TOKEN"]
    for path in _files():
        source = _read(path)
        hits = [name for name in banned if name in source]
        assert hits == [], f"{_rel(path)}: {hits}"


#: 允許出現 `api_key` / `apiKey` 的檔案（ZEN-012）。
#:
#: Console 現在**傳輸**一次性的 LLM 憑證（api-contract §2.1），但規則沒鬆：
#: 它只寫不存、不回讀、不留在 DOM。名單刻意只有兩個檔，新增第三個要先想清楚為什麼。
CREDENTIAL_WRITE_PATHS = {
    "static/js/api.js",                              # 契約欄位名只在這裡出現一次
    "static/js/components/llm-credential-card.js",   # 輸入表單
}


def test_console_stores_no_long_lived_credentials() -> None:
    """ui-contract §6.4：憑證不准進瀏覽器儲存，也不准硬寫在原始碼裡。

    `api_key` 在 ZEN-012 之後變成「可以傳，不可以留」——
    所以字串檢查對兩個明列的檔案放行，**真正的規則由下面的儲存斷言守著**。
    其餘的憑證字眼（`Authorization` / `Bearer` / `access_token`）
    一個檔案都不准出現：Console 是同源呼叫，它本來就不該自己組認證標頭。

    **ZEN-019 之後 `secret` 是一個端點的名字**（`/v1/agents/{id}/secrets`），
    不再是「這裡有一把憑證」的訊號 —— 那一層的設計正好相反：
    Console 只看得到機密的**名字**與尾四碼，值讀不回來（api-contract §2.7）。
    所以把它從字眼清單移除，真正的規則由下面的儲存斷言與
    `test_the_secrets_card_never_shows_a_value` 守著。
    """
    always_banned = ["Authorization", "Bearer", "access_token"]
    credential_fields = ["api_key", "apiKey"]

    for path in JS_FILES + HTML_FILES:
        source = _read(path)
        hits = [name for name in always_banned if name in source]
        assert hits == [], f"{_rel(path)}: {hits}"

        # `password` 單獨處理：`type="password"` 是**我們要的**寫法（遮蔽輸入），
        # 禁的是把密碼當資料處理。檢查前先拿掉註解與那個型別宣告 ——
        # 稽核的是程式碼，不是散文。
        code = _strip_js_comments(source).replace('type="password"', "")
        # `password_secret_name` 是**機密的名字**，不是密碼（ADR-0014 M2：
        # 寄件者那張表沒有密碼欄位）。名字本來就該出現在前端，值永遠不會。
        code = code.replace("password_secret_name", "").replace(
            "passwordSecretName", "").replace("passwordSecret", "").replace(
            "passwordHint", "")
        assert "password" not in code, (
            f"{_rel(path)}: 出現 password，且不是 input 型別宣告"
        )

        if _rel(path) not in CREDENTIAL_WRITE_PATHS:
            hits = [name for name in credential_fields if name in source]
            assert hits == [], (
                f"{_rel(path)}: {hits} —— 憑證欄位只准出現在 {sorted(CREDENTIAL_WRITE_PATHS)}"
            )

    # 這才是規則本身：任何憑證都不准進 localStorage / sessionStorage。
    storage_keys = set()
    for path in JS_FILES:
        source = _read(path)
        storage_keys.update(re.findall(r"(?:local|session)Storage\.\w+\(\s*([A-Z_]+|'[^']*')", source))
    # THEME_KEY 與 SIDEBAR_KEY 同類：UI 偏好，不是憑證。§6.4 禁的是憑證。
    assert storage_keys <= {"SIDEBAR_KEY", "STASH_KEY", "THEME_KEY"}, storage_keys


def test_the_credential_card_keeps_no_copy_of_the_key() -> None:
    """金鑰不准存活過送出那一刻。

    擋的是一個很容易寫出來的錯：把輸入值存進 module 變數「之後可能要用」。
    元件只在送出時讀一次 `.value`，成功後立刻清空 —— DOM 與 JS 都不留副本。
    """
    source = _read(CONSOLE / "static/js/components/llm-credential-card.js")

    assert "apiKey.value = ''" in source, "送出後必須清空輸入欄位"
    for banned in ("localStorage", "sessionStorage", "indexedDB"):
        assert banned not in source, f"憑證卡不得使用 {banned}"


def test_localstorage_run_stash_has_been_removed() -> None:
    """ZEN-006A 偏離 D3 的收斂點：Runs 頁改讀真的 `GET /v1/runs`，鷹架必須不見。"""
    for path in JS_FILES:
        source = _strip_js_comments(_read(path))
        assert "zentra.console.recentRuns" not in source, _rel(path)
    storage_keys = set()
    for path in JS_FILES:
        storage_keys.update(re.findall(
            r"(?:local|session)Storage\.\w+\(\s*([A-Z_]+|'[^']*')", _read(path)))
    # SIDEBAR_KEY / THEME_KEY 都是 UI 偏好。這條守的是「Run 資料不准進瀏覽器儲存」，
    # 不是「不准用 localStorage」。
    assert storage_keys <= {"SIDEBAR_KEY", "THEME_KEY"}, storage_keys


def test_root_redirect_deviation_is_resolved() -> None:
    """ZEN-006A 偏離 D1 的收斂點：`/` 轉回 ui-contract 寫的 `/console/agents`。"""
    static_py = _read(REPO / "apps" / "api" / "static.py")
    assert 'RedirectResponse("/console/agents", status_code=307)' in static_py
    assert '"/console/runs", status_code=307' not in static_py


def test_dialogs_are_native_elements_not_custom_components() -> None:
    """ui-contract §7「原生元素優先」：任何 modal 都必須是原生 <dialog>。"""
    dialog_sources = [path for path in JS_FILES
                      if "<dialog" in _read(path) or "showModal()" in _read(path)]
    assert dialog_sources, "找不到任何 dialog，護欄失效"
    for path in dialog_sources:
        source = _read(path)
        assert "<dialog" in source and "showModal()" in source, _rel(path)
    for path in JS_FILES:
        source = _strip_js_comments(_read(path))
        assert 'role="dialog"' not in source and "role='dialog'" not in source, (
            f"{_rel(path)} 自製了 modal，沒有用原生 <dialog>")


def test_no_stray_json_config_in_console() -> None:
    assert [
        _rel(path) for path in _files(".json")
    ] == [], "Console is zero-build; no JSON config belongs here"
    json.dumps({})  # keep the import meaningful for linters


def test_run_list_rows_carry_agent_name() -> None:
    """api-contract v0.11.0：`GET /v1/runs` 每一列有 `agent_name`，Runs 列表要讀得到。

    列表走的是 viewRunSummary，不是 viewRun（單一 Run 的回應根本沒有這個欄位）。
    曾經加錯地方：欄位標題出現了，每一列卻都是 `—`。
    """
    api = _strip_js_comments(_read(CONSOLE / "static/js/api.js"))
    summary = re.search(r"function viewRunSummary\(raw\) \{.*?\n\}", api, flags=re.S)
    assert summary, "api.js 找不到 viewRunSummary"
    assert "agentName: raw.agent_name" in summary.group(0)


# ------------------------------------------- ui-contract v0.11.0 §5.7 卡片格子
#
# 瀏覽器 E2E（cdp_agent_bulk_delete_e2e.mjs）在容器裡會 skip，這兩條是容器裡也守得住的底線。


def _css_rule(text: str, selector: str) -> str:
    match = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^{}]*)\}", text, flags=re.M)
    assert match, f"layout.css 找不到 {selector!r}"
    return match.group(1)


def test_agent_card_is_not_wrapped_in_a_link() -> None:
    """§5.7：名稱 <a> 放在 <h3> 裡、是卡片唯一的連結；不准把整張 <article> 包進 <a>，
    也不准在卡片上掛 click handler 假裝連結（鍵盤進不去）。"""
    source = _strip_js_comments(_read(CONSOLE / "static/js/components/agent-list.js"))
    assert len(re.findall(r"\bel\(\s*'a'", source)) == 1, "agent-list.js 應只建一個 <a>（卡片名稱）"
    assert "<a" not in source, "agent-list.js 的樣板字串裡不該有 <a>"
    assert re.search(r"\bel\(\s*'article'", source) and re.search(r"\bel\(\s*'h3'", source)
    assert re.search(r"\bcard\.addEventListener\(\s*'click'", source) is None


def test_agent_card_click_area_and_grid_follow_the_contract() -> None:
    """§5.7：點卡片任何地方靠 `::after { position: absolute; inset: 0 }` 撐開；
    格子欄數由 --card-min-width 決定、不寫斷點；卡片不准陰影或漸層。"""
    css = re.sub(r"/\*.*?\*/", "", _read(CONSOLE / "static/css/layout.css"), flags=re.S)
    card = _css_rule(css, ".agent-card")
    assert re.search(r"position:\s*relative", card)
    assert "shadow" not in card and "gradient" not in card
    hit_after = _css_rule(css, ".agent-card-hit::after")
    assert re.search(r"position:\s*absolute", hit_after) and re.search(r"inset:\s*0", hit_after)
    grid = _css_rule(css, ".agent-grid")
    assert re.search(
        r"grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(min\(var\(--card-min-width\),\s*100%\),\s*1fr\)\)",
        grid)
    for media in re.finditer(r"@media[^{]*\{(.*?)\n\}", css, flags=re.S):
        assert ".agent-grid" not in media.group(1), "卡片格子不准用 media query 斷點"
    agent_rules = re.findall(r"\.agent-card[^{]*\{([^{}]*)\}", css)
    assert agent_rules and not any("shadow" in r or "gradient" in r for r in agent_rules)
