"""Thin async MCP JSON-RPC over HTTP client.

The deployed MCP backends are FastMCP HTTP transports — every call is a
single POST that returns either application/json or text/event-stream.
We follow the same shape as `mcp-server/smoke_test.py`.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from .config import AGENT_TO_MCP_PROFILE


class McpError(RuntimeError):
    pass


def _parse_response(body: str, content_type: str) -> dict[str, Any]:
    if "text/event-stream" in content_type:
        result_obj: dict[str, Any] = {}
        for line in body.splitlines():
            if line.startswith("data: "):
                try:
                    result_obj = json.loads(line[6:])
                except json.JSONDecodeError:
                    pass
        return result_obj
    return json.loads(body)


async def _rpc(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    params: dict[str, Any] | None = None,
    session_id: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], str | None]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
    if params is not None:
        payload["params"] = params
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if extra_headers:
        for k, v in extra_headers.items():
            if v is not None:
                headers[k] = v
    resp = await client.post(url, json=payload, headers=headers, timeout=60.0)
    resp.raise_for_status()
    sid = resp.headers.get("Mcp-Session-Id") or session_id
    return _parse_response(resp.text, resp.headers.get("Content-Type", "")), sid


async def _initialize(
    client: httpx.AsyncClient, url: str, extra_headers: dict[str, str] | None = None
) -> str:
    init, sid = await _rpc(
        client,
        url,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "chat-api", "version": "0.3.1"},
        },
        extra_headers=extra_headers,
    )
    if "error" in init:
        raise McpError(f"initialize failed: {init['error']}")
    if not sid:
        raise McpError("MCP server did not return Mcp-Session-Id")
    try:
        await _rpc(
            client, url, "notifications/initialized", {},
            session_id=sid, extra_headers=extra_headers,
        )
    except Exception:  # noqa: BLE001
        pass
    return sid


def _unwrap_tool_result(result: dict[str, Any]) -> Any:
    content = result.get("content") or []
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return content[0]["text"]
    return result


async def call_tool(
    mcp_url: str,
    tool: str,
    args: dict[str, Any],
    user_token: str | None = None,
) -> Any:
    """Initialize a session, call one tool, return the parsed result.

    If ``user_token`` is provided we forward it to the MCP backend as
    ``X-User-Token``. This is the on-ramp for the OBO swap (see workiq-user
    work item) — the chat-api itself does not perform the swap; it just
    propagates whatever the inbound request carried.
    """
    extra = {"X-User-Token": user_token} if user_token else None
    async with httpx.AsyncClient() as client:
        sid = await _initialize(client, mcp_url, extra_headers=extra)
        resp, _ = await _rpc(
            client,
            mcp_url,
            "tools/call",
            {"name": tool, "arguments": args},
            session_id=sid,
            extra_headers=extra,
        )
        if "error" in resp:
            raise McpError(f"{tool} error: {resp['error']}")
        return _unwrap_tool_result(resp.get("result", {}))


async def execute_mcp_tool(
    profile: str,
    tool: str,
    args: dict[str, Any],
    mcp_urls: dict[str, str],
    user_token: str | None = None,
) -> Any:
    if profile not in mcp_urls:
        raise McpError(f"Unknown MCP profile: {profile}")
    return await call_tool(mcp_urls[profile], tool, args, user_token=user_token)


def profile_for_agent(agent_id: str) -> str:
    return AGENT_TO_MCP_PROFILE.get(agent_id, agent_id)
