"""平台 MCP 接口的 key 管理（Console 的 `/console/mcp` 用）。

明文只在 POST 的回應出現一次；DB 存的是 SHA-256，沒有任何端點讀得回來。
"""

from fastapi import APIRouter, Request

from apps.api import db
from apps.api.schemas import (
    CreateMcpKeyRequest,
    IssuedMcpKeyResponse,
    McpKeyListResponse,
    McpKeyResponse,
    SetMcpKeyAgentsRequest,
)
from apps.api.services import mcp_keys

router = APIRouter(prefix="/v1/mcp-keys", tags=["mcp"])


def _view(row, agent_ids: list[str]) -> McpKeyResponse:
    return McpKeyResponse(
        key_id=str(row.id), name=row.name, key_prefix=row.key_prefix, agent_ids=agent_ids,
        created_at=row.created_at, last_used_at=row.last_used_at, revoked_at=row.revoked_at,
    )


@router.post("", status_code=201, response_model=IssuedMcpKeyResponse)
async def issue_mcp_key(body: CreateMcpKeyRequest) -> IssuedMcpKeyResponse:
    """發一把 MCP key 並勾選 Agent。**`key` 只在這次回應出現，之後讀不回來。**"""
    async with db.transaction() as conn:
        issued = await mcp_keys.issue(conn, body.name, body.agent_ids)
        selected = (await mcp_keys.agent_ids_by_key(conn, [issued.row.id]))[issued.row.id]
    return IssuedMcpKeyResponse(**_view(issued.row, selected).model_dump(), key=issued.plaintext)


@router.get("", response_model=McpKeyListResponse)
async def list_mcp_keys(request: Request) -> McpKeyListResponse:
    """列出所有 key，含已撤銷的，永不含 key 本身。"""
    async with db.connection() as conn:
        rows = await mcp_keys.list_keys(conn)
        selected = await mcp_keys.agent_ids_by_key(conn, [row.id for row in rows])
    return McpKeyListResponse(
        endpoint=str(request.base_url).rstrip("/") + "/mcp",
        items=[_view(row, selected[row.id]) for row in rows],
    )


@router.put("/{key_id}/agents", response_model=McpKeyResponse)
async def set_mcp_key_agents(key_id: str, body: SetMcpKeyAgentsRequest) -> McpKeyResponse:
    """整份取代這把 key 勾選的 Agent，下一次 `tools/list` 就生效。"""
    async with db.transaction() as conn:
        row = await mcp_keys.load_key(conn, key_id)
        await mcp_keys.set_agents(conn, row, body.agent_ids)
        selected = (await mcp_keys.agent_ids_by_key(conn, [row.id]))[row.id]
    return _view(row, selected)


@router.delete("/{key_id}", response_model=McpKeyResponse)
async def revoke_mcp_key(key_id: str) -> McpKeyResponse:
    """軟撤銷：回 200 帶 `revoked_at`，立即失效，重複撤銷冪等。"""
    async with db.transaction() as conn:
        row = await mcp_keys.load_key(conn, key_id)
        row = await mcp_keys.revoke(conn, row)
        selected = (await mcp_keys.agent_ids_by_key(conn, [row.id]))[row.id]
    return _view(row, selected)
