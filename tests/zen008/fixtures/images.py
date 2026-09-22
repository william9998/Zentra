"""建立並 push ZEN-008 驗收用的 OCI fixture image。

為什麼要真的 build + push：ZEN-008 的主線是「digest 註冊 → q.build pull/inspect/validate →
PUBLISHED → 沙盒以同一 digest 執行」。任何 mock 的 image 都會讓 inspect、RepoDigest 比對、
platform 檢查、label 檢查全部失去意義，所以這裡用本機 Registry 與真 Docker daemon。

Registry 是**測試期間**才存在的容器，不是產品 compose 的第五個長駐服務（acceptance §1）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parents[2]

#: 測試 Registry。Colima 的 daemon 在 VM 內，published port 對它就是 localhost。
REGISTRY = os.getenv("ZENTRA_TEST_REGISTRY", "localhost:5010")
REGISTRY_CONTAINER = "zentra-qa-registry"
BASE_IMAGE = "zentra-qa-fixture-base:latest"

#: name -> (agent 原始檔, 目標 repository/tag, 是否加 zentra.image/v1 label, 額外 Dockerfile 指令)
FIXTURES: dict[str, tuple[str, str, bool, str]] = {
    # 主線：兩個 entrypoint、合法的未 compile StateGraph
    "valid_v1": ("agent_valid_v1.py", "zentra-qa/agent:stable", True, ""),
    # 與 valid_v1 同一個 repository **同一個 tag**，內容不同（Z8-07 tag 重指）
    "valid_v2": ("agent_valid_v2.py", "zentra-qa/agent:stable", True, ""),
    # 缺 io.zentra.image-contract label → 必須在 INSPECT 階段失敗
    "no_label": ("agent_valid_v1.py", "zentra-qa/no-label:latest", False, ""),
    # build() 回傳已 compile graph → 必須在 VALIDATE 階段失敗（P2）
    "compiled_graph": ("agent_compiled_graph.py", "zentra-qa/compiled:latest", True, ""),
    # 專供 cache miss / Registry 故障測試刪來刪去，不會影響其他測試的 image
    "cache_probe": ("agent_cache_probe.py", "zentra-qa/cache-probe:latest", True, ""),
    # image 宣告一個**沙盒 uid 可寫**的 VOLUME：用來證明 L4 是否真的只剩 /workspace
    "volume_escape": ("agent_valid_v1.py", "zentra-qa/volume-escape:latest", True,
                      "RUN mkdir -p /escape && chown 65534:65534 /escape\nVOLUME /escape\n"),
    # run 模式 exit 0 但不回寫 result
    "silent_exit": ("agent_valid_v1.py", "zentra-qa/silent:latest", True,
                    "COPY zentra-agent-silent /opt/zentra/bin/zentra-agent\n"
                    "RUN chmod 0555 /opt/zentra/bin/zentra-agent\n"),
    # validate 通過，但 node 執行時拋例外
    "raises": ("agent_raises.py", "zentra-qa/raises:latest", True, ""),
}


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def _build_context(tmp: Path) -> Path:
    context = tmp / "context"
    if context.exists():
        shutil.rmtree(context)
    context.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "packages/sdk/zentra_sdk", context / "zentra_sdk",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(FIXTURE_DIR / "zentra-agent", context / "zentra-agent")
    shutil.copy(FIXTURE_DIR / "zentra-agent-silent", context / "zentra-agent-silent")
    shutil.copy(FIXTURE_DIR / "base.Dockerfile", context / "base.Dockerfile")
    for source, *_ in FIXTURES.values():
        shutil.copy(FIXTURE_DIR / source, context / source)
    return context


def _fixture_dockerfile(source: str, labelled: bool, extra: str = "") -> str:
    label = (
        'LABEL io.zentra.image-contract="zentra.image/v1"\n' if labelled else
        "# 故意不帶 io.zentra.image-contract label\n"
    )
    return (
        f"FROM {BASE_IMAGE}\n"
        "USER root\n"
        f"COPY {source} /app/agent.py\n"
        # image 自己宣告的 port / volume / healthcheck / user 必須全部被平台忽略（Z8-10）
        "EXPOSE 9999\n"
        "VOLUME /app/declared-volume\n"
        'HEALTHCHECK --interval=1s CMD ["/bin/false"]\n'
        'ENTRYPOINT ["/bin/false"]\n'
        'CMD ["not-a-zentra-mode"]\n'
        "USER 0:0\n"
        f"{extra}"
        f"{label}"
    )


def build_all(*, force: bool = False) -> dict[str, str]:
    """回傳 ``{fixture_name: "repository@sha256:..."}``（canonical digest reference）。"""

    cache = FIXTURE_DIR / ".image-refs.json"
    if cache.exists() and not force:
        cached = json.loads(cache.read_text())
        if set(cached) == set(FIXTURES) and _all_present(cached):
            return cached

    tmp = Path(os.getenv("TMPDIR", "/tmp")) / "zentra-qa-fixtures"
    tmp.mkdir(parents=True, exist_ok=True)
    context = _build_context(tmp)
    _run(["docker", "build", "-f", str(context / "base.Dockerfile"),
          "-t", BASE_IMAGE, str(context)])

    refs: dict[str, str] = {}
    for name, (source, repo_tag, labelled, extra) in FIXTURES.items():
        dockerfile = context / f"Dockerfile.{name}"
        dockerfile.write_text(_fixture_dockerfile(source, labelled, extra))
        target = f"{REGISTRY}/{repo_tag}"
        _run(["docker", "build", "-f", str(dockerfile), "-t", target, str(context)])
        _run(["docker", "push", target])
        refs[name] = _digest_ref(target)
    cache.write_text(json.dumps(refs, indent=2, sort_keys=True))
    return refs


def _digest_ref(target: str) -> str:
    """push 後從 RepoDigests 取 canonical reference（不猜、不自己算 sha256）。"""
    raw = _run(["docker", "image", "inspect", target,
                "--format", "{{json .RepoDigests}}"]).stdout
    repository = target.rsplit(":", 1)[0] if "/" in target.rsplit(":", 1)[0] else target
    for candidate in json.loads(raw):
        if candidate.startswith(repository + "@"):
            return candidate
    raise RuntimeError(f"no RepoDigest for {target}: {raw}")


def _all_present(refs: dict[str, str]) -> bool:
    for ref in refs.values():
        probe = subprocess.run(["docker", "image", "inspect", ref],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            return False
    return True


if __name__ == "__main__":
    print(json.dumps(build_all(force="--force" in sys.argv), indent=2))
