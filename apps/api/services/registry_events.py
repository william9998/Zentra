"""push 即註冊：內部 registry 的 push 通知 → 註冊 Agent 版本。

約定：`docker push localhost:6000/<agent 名>:<版號>` 就是註冊 `<agent 名>` 的 `<版號>`。
Agent 不存在就自動建立；同版號再 push 就是取代（與 `POST /v1/agents/{id}/versions` 同一條路）。

## 為什麼要回頭問 registry

通知端點沒有 token，而 `/internal/*` 是沙盒連得到的。所以通知內容**不被相信**，
只被當成「去看一下」的觸發：註冊前向 registry 確認 `<repo>:<tag>` 現在真的指向那個 digest。
偽造的通知最多只能讓平台「註冊 registry 裡本來就是那樣的東西」—— 跟真的 push 一樣，無害。

不符合約定的一律略過並記 log（`latest`、`dev` 這種 tag、多層 repo、layer 事件）：
平台不猜。
"""

import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from apps.api import db
from apps.api.config import get_settings
from apps.api.errors import ZentraError
from apps.api.schemas import CreateAgentRequest, CreateAgentVersionRequest
from apps.api.services import agents as agents_service

log = logging.getLogger(__name__)

#: 算「一個版本」的 manifest 類型。layer / config blob 的事件不是版本。
MANIFEST_TYPES = frozenset({
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
})

#: Agent 名稱的規則（與 CreateAgentRequest 相同）。多層 repo（含 `/`）不符合。
_AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def candidates(envelope: dict[str, Any]) -> list[tuple[str, str, str]]:
    """從通知裡挑出「看起來是一次版本 push」的 `(repository, tag, digest)`。"""
    found = []
    for event in envelope.get("events") or []:
        target = event.get("target") or {}
        repo, tag, digest = target.get("repository"), target.get("tag"), target.get("digest")
        if event.get("action") != "push" or not (repo and tag and digest):
            continue
        if target.get("mediaType") not in MANIFEST_TYPES:
            continue
        if not _AGENT_NAME.match(repo):
            log.warning("registry push %s:%s ignored: repository must be a single agent name", repo, tag)
            continue
        try:
            CreateAgentVersionRequest(semver=tag, image_ref=f"x/{repo}@{digest}")
        except ValidationError:
            log.warning("registry push %s:%s ignored: tag is not a version number", repo, tag)
            continue
        found.append((repo, tag, digest))
    return found


async def current_digest(repo: str, tag: str) -> str | None:
    """向 registry 問 `<repo>:<tag>` 現在指向哪個 digest。**不在交易內呼叫。**"""
    url = f"{get_settings().internal_registry_url.rstrip('/')}/v2/{repo}/manifests/{tag}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.head(url, headers={"Accept": ", ".join(sorted(MANIFEST_TYPES))})
    except httpx.HTTPError as exc:
        log.warning("registry lookup for %s:%s failed: %s", repo, tag, exc)
        return None
    if response.status_code != 200:
        return None
    return response.headers.get("Docker-Content-Digest")


async def register(repo: str, tag: str, digest: str) -> str | None:
    """確認之後註冊。回傳要派 build 的 agent_version_id（不需要 build 時回 None）。"""
    actual = await current_digest(repo, tag)
    if actual != digest:
        log.warning("registry push %s:%s ignored: registry says %s, event says %s",
                    repo, tag, actual, digest)
        return None

    image_ref = f"{get_settings().internal_registry_host}/{repo}@{digest}"
    body = CreateAgentVersionRequest(semver=tag, image_ref=image_ref)
    async with db.transaction() as conn:
        agent = await _agent_by_name(conn, repo)
        if agent is None:
            try:
                agent = await agents_service.create_agent(conn, CreateAgentRequest(
                    name=repo, description="由 registry push 自動建立"))
            except ZentraError:
                # 兩個通知同時建同一個 Agent：另一個先建好了
                agent = await _agent_by_name(conn, repo)
                if agent is None:
                    raise
            log.info("registry push created agent %s (%s)", repo, agent.id)
        try:
            row, needs_build = await agents_service.create_version(conn, agent, body)
        except ZentraError as exc:
            # 409：同版號已經在驗證中（CD 連推兩次）。等它結束再推。
            log.warning("registry push %s:%s not registered: %s", repo, tag, exc)
            return None
    log.info("registry push %s:%s → agent_version %s (%s)", repo, tag, row.id,
             "validating" if needs_build else "already live")
    return str(row.id) if needs_build else None


async def _agent_by_name(conn, name: str):
    rec = await conn.fetchrow("SELECT id FROM agents WHERE name = $1 AND deleted_at IS NULL", name)
    if rec is None:
        return None
    return await agents_service.load_agent(conn, str(rec["id"]))
