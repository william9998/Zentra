"""ZEN-006A static audit of apps/console against ui-contract v0.4.0 and api-contract v0.2.2.

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
    "run.html",
    "runs.html",
    "static/css/base.css",
    "static/css/layout.css",
    "static/css/tokens.css",
    "static/icons.svg",
    "static/js/api.js",
    "static/js/components/run-timeline.js",
    "static/js/dom.js",
    "static/js/pages/run.js",
    "static/js/pages/runs.js",
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
    "QUOTA_EXCEEDED", "AGENT_NOT_PUBLISHED", "BUILD_FAILED", "SANDBOX_TIMEOUT",
    "SANDBOX_ERROR", "LLM_ERROR", "EGRESS_BLOCKED", "USER_CODE_ERROR", "INTERNAL_ERROR",
}
# ui-contract §2 status labels, character for character.
CONTRACT_STATUS_LABELS = {
    "QUEUED": "排隊中", "SCHEDULING": "配置中", "RUNNING": "執行中",
    "SUCCEEDED": "已完成", "FAILED": "失敗", "CANCELLED": "已取消",
}
# ui-contract §3 sprite: exactly these ten symbols, "不要多抓".
CONTRACT_ICON_IDS = {
    "i-bot", "i-activity", "i-box", "i-upload", "i-copy",
    "i-chevron-right", "i-chevron-down", "i-rotate-cw", "i-square", "i-search",
}
# Values removed in SPEC/api-contract v0.2.0; nobody may render or assert them.
REMOVED_VALUES = ["WAITING_INPUT", "tool.call", "tool.result", "interrupt"]

# BOARD ZEN-006A scope ceiling: only these three v1 endpoints exist for this slice.
ALLOWED_V1_PATTERNS = [
    re.compile(r"^/agents/\{?[^/]*\}?/runs$"),
    re.compile(r"^/runs/\{?[^/]*\}?$"),
    re.compile(r"^/runs/\{?[^/]*\}?/events$"),
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


def test_delivered_file_set_is_exactly_the_zen006a_slice() -> None:
    """S3: no extra screens or components sneaked in from ZEN-006."""
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


def test_tokens_css_matches_ui_contract_section_1() -> None:
    contract = _read(UI_CONTRACT)
    section = contract[contract.index("## 1. 設計 Token"):contract.index("## 2. Run 狀態")]
    expected = _css_tokens(section)
    actual = _css_tokens(_read(CONSOLE / "static/css/tokens.css"))
    assert expected, "failed to parse ui-contract §1 tokens"
    assert set(actual) == set(expected), (
        f"missing={set(expected) - set(actual)} extra={set(actual) - set(expected)}"
    )
    mismatched = {k: (expected[k], actual[k]) for k in expected if expected[k] != actual[k]}
    assert mismatched == {}


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


@pytest.mark.parametrize("name", ["shell.js", "components/run-timeline.js"])
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


def test_api_js_enums_match_api_contract_v022() -> None:
    source = _read(CONSOLE / "static/js/api.js")
    assert set(_frozen_enum(source, "RUN_STATUS").values()) == CONTRACT_RUN_STATUS
    assert set(_frozen_enum(source, "EVENT_TYPE").values()) == CONTRACT_EVENT_TYPES
    assert set(_frozen_enum(source, "ERROR_CODE").values()) == CONTRACT_ERROR_CODES


def test_contract_enums_still_say_what_this_test_asserts() -> None:
    """Guard against the contract moving underneath us without the Console noticing."""
    contract = _read(API_CONTRACT)
    assert "| 契約版本 | `v0.2.2` |" in contract
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


def test_only_the_three_existing_v1_endpoints_are_referenced() -> None:
    """BOARD scope ceiling: POST agents/{id}/runs, GET runs/{id}, GET runs/{id}/events."""
    api = _collapse_concat(_strip_js_comments(_read(CONSOLE / "static/js/api.js")))
    paths = set(re.findall(r"req\(\s*'([^']+)'", api))
    paths.update(re.findall(r"new EventSource\(\s*BASE\s*\+\s*'([^']+)'", api))
    assert paths, "no request paths found -- the extraction regex is broken, not the code"
    for path in sorted(paths):
        stripped = path.split("?")[0].rstrip("/")
        assert any(pattern.match(stripped) for pattern in ALLOWED_V1_PATTERNS), (
            f"out-of-scope endpoint referenced: {path}"
        )
    assert paths == {"/runs/{}", "/agents/{}/runs", "/runs/{}/events"}, paths


def test_unimplemented_endpoints_are_not_called() -> None:
    banned = ["/cancel", "/versions", "/openapi.json", "/mcp/", "listRuns", "cancelRun"]
    for path in JS_FILES + HTML_FILES:
        source = _strip_js_comments(_read(path)) if path.suffix == ".js" else _read(path)
        hits = [name for name in banned if name in source]
        assert hits == [], f"{_rel(path)}: {hits}"


# ------------------------------------------------------- P4 / isolation (X4-X5)


def test_console_never_references_internal_gateway_or_data_layer() -> None:
    banned = ["/internal", "postgres", "redis", ":5432", ":6379", "ZENTRA_RUN_TOKEN"]
    for path in _files():
        source = _read(path)
        hits = [name for name in banned if name in source]
        assert hits == [], f"{_rel(path)}: {hits}"


def test_console_stores_no_long_lived_credentials() -> None:
    """ui-contract §6.4: never put an api_key in localStorage."""
    banned = ["api_key", "apiKey", "Authorization", "Bearer", "secret", "password", "access_token"]
    for path in JS_FILES + HTML_FILES:
        source = _read(path)
        hits = [name for name in banned if name in source]
        assert hits == [], f"{_rel(path)}: {hits}"
    storage_keys = set()
    for path in JS_FILES:
        source = _read(path)
        storage_keys.update(re.findall(r"(?:local|session)Storage\.\w+\(\s*([A-Z_]+|'[^']*')", source))
    assert storage_keys <= {"SIDEBAR_KEY", "STASH_KEY"}, storage_keys


def test_localstorage_stash_is_marked_for_removal() -> None:
    """BOARD deviation 3: the scaffold must be removable in one block after ZEN-004."""
    source = _read(CONSOLE / "static/js/pages/runs.js")
    assert "ZEN-004" in source and "刪除" in source
    assert re.search(r"STASH_KEY\s*=\s*'zentra\.console\.recentRuns'", source)
    stash = re.search(r"unshift\(\{(.*?)\}\);", source, re.S)
    assert stash, "stash record shape not found"
    fields = set(re.findall(r"(\w+)\s*[,:]", stash.group(1)))
    assert fields <= {"runId", "question", "createdAt", "truncate", "toISOString"}, fields


def test_root_redirect_deviation_is_documented_in_code() -> None:
    """BOARD deviation 1: '/' points at /console/runs; the code must say why and when it reverts."""
    static_py = _read(REPO / "apps" / "api" / "static.py")
    assert 'RedirectResponse("/console/runs", status_code=307)' in static_py
    assert "/console/agents" in static_py and "ZEN-006" in static_py


def test_dialog_is_a_native_element_not_a_custom_component() -> None:
    """BOARD deviation 2: allowed only because it is the native <dialog> (ui-contract §7)."""
    source = _read(CONSOLE / "static/js/pages/runs.js")
    assert "<dialog" in source and "showModal()" in source
    assert not (CONSOLE / "static/js/components/new-run-dialog.js").exists()


def test_acceptance_file_is_present_for_this_task() -> None:
    """G5: a REVIEW task without written acceptance criteria cannot be judged."""
    path = REPO / "specs" / "features" / "ZEN-006A" / "acceptance.md"
    assert path.is_file()
    assert "ui-contract" in _read(path)


def test_no_stray_json_config_in_console() -> None:
    assert [
        _rel(path) for path in _files(".json")
    ] == [], "Console is zero-build; no JSON config belongs here"
    json.dumps({})  # keep the import meaningful for linters
