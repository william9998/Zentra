"""純邏輯單元測試 + Z8-15 的 P1 靜態邊界。

這裡不碰網路、不碰 Docker、不碰 DB，所以它們在任何機器上都跑得起來；
但也因此**不能**拿來當隔離或發布的證據（那些在 integration/security 檔案裡）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from apps.sandbox.drivers.base import (
    OCI_RUNTIME_CONTRACT,
    OCI_RUNTIME_EXECUTABLE,
    ImageInspectError,
    ImagePullError,
    SandboxCreateError,
    SandboxMode,
    SandboxSpec,
    parse_digest_reference,
)
from apps.sandbox.drivers.docker_driver import RegistryCredentialStore, _safe_error

REPO_ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "ab" * 32


# ------------------------------------------------------- canonical reference


@pytest.mark.parametrize("image_ref, repository", [
    (f"acme/agent@{DIGEST}", "acme/agent"),
    (f"ghcr.io/acme/agent@{DIGEST}", "ghcr.io/acme/agent"),
    (f"localhost:5010/zentra-qa/agent@{DIGEST}", "localhost:5010/zentra-qa/agent"),
    (f"registry.example.com:5000/a/b/c@{DIGEST}", "registry.example.com:5000/a/b/c"),
])
def test_canonical_digest_references_are_accepted(image_ref: str, repository: str) -> None:
    assert parse_digest_reference(image_ref) == (repository, DIGEST)


@pytest.mark.parametrize("image_ref", [
    "acme/agent:1.0.0",
    f"acme/agent:1.0.0@{DIGEST}",
    f"@{DIGEST}",
    f"acme/agent@sha256:{'ab' * 20}",
    f"acme/agent@sha512:{'ab' * 32}",
    f"acme/agent@{DIGEST.upper()}",
    f"ACME/agent@{DIGEST}",
    f"acme/agent@sha256:{'zz' * 32}",
    "",
    f" acme/agent@{DIGEST}",
    f"acme/agent@{DIGEST}\n",
])
def test_non_canonical_references_are_rejected(image_ref: str) -> None:
    """P5 的最底層閘門。regex 放寬一點點，tag 就會混進來。"""
    with pytest.raises(ValueError):
        parse_digest_reference(image_ref)


# ---------------------------------------------------------------- SandboxSpec


def test_validation_spec_carries_exactly_three_environment_variables() -> None:
    spec = SandboxSpec.for_validation(
        agent_version_id="v1", image_ref=f"acme/agent@{DIGEST}",
        network="zentra-sandbox-net", gateway_url="http://app:8000/internal",
        validation_token="t")
    assert set(spec.env) == {"ZENTRA_GATEWAY_URL", "ZENTRA_AGENT_VERSION_ID",
                             "ZENTRA_VALIDATION_TOKEN"}
    assert spec.mode is SandboxMode.VALIDATE
    assert spec.role == "validation"


def test_run_spec_carries_exactly_three_environment_variables() -> None:
    spec = SandboxSpec.for_run(
        run_id="r1", image_ref=f"acme/agent@{DIGEST}", network="zentra-sandbox-net",
        gateway_url="http://app:8000/internal", run_token="t")
    assert set(spec.env) == {"ZENTRA_GATEWAY_URL", "ZENTRA_RUN_ID", "ZENTRA_RUN_TOKEN"}
    assert spec.mode is SandboxMode.RUN
    assert spec.role == "run"


@pytest.mark.parametrize("extra_env", [
    {"OPENAI_API_KEY": "sk-x"},
    {"DATABASE_URL": "postgresql://zentra:zentra@postgres:5432/zentra"},
    {"ZENTRA_REGISTRY_AUTH": "{}"},
])
def test_spec_refuses_any_extra_environment_variable(extra_env: dict) -> None:
    """P4 的程式面閘門：多塞一個 env 就該炸，不要等到容器跑起來才發現。"""
    with pytest.raises(ValueError):
        SandboxSpec(resource_id="r1", image_ref=f"acme/agent@{DIGEST}",
                    network="net", mode=SandboxMode.RUN,
                    env={"ZENTRA_GATEWAY_URL": "g", "ZENTRA_RUN_ID": "r1",
                         "ZENTRA_RUN_TOKEN": "t", **extra_env})


@pytest.mark.parametrize("override", [
    {"mem_limit": "8g"}, {"pids_limit": 65535},
    {"nano_cpus": 16_000_000_000}, {"workspace_size": "8g"},
])
def test_resource_limits_cannot_be_raised_by_the_caller(override: dict) -> None:
    with pytest.raises(ValueError):
        SandboxSpec(resource_id="r1", image_ref=f"acme/agent@{DIGEST}", network="net",
                    mode=SandboxMode.RUN,
                    env={"ZENTRA_GATEWAY_URL": "g", "ZENTRA_RUN_ID": "r1",
                         "ZENTRA_RUN_TOKEN": "t"}, **override)


def test_resource_id_must_match_the_identifier_in_the_environment() -> None:
    with pytest.raises(ValueError):
        SandboxSpec(resource_id="r1", image_ref=f"acme/agent@{DIGEST}", network="net",
                    mode=SandboxMode.RUN,
                    env={"ZENTRA_GATEWAY_URL": "g", "ZENTRA_RUN_ID": "r2",
                         "ZENTRA_RUN_TOKEN": "t"})


# ----------------------------------------------------------- stage 對應


def test_platform_errors_carry_the_public_build_stage() -> None:
    """`error.details.stage` 只能是 PULL/INSPECT/VALIDATE（契約 §2）。"""
    assert ImagePullError("x").stage == "PULL"
    assert ImageInspectError("x").stage == "INSPECT"
    assert ImagePullError("x").retryable is True
    assert ImageInspectError("x").retryable is False
    assert SandboxCreateError("x", stage="INSPECT", retryable=False).stage == "INSPECT"


# -------------------------------------------------- Z8-19 憑證不進訊息


@pytest.mark.parametrize("message, secret", [
    ("unauthorized: Bearer abcdef123456", None),
    ("failed to auth Basic ZGVtbzpodW50ZXIy", None),
    ("cannot dial https://user:hunter2@registry.example.com/v2/", None),
    ("pull failed for hunter2", "hunter2"),
])
def test_docker_error_messages_are_redacted(message: str, secret: str | None) -> None:
    cleaned = _safe_error(Exception(message), (secret,) if secret else ())
    for leaked in ("abcdef123456", "ZGVtbzpodW50ZXIy", "hunter2"):
        assert leaked not in cleaned, cleaned


def test_missing_credential_secret_is_not_an_error(tmp_path: Path) -> None:
    """公開 Registry 的正常路徑：沒有 secret 檔就是沒有憑證，不該炸。"""
    store = RegistryCredentialStore(tmp_path / "absent.json")
    assert store.resolve("ghcr.io/acme/agent") is None


def test_credential_store_reads_standard_docker_auths(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text('{"auths": {"registry.example.com": {"auth": "ZGVtbzpodW50ZXIy"}}}')
    store = RegistryCredentialStore(path)
    assert store.resolve("registry.example.com/acme/agent") == {
        "username": "demo", "password": "hunter2"}
    assert store.resolve("ghcr.io/acme/agent") is None, "憑證不得外溢到別的 Registry"


# ---------------------------------------------------------- Z8-15 P1 邊界


def _python_sources(*relative: str) -> list[Path]:
    files: list[Path] = []
    for item in relative:
        files.extend(sorted((REPO_ROOT / item).rglob("*.py")))
    return [path for path in files if "__pycache__" not in path.parts]


def test_platform_processes_never_import_or_exec_image_code() -> None:
    """P1：`api` / `worker` 行程不得載入或執行使用者 image 內的模組。

    擋住的錯：為了「只是檢查 build() 在不在」而 import 使用者模組——
    那已經是在平台行程裡執行使用者程式碼了。
    """
    banned_calls = {"exec", "eval", "compile", "__import__"}
    offenders: list[str] = []
    for path in _python_sources("apps/api", "apps/worker"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in banned_calls:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} "
                                     f"{node.func.id}()")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                module = getattr(node, "module", "") or ""
                for candidate in [module, *names]:
                    if candidate.startswith(("runpy", "importlib", "imp",
                                             "apps.sandbox.runtime", "zentra_sdk")):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} "
                                         f"import {candidate}")
    assert not offenders, offenders


def test_worker_only_controls_containers_through_the_driver() -> None:
    """worker 可以叫 Docker，但不可以自己 subprocess 出一個 runtime。"""
    for path in _python_sources("apps/worker"):
        source = path.read_text(encoding="utf-8")
        assert "subprocess" not in source, f"{path.relative_to(REPO_ROOT)} 出現 subprocess"
        assert "os.system" not in source, path.relative_to(REPO_ROOT)


def test_api_process_has_no_docker_access() -> None:
    """api 不掛 docker.sock，也不該 import docker SDK（compose 裡只有 worker 掛）。"""
    for path in _python_sources("apps/api"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                names = [alias.name for alias in node.names]
                assert "docker" not in [module.split(".")[0], *[n.split(".")[0] for n in names]], (
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} api 行程 import 了 docker")


def test_image_runtime_lives_only_in_the_sdk() -> None:
    """`zentra-agent` 的實作屬於 image，不屬於平台。"""
    executable_users = [path.relative_to(REPO_ROOT)
                        for path in _python_sources("apps/api", "apps/worker")
                        if OCI_RUNTIME_EXECUTABLE in path.read_text(encoding="utf-8")]
    assert not executable_users, executable_users
    sdk = (REPO_ROOT / "packages/sdk/zentra_sdk/image_runtime.py").read_text(encoding="utf-8")
    assert "importlib.import_module(\"agent\")" in sdk
    assert OCI_RUNTIME_CONTRACT in sdk


def test_no_removed_enum_value_is_referenced_by_the_platform() -> None:
    """v0.2.0 移除的值不得復活（SPEC §9、契約 §1）。"""
    removed = ("WAITING_INPUT", "tool.call", "tool.result", '"interrupt"', '"tier"',
               '"CONSOLE"')
    offenders = []
    for path in _python_sources("apps/api", "apps/worker", "apps/sandbox",
                                "packages/sdk"):
        source = re.sub(r"#.*", "", path.read_text(encoding="utf-8"))
        source = re.sub(r'"""[\s\S]*?"""', "", source)
        for value in removed:
            if value in source:
                offenders.append(f"{path.relative_to(REPO_ROOT)} → {value}")
    assert not offenders, offenders
