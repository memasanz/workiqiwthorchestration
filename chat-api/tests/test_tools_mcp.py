"""MCP JSON-RPC client shape tests (mocked transport)."""
import json
from unittest.mock import patch

import httpx
import pytest

from chat_api.tools import call_tool


@pytest.mark.asyncio
async def test_call_tool_does_initialize_then_call(monkeypatch):
    posts: list[dict] = []

    async def fake_post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        import json as _json
        posts.append({"url": url, "json": json, "headers": headers})
        payload_in = json
        method = payload_in.get("method") if isinstance(payload_in, dict) else None
        if method == "initialize":
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json", "Mcp-Session-Id": "sess-xyz"},
                content=b'{"jsonrpc":"2.0","id":"1","result":{"protocolVersion":"2025-06-18"}}',
                request=request,
            )
        if method == "notifications/initialized":
            request = httpx.Request("POST", url)
            return httpx.Response(200, content=b'{}', headers={"Content-Type": "application/json"}, request=request)
        if method == "tools/call":
            request = httpx.Request("POST", url)
            body = _json.dumps({
                "jsonrpc": "2.0", "id": "2",
                "result": {"content": [{"type": "text", "text": '{"projectId": "proj_abc"}'}]},
            }).encode()
            return httpx.Response(200, content=body, headers={"Content-Type": "application/json"}, request=request)
        request = httpx.Request("POST", url)
        return httpx.Response(500, content=b'{}', request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = await call_tool("https://mcp/example/mcp", "create_project", {"submitter_id": "x@y.com"})
    assert out == {"projectId": "proj_abc"}
    methods = [p["json"]["method"] for p in posts]
    assert methods[0] == "initialize"
    assert methods[-1] == "tools/call"
    # Mcp-Session-Id is propagated on the tools/call POST.
    assert posts[-1]["headers"]["Mcp-Session-Id"] == "sess-xyz"
    # tools/call body shape
    tc_body = posts[-1]["json"]
    assert tc_body["params"]["name"] == "create_project"
    assert tc_body["params"]["arguments"] == {"submitter_id": "x@y.com"}
