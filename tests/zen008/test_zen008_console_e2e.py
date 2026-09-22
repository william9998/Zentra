"""Z8-28 / Z8-29：Console 對**真後端**的瀏覽器 E2E。

環境裡沒有 Playwright，ADR-0006 也不准往 repo 加 npm 依賴，所以用 node 24 內建的
WebSocket 直驅 headless Chrome（`cdp_zen008_console.mjs` + `cdp_lib.mjs`），
與 ZEN-006A 同一套作法。

driver 只記錄具名檢查，pass/fail 全部在這裡判；`ok is None`（沒觀察到）一律 skip，
**絕不當成 pass**。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.zen008.conftest import require_registry
from tests.zen008.fixtures import images

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).resolve().parent / "cdp_zen008_console.mjs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

#: Registry 裡不存在的 digest —— 用來驗 Console 怎麼顯示 BUILD_FAILED。
BAD_IMAGE_REF = "registry.example.com/acme/nope@sha256:" + "ab" * 32

EXPECTED_CHECKS = [
    "env.chrome_version",
    # Z8-28 註冊
    "agents.create_dialog_creates_metadata_only",
    "agents.create_uses_the_contract_endpoint",
    "version_new.has_no_upload_or_credential_fields",
    "version_new.asks_for_semver_and_canonical_image_ref",
    "version_new.form_exposes_exactly_semver_and_image_ref",
    "version_new.confirms_immutability_before_submitting",
    "version_new.request_body_is_exactly_semver_and_image_ref",
    "version_new.request_carries_no_trigger_or_credential_header",
    "version_new.polls_building_to_published",
    "version_new.full_canonical_digest_is_available_and_does_not_overflow",
    "version_new.failure_shows_build_failed_message_and_stage",
    "version_new.tag_only_reference_is_rejected_in_the_form",
    # Z8-29 呼叫
    "agent_detail.shows_three_facade_endpoints",
    "agent_detail.shows_full_digest",
    "agent_detail.only_published_versions_are_selectable",
    "agent_detail.entrypoints_come_from_the_published_manifest",
    "run_composer.schema_error_keeps_the_user_on_the_form",
    "run_composer.navigates_to_the_run_observation_page",
    "run_composer.request_body_has_no_trigger_or_container_fields",
    "run_composer.request_has_no_ingress_discriminator_header",
    "run_observation.reaches_terminal_state_over_real_sse",
    "run_observation.payload_is_rendered_as_text_not_dom",
    "scope.console_never_touches_internal_or_mcp",
]


@pytest.fixture(scope="session")
def e2e(tmp_path_factory, image_refs: dict):
    base = os.getenv("ZENTRA_TEST_BASE_URL")
    if not base:
        pytest.skip("Set ZENTRA_TEST_BASE_URL to run the ZEN-008 browser E2E")
    if shutil.which("node") is None:
        pytest.skip("node is required to drive Chrome over CDP")
    if not Path(CHROME).exists():
        pytest.skip(f"headless Chrome not found at {CHROME}")
    require_registry()

    out = tmp_path_factory.mktemp("zen008") / "e2e.json"
    result = subprocess.run(
        ["node", str(DRIVER), "--base", base, "--out", str(out),
         "--image-ref", image_refs["valid_v1"], "--bad-image-ref", BAD_IMAGE_REF],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    if not out.exists():
        pytest.fail(f"driver produced no result (rc={result.returncode})\n"
                    f"STDOUT:\n{result.stdout[-4000:]}\nSTDERR:\n{result.stderr[-4000:]}")
    return json.loads(out.read_text())


@pytest.fixture(scope="session")
def checks(e2e) -> dict:
    return {item["id"]: item for item in e2e["checks"]}


def test_driver_completed_without_a_fatal_error(e2e) -> None:
    assert e2e["fatal"] is None, e2e["fatal"]


def test_all_expected_checks_were_recorded(checks) -> None:
    """driver 悄悄少記一個檢查，套件必須變紅，不能靜靜縮水。"""
    missing = [name for name in EXPECTED_CHECKS if name not in checks]
    extra = [name for name in checks if name not in EXPECTED_CHECKS]
    assert missing == [], f"driver stopped recording: {missing}"
    assert extra == [], f"undeclared checks: {extra}"


@pytest.mark.parametrize("name", EXPECTED_CHECKS)
def test_browser_check(checks, name: str) -> None:
    check = checks.get(name)
    assert check is not None, f"{name} was never recorded"
    detail = json.dumps(check["detail"], ensure_ascii=False)[:2000]
    if check["ok"] is None:
        pytest.skip(f"{name}: 沒有觀察到，回報為未驗證而不是通過（detail={detail[:400]}）")
    assert check["ok"] is True, f"{name} failed\ndetail={detail}"


async def test_console_run_is_recorded_as_api_trigger(e2e) -> None:
    """Z8-29 的後端半邊：Console 走同一個 REST 端點，所以 trigger 必須是 `API`。

    擋住的錯：有人為了「知道是 Console 來的」而加一個 body/header discriminator——
    Q-008 已經裁決不準（可偽造的 header 不是 caller identity）。
    """
    import uuid

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.api.models import Run
    from tests.zen008.conftest import db_url

    run_id = e2e.get("runId")
    assert run_id, e2e
    engine = create_async_engine(db_url())
    try:
        async with async_sessionmaker(engine)() as session:
            run = (await session.execute(select(Run).where(
                Run.id == uuid.UUID(run_id)))).scalar_one()
        assert run.trigger == "API", run.trigger
        assert run.status == "SUCCEEDED", run.status
        assert str(run.agent_id) == e2e["agentId"]
    finally:
        await engine.dispose()
