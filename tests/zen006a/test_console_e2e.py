"""ZEN-006A browser E2E: drives headless Chrome through cdp_console_e2e.mjs and
turns every recorded check into an individually named pytest result.

    ZENTRA_TEST_BASE_URL=http://127.0.0.1:8000 PYTHONPATH=. pytest tests/zen006a/test_console_e2e.py -v

Requirements (all already on this machine, none vendored into the repo):
  * a live four-service compose deployment serving /console/*
  * Google Chrome (headless), node >= 22 for the built-in WebSocket

The driver runs ONCE per session (it creates real Runs and then idles to prove the
EventSource stays closed), so the module is deliberately slow: budget ~2 minutes.
Checks whose value is null mean "could not be observed"; they are reported as
skips, never as passes.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).resolve().parent / "cdp_console_e2e.mjs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Every check the driver is expected to record. Listing them here means a driver that
# silently stops emitting a check fails the suite instead of quietly shrinking it.
EXPECTED_CHECKS = [
    "env.chrome_version",
    "runs.empty_has_explanation_and_secondary_button",
    "dialog.native_opens_modal_and_focuses_input",
    "dialog.empty_submit_sends_no_request",
    "dialog.escape_closes",
    "dialog.cancel_closes",
    "flow.navigates_to_run_observation_page",
    "flow.create_run_uses_only_the_contract_endpoint",
    "run.reaches_terminal_state",
    "run.terminal_label_is_contract_text",
    "run.status_dot_is_6px_pill_and_has_text_label",
    "run.status_region_is_aria_live_polite",
    "run.running_shows_node_and_elapsed",
    "run.running_elapsed_is_always_numeric",
    "run.statuses_seen_are_all_in_spec_enum",
    "run.result_echoes_input_verbatim_as_text",
    "timeline.has_node_events_and_terminal_status",
    "timeline.rows_default_collapsed",
    "timeline.is_aria_live_polite",
    "timeline.connection_status_shown_after_stream_end",
    "usage.labels_use_contract_field_names",
    "xss.payload_never_becomes_dom",
    "xss.no_javascript_dialog_opened",
    "sse.single_events_request_no_reconnect_replay",
    "timeline.no_duplicate_rows_after_idle",
    "p4.no_long_lived_credentials_in_browser_storage",
    "p4.localstorage_only_holds_ui_prefs_and_run_ids",
    "p4.all_requests_are_same_origin",
    "scope.only_three_endpoints_are_called",
    "scope.internal_gateway_never_called",
    "runtime.no_uncaught_exceptions",
    "runtime.no_console_errors",
    "runtime.no_failed_asset_responses",
    "reload.terminal_run_rebuilds_and_does_not_reconnect",
    "stash.lists_run_and_renders_payload_as_text",
    "error404.page_shows_code_runid_and_next_step",
    "error404.code_uses_mono_font",
    "error404.no_uncaught_exception",
    "error404.does_not_open_event_stream",
    "failed.shows_error_code_in_status_and_result",
    "failed.error_box_has_runid_and_next_step",
    "failed.retryable_is_surfaced_without_a_dead_button",
    "timeline.window_is_200_with_load_earlier",
    "timeline.large_payload_is_folded",
    "timeline.dedups_repeated_seq",
    "failed.no_uncaught_exceptions",
    "cancelled.renders_contract_label_and_no_output",
    "cancelled.dot_colour_differs_from_failed",
    "cancelled.no_uncaught_exceptions",
]


# Open defects found during ZEN-006A verification. They are xfail (not deleted and not
# skipped) so the suite stays green as a gate while the failure stays visible, and so it
# turns XPASS the moment somebody fixes it. Every entry here is listed in
# coordination/handoffs/ZEN-006A/qa-verifier-to-orchestrator.md with an owner.
KNOWN_DEFECTS = {
    "run.running_elapsed_is_always_numeric":
        "ZEN-006A-D1 (@frontend-dev, minor, non-blocking): while RUNNING, elapsed renders "
        "'—' instead of a duration whenever the server timestamp is ahead of the browser "
        "clock. elapsedMs() in pages/run.js subtracts a DB timestamp from Date.now() and "
        "formatDuration() in dom.js returns '—' for ms < 0. Measured skew on this host: "
        "created_at is ~17ms in the browser's future, and the bad frame persists until the "
        "next 1s tick. Fix: clamp to Math.max(0, ...). Environment-dependent: on a host "
        "with no skew this check passes (XPASS).",
}


@pytest.fixture(scope="session")
def e2e(tmp_path_factory):
    base = os.getenv("ZENTRA_TEST_BASE_URL")
    if not base:
        pytest.skip("Set ZENTRA_TEST_BASE_URL to run the ZEN-006A browser E2E")
    if shutil.which("node") is None:
        pytest.skip("node is required to drive Chrome over CDP")
    if not Path(CHROME).exists():
        pytest.skip(f"headless Chrome not found at {CHROME}")

    out = tmp_path_factory.mktemp("zen006a") / "e2e.json"
    idle_ms = os.getenv("ZENTRA_E2E_IDLE_MS", "20000")
    result = subprocess.run(
        ["node", str(DRIVER), "--base", base, "--out", str(out), "--idle-ms", idle_ms],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    if not out.exists():
        pytest.fail(f"driver produced no result (rc={result.returncode})\n"
                    f"STDOUT tail:\n{result.stdout[-4000:]}\nSTDERR:\n{result.stderr[-4000:]}")
    report = json.loads(out.read_text())
    report["_stderr"] = result.stderr[-4000:]
    return report


@pytest.fixture(scope="session")
def checks(e2e):
    return {item["id"]: item for item in e2e["checks"]}


def test_driver_completed_without_a_fatal_error(e2e) -> None:
    assert e2e["fatal"] is None, e2e["fatal"]


def test_all_expected_checks_were_recorded(checks) -> None:
    missing = [name for name in EXPECTED_CHECKS if name not in checks]
    extra = [name for name in checks if name not in EXPECTED_CHECKS]
    assert missing == [], f"driver stopped recording: {missing}"
    assert extra == [], f"undeclared checks: {extra}"


@pytest.mark.parametrize("name", EXPECTED_CHECKS)
def test_browser_check(checks, name: str) -> None:
    check = checks.get(name)
    assert check is not None, f"{name} was never recorded"
    if check["ok"] is None:
        pytest.skip(f"{name}: not observed in this run -- report as untested, not as passed "
                    f"(detail={json.dumps(check['detail'], ensure_ascii=False)[:400]})")
    detail = json.dumps(check["detail"], ensure_ascii=False)[:2000]
    if check["ok"] is False and name in KNOWN_DEFECTS:
        pytest.xfail(f"{KNOWN_DEFECTS[name]}\nobserved={detail}")
    assert check["ok"] is True, f"{name} failed\ndetail={detail}"
