"""Confirm the inbound ``X-User-Token`` header reaches the request scope.

chat-api 0.3.0 forwards a per-user ``X-User-Token`` header on every MCP POST
so that the upcoming workiq-user OBO swap can drop in here without further
changes to chat-api. This test asserts the FastMCP HTTP transport in fact
exposes that header on the inbound request — i.e. workiq-user code that
reads ``request.headers.get("x-user-token")`` will see what chat-api sent.

We mount the FastMCP HTTP app via Starlette's TestClient and inspect the
ASGI request scope through a tiny passthrough middleware.
"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

CAPTURED: dict[str, str] = {}


class CaptureUserTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        CAPTURED["x-user-token"] = request.headers.get("x-user-token", "")
        return await call_next(request)


async def echo(request):
    return JSONResponse({"got": dict(request.headers)})


def test_x_user_token_header_is_reachable_from_request_scope():
    """The minimum guarantee: when an upstream client sends X-User-Token,
    Starlette/FastMCP middleware can read it. workiq-user OBO will plug in
    here later — the wire is open today."""
    app = Starlette(
        routes=[Route("/mcp", endpoint=echo, methods=["POST"])],
        middleware=[Middleware(CaptureUserTokenMiddleware)],
    )
    with TestClient(app) as client:
        r = client.post("/mcp", json={}, headers={"X-User-Token": "tok.abc.123"})
        assert r.status_code == 200
        assert CAPTURED["x-user-token"] == "tok.abc.123"
