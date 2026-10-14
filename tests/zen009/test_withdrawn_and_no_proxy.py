"""acceptance §6 D（平台不得代理 Registry）+ §7 E（R2 撤回項必須不存在）的靜態半邊。

這些是**護欄測試**：它們證明的不是「功能做好了」，而是「某些東西沒有偷偷長出來」。
所以它們在實作開始前就應該是綠的 —— 現在綠，是為了在有人寫回來的那一刻變紅。

活服務半邊（multipart → 422、回應沒有 `artifact.origin`、`/openapi.json` 乾淨、
MCP tool schema 乾淨）要等驗收時對跑著的 compose 跑，不在本檔。
"""

from __future__ import annotations

import pytest

from tests.zen009.conftest import PY, REPO_ROOT, WEB, grep_tree

APPS = REPO_ROOT / "apps"


# --------------------------------------------------------------- §7 E 撤回項


@pytest.mark.parametrize("token", [
    "image_origin",                     # agent_versions 的欄位，R2 取消
    "ZENTRA_MAX_IMAGE_UPLOAD_BYTES",    # 2 GiB 上限，R2 取消
    "ZENTRA_UPLOAD_DIR",                # uploads volume 路徑，R2 取消
    "ZENTRA_INTERNAL_REGISTRY",         # 平台不需要知道 registry 位址
])
def test_apps_contain_no_withdrawn_identifier(token: str) -> None:
    """Z9-E4：R2 撤回的識別字在 `apps/**` 一個都不准出現。"""
    hits = grep_tree(APPS, rf"\b{token}\b", suffixes=PY + WEB)
    assert not hits, f"`apps/**` 出現已撤回的 {token}（ADR-0009 R2）：\n" + "\n".join(hits)


def test_build_stage_vocabulary_has_no_load_or_push() -> None:
    """Z9-E5：`details.stage` 的值域只有 `PULL` / `INSPECT` / `VALIDATE`。

    擋住的錯：有人加回 `LOAD`/`PUSH` 兩個 stage（R1 的 tar 路徑），
    前端與 SDK 就得多兩條分支，而那兩個階段永遠不會發生。
    這裡只比對**被當成 stage 字面值**的寫法，避免誤殺無關的 push/load 用詞。
    """
    hits = grep_tree(APPS, r"""stage\s*[=:]\s*['"](LOAD|PUSH)['"]|['"](LOAD|PUSH)['"]\s*,?\s*#\s*stage""",
                     suffixes=PY)
    hits += grep_tree(APPS, r"""BuildStage\.(LOAD|PUSH)\b""", suffixes=PY)
    assert not hits, "build stage 出現 LOAD/PUSH（R2 撤回）：\n" + "\n".join(hits)


def test_no_multipart_handling_in_the_version_endpoint() -> None:
    """Z9-E1 的靜態半邊：平台**不接收檔案**，就不該有 multipart 的相依。

    `UploadFile` / `File(` / `multipart/form-data` 出現在 `apps/api/**` 等於
    R1 的上傳端點又長回來了。活服務半邊（送 multipart → 422）在驗收時另跑。
    """
    hits = grep_tree(APPS / "api", r"UploadFile|[^a-z]File\(|multipart/form-data", suffixes=PY)
    assert not hits, "api 出現 multipart 上傳的痕跡（R2 撤回）：\n" + "\n".join(hits)


def test_platform_never_parses_a_user_archive() -> None:
    """R1 的顧慮在 R2 之後降級成「沒有機會發生」—— 釘住它。

    平台行程不得用 Python `tarfile` 解析使用者 archive（P1 的鄰居）。
    """
    hits = grep_tree(APPS / "api", r"^\s*import tarfile|^\s*from tarfile", suffixes=PY)
    hits += grep_tree(APPS / "worker", r"^\s*import tarfile|^\s*from tarfile", suffixes=PY)
    assert not hits, "平台行程 import 了 tarfile：\n" + "\n".join(hits)


# ------------------------------------------- §6 D 平台不得代理 Registry（SPEC §5 硬規則 4）


def test_api_never_talks_to_a_registry_over_http() -> None:
    """Z9-D1：`api` 上任何「幫你查 registry」的端點都是沙盒的跳板。

    `api` 同時掛 `zentra-net` 與 `zentra-sandbox-net`。這條不是潔癖，
    是 P4 的唯一保證方式：**Registry 對沙盒不可達要靠「沒有任何一條路可以到」**，
    不是靠沙盒不知道位址。
    """
    hits = [
        hit for hit in grep_tree(
            APPS / "api", r""":5000|/v2/|registry:5000|localhost:6000""", suffixes=PY)
        # `config.py` 是唯一的例外（ADR-0013）：worker 要刪 manifest 就得知道位址，
        # 而 Settings 是兩邊共用的一個類別。**那裡只有字串，沒有任何 HTTP 呼叫** ——
        # 下面那條斷言守的就是這件事。
        if not hit.startswith("apps/api/config.py:")
    ]
    assert not hits, (
        "apps/api/** 出現 registry 位址。即使是註解或常數也不行 —— "
        "它是下一個人寫出代理端點的起點：\n" + "\n".join(hits)
    )

    # 真正的那條線：api 不准自己對 registry 發請求，不管位址寫在哪裡。
    calls = grep_tree(
        APPS / "api",
        r"""(httpx|requests)\.(get|post|put|delete|request)\([^)]*(registry|:5000|:6000)""",
        suffixes=PY)
    assert not calls, "apps/api/** 直接對 registry 發 HTTP：\n" + "\n".join(calls)


