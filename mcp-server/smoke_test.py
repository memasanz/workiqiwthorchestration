"""One-shot script: seed routing docs and smoke test the live MCP server.

Uses DefaultAzureCredential (your `az login`) for Cosmos seeding, and HTTP
calls for the MCP smoke test.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
import uuid
from typing import Any

from azure.cosmos.aio import CosmosClient
from azure.cosmos import exceptions as cex
from azure.identity.aio import DefaultAzureCredential

COSMOS_ENDPOINT = "https://cosmos-mpwflow-dev-a3qzr7isqw476.documents.azure.com:443/"
DB = "workflow"
MCP_BASE = "https://ca-mpwflow-dev-mcp.icyground-4e2c6fde.eastus2.azurecontainerapps.io"
MCP_URL = MCP_BASE + "/mcp"

ROUTING_DOCS = [
    {"id": "tax", "category": "tax",
     "userIds": ["maya@contoso.com", "alex@contoso.com"], "roundRobinIndex": 0},
    {"id": "legal", "category": "legal",
     "userIds": ["devon@contoso.com", "rae@contoso.com"], "roundRobinIndex": 0},
]


async def seed_routing() -> None:
    cred = DefaultAzureCredential()
    client = CosmosClient(COSMOS_ENDPOINT, credential=cred)
    try:
        db = client.get_database_client(DB)
        routing = db.get_container_client("routing")
        for doc in ROUTING_DOCS:
            try:
                await routing.upsert_item(doc)
                print(f"  seeded routing/{doc['id']}: {doc['userIds']}")
            except cex.CosmosHttpResponseError as e:
                print(f"  ERROR seeding {doc['id']}: {e.message}")
    finally:
        await client.close()
        await cred.close()


def mcp_call(method: str, params: dict[str, Any] | None = None,
             session_id: str | None = None) -> tuple[dict, str | None]:
    """Make a single MCP JSON-RPC over HTTP call. Returns (result, session_id)."""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        sid = resp.headers.get("Mcp-Session-Id") or session_id
        ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        # parse SSE: collect 'data:' lines
        result_obj: dict[str, Any] = {}
        for line in body.splitlines():
            if line.startswith("data: "):
                try:
                    result_obj = json.loads(line[6:])
                except json.JSONDecodeError:
                    pass
        return result_obj, sid
    return json.loads(body), sid


def call_tool(name: str, args: dict[str, Any], session_id: str) -> Any:
    resp, _ = mcp_call(
        "tools/call",
        {"name": name, "arguments": args},
        session_id=session_id,
    )
    if "error" in resp:
        raise RuntimeError(f"{name} error: {resp['error']}")
    result = resp.get("result", {})
    # FastMCP returns content[].text JSON-encoded
    content = result.get("content") or []
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return content[0]["text"]
    return result


def smoke_test() -> int:
    print("\n--- MCP smoke test ---")
    init, sid = mcp_call("initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "smoke-test", "version": "0.1.0"},
    })
    if "error" in init:
        print("initialize error:", init["error"])
        return 1
    print(f"  initialized; session={sid}")

    # MCP requires a notifications/initialized after initialize
    try:
        mcp_call("notifications/initialized", {}, session_id=sid)
    except Exception:
        pass  # notifications may not return a body

    routing = call_tool("get_routing", {"category": "tax"}, sid)
    print(f"  get_routing(tax) -> {routing}")

    proj = call_tool("create_project", {
        "submitter_id": "smoketest@contoso.com",
        "display_name": f"SMOKE-{uuid.uuid4().hex[:6]}",
        "client_request_id": f"smoke-{uuid.uuid4().hex[:8]}",
    }, sid)
    print(f"  create_project -> {proj}")
    pid = proj["projectId"]

    qres = call_tool("submit_questions", {
        "project_id": pid,
        "questions": [
            {"text": "Smoke: deduct R&D?", "classification": "tax",
             "assigned_user_id": routing["next_user_id"]},
            {"text": "Smoke: NDA enforceable?", "classification": "legal",
             "assigned_user_id": "devon@contoso.com"},
        ],
    }, sid)
    print(f"  submit_questions -> {qres}")
    qid = qres["questionIds"][0]

    ans = call_tool("submit_answer", {
        "question_id": qid, "project_id": pid,
        "final_answer": "Yes, see IRC 174 — smoke OK.",
        "by": "smoketest@contoso.com",
    }, sid)
    print(f"  submit_answer -> {ans}")

    q = call_tool("get_question", {"question_id": qid, "project_id": pid}, sid)
    print(f"  get_question.status={q['status']} finalAnswer={q['finalAnswer']!r}")

    proj_full = call_tool("get_project", {"project_id": pid}, sid)
    print(f"  get_project.questions={len(proj_full['questions'])} status={proj_full['status']}")

    if q["status"] != "approved":
        print("  FAIL: question not approved")
        return 2
    print("  SMOKE TEST PASSED")
    return 0


async def main() -> int:
    print("--- Seeding routing in Cosmos ---")
    await seed_routing()
    return smoke_test()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