def test_worker_only_talks_to_the_registry_to_delete_manifests() -> None:
    """Z9-D5（ADR-0013 改）：worker 現在會打 registry，但**只為了刪 manifest**。

    原本這條是「一律不准」。它擔心的是有人繞過主機的 Docker daemon 自己實作 pull ——
    那會讓 `RepoDigests` 比對（P5 的可重現性證據）失去意義。**那個擔心沒有變。**

    ADR-0013 開的口子很窄：刪整個 Agent 時刪掉它的 manifest。
    所以現在守的是「除了 DELETE manifests 以外，一律不准」。
    """
    calls = grep_tree(
        APPS / "worker",
        r"""(httpx|requests)\.(get|post|put|patch|head|request)\([^)]*(registry|:5000|/v2/)""",
        suffixes=PY)
    assert not calls, (
        "worker 只准對 registry 發 DELETE（刪 manifest）。其他動詞代表有人在自己實作 "
        "pull 或 push，那會繞過 daemon 的 digest 驗證：\n" + "\n".join(calls)
    )

    blobs = grep_tree(APPS / "worker", r"""/v2/[^"']*/blobs/""", suffixes=PY)
    assert not blobs, "worker 不准碰 blob —— 那就是自己實作 pull：\n" + "\n".join(blobs)


def test_console_only_mentions_the_registry_as_plain_text() -> None:
    """Z9-D2：`localhost:6000` 在 Console **只能是說明文字**。

    允許：說明區塊裡的指令字串（必須 `textContent` 寫入）。
    不允許：`fetch` / `XMLHttpRequest` 的目標、`href` / `src` 屬性。
    """
    console = APPS / "console"
    if not console.exists():
        pytest.skip("apps/console 不存在")

    forbidden = grep_tree(
        console,
        r"""(fetch\s*\(|XMLHttpRequest|href\s*=\s*["'][^"']*|src\s*=\s*["'][^"']*)[^"')]*:5000""",
        suffixes=WEB,
    )
    assert not forbidden, (
        "Console 對 registry 發請求或連結它（SPEC §5 硬規則 4）：\n" + "\n".join(forbidden)
    )

    # 既有慣例是 `root.innerHTML = FRAME`（模組層級的固定常數樣板），那是安全的。
    # 真正的風險是把**變數**插進 innerHTML —— 說明區塊若哪天改成字串插值，
    # `localhost:6000` 就會變成一條 XSS 路徑。只擋這一種。
    unsafe = grep_tree(console, r"""innerHTML\s*=\s*(`[^`]*\$\{|[^A-Z_;\n]*\+|['"][^'"]*['"]\s*\+)""",
                       suffixes=WEB)
    assert not unsafe, (
        "Console 用字串插值/串接寫 innerHTML —— 說明區塊一律 textContent（CLAUDE.md）：\n"
        + "\n".join(unsafe)
    )


def test_repo_has_no_script_that_runs_delete_untagged() -> None:
    """Z9-G7：`registry garbage-collect --delete-untagged` 的腳本護欄。

    擋住的錯：有人寫一個「清理 registry」的便利腳本並加上 `--delete-untagged`。
    tag 覆寫會讓某個 `PUBLISHED` 版本的 manifest 變成 untagged，
    那個 flag 會把它刪掉 —— 版本立刻永久不可執行（ADR-0009 R2-4 第 5 點）。
    說明文件裡出現這個字串是**必要的**（那是禁令本身），所以只掃可執行的東西。
    """
    hits: list[str] = []
    for suffixes in ((".sh",), (".yaml", ".yml"), (".mk",), (".py",)):
        hits += grep_tree(REPO_ROOT / "docker", r"--delete-untagged", suffixes=suffixes)
        hits += grep_tree(APPS, r"--delete-untagged", suffixes=suffixes)
    compose_hits = grep_tree(REPO_ROOT, r"--delete-untagged", suffixes=(".yaml", ".yml"))
    hits += [line for line in compose_hits if "/specs/" not in line]
    assert not hits, (
        "有可執行的東西會跑 --delete-untagged，那是會刪掉 PUBLISHED 版本的指令：\n"
        + "\n".join(hits)
    )
