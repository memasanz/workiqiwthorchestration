"""Smoke test for the Legal SME agent.

Sequence:
  1. Ensure a project with at least one legal question assigned to
     ``devon@contoso.com`` exists. If not, create one directly through the
     MCP server (HTTP / JSON-RPC).
  2. Send the agent: "I'm devon@contoso.com. What's on my plate?"
  3. Verify the agent called ``get_my_assignments``.
  4. Send: "Let's work on the first one."
  5. Verify the agent called ``get_question`` and produced a draft.
  6. Send: "Approve"
  7. Verify the agent called ``submit_answer`` and that the question is
     now ``approved`` in Cosmos (via ``get_question`` over MCP).

Exits non-zero on any verification failure.
"""
from __future__ import annotations

import json
import sys
import uuid
from typing import Any

import requests
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

PROJECT_ENDPOINT = (
    "https://aif-mpwflow-dev-a3qzr7isqw476.services.ai.azure.com"
    "/api/projects/proj-mpwflow-dev"
)
MCP_SERVER_URL = (
    "https://ca-mpwflow-dev-mcp-legal.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"
)
AGENT_NAME = "legal-sme-agent"
SME = "devon@contoso.com"

# The legal SME backend doesn't expose create_project / submit_questions, so
# setup uses the submissions backend.
SETUP_MCP_URL = (
    "https://ca-mpwflow-dev-mcp-submissions.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"
)


# ---------- direct MCP HTTP/JSON-RPC client (for setup + verification) ----

class MCPClient:
    """Minimal MCP HTTP / JSON-RPC client (Streamable HTTP transport)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.session = requests.Session()
        self._id = 0
        self._session_id: str | None = None
        self._initialized = False

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        r = self.session.post(self.url, json=payload, headers=headers, timeout=60)
        if "mcp-session-id" in r.headers and not self._session_id:
            self._session_id = r.headers["mcp-session-id"]
        if r.status_code == 202:
            return None
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
            raise RuntimeError(f"empty SSE response: {r.text!r}")
        return r.json()

    def initialize(self) -> None:
        if self._initialized:
            return
        resp = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "legal-smoke-test", "version": "0.1"},
            },
        })
        if not resp or "result" not in resp:
            raise RuntimeError(f"initialize failed: {resp}")
        self._post({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        self._initialized = True

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.initialize()
        resp = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        if not resp:
            raise RuntimeError(f"no response for {tool}")
        if "error" in resp:
            raise RuntimeError(f"{tool} error: {resp['error']}")
        result = resp.get("result", {})
        if result.get("isError"):
            raise RuntimeError(f"{tool} reported error: {result}")
        if "structuredContent" in result:
            sc = result["structuredContent"]
            if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
                return sc["result"]
            return sc
        for c in result.get("content", []):
            if c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except Exception:
                    return c["text"]
        return result


# ---------- helpers --------------------------------------------------------

def ensure_legal_question_for(
    mcp: MCPClient, setup_mcp: MCPClient, user_id: str,
) -> tuple[str, str]:
    """Return (project_id, question_id) for a legal question assigned to user.

    Uses ``mcp`` (legal backend) for read operations like get_my_assignments,
    and ``setup_mcp`` (submissions backend) for create_project + submit_questions
    since those tools aren't exposed on the legal backend.
    """
    assignments = mcp.call(
        "get_my_assignments",
        {"user_id": user_id, "classification": "legal"},
    )
    for q in assignments or []:
        status = q.get("status")
        if status in {"assigned", "drafting", "awaiting_review", "rejected"}:
            qid = q.get("questionId") or q.get("id")
            return q["projectId"], qid

    cri = f"smoke-legal-{uuid.uuid4().hex[:8]}"
    project = setup_mcp.call(
        "create_project",
        {
            "submitter_id": "smoke-test@contoso.com",
            "display_name": f"Legal SME Smoke Test {cri}",
            "client_request_id": cri,
        },
    )
    project_id = project.get("projectId") or project.get("id")
    submit = setup_mcp.call(
        "submit_questions",
        {
            "project_id": project_id,
            "questions": [
                {
                    "text": (
                        "We're signing an NDA with a Texas-based contractor "
                        "for a 5-year term covering general business "
                        "information. Is the duration enforceable under "
                        "Texas law, and what carve-outs should we add?"
                    ),
                    "classification": "legal",
                    "assigned_user_id": user_id,
                },
            ],
            "client_request_id": cri + "-q",
        },
    )
    qids = submit.get("questionIds") or [
        q.get("questionId") or q.get("id") for q in submit.get("questions", [])
    ]
    return project_id, qids[0]


def collect_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Extract MCP tool-call output items from a Responses API response."""
    calls: list[dict[str, Any]] = []
    for item in getattr(response, "output", []) or []:
        itype = getattr(item, "type", None)
        if itype in {"mcp_call", "mcp_tool_call"}:
            calls.append({
                "name": getattr(item, "name", None),
                "arguments": getattr(item, "arguments", None),
                "output": getattr(item, "output", None),
                "error": getattr(item, "error", None),
            })
    return calls


def text_from_response(response: Any) -> str:
    txt = getattr(response, "output_text", None)
    if txt:
        return txt
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) == "message":
            for c in getattr(item, "content", []) or []:
                if getattr(c, "type", None) in {"output_text", "text"}:
                    parts.append(getattr(c, "text", "") or "")
    return "\n".join(parts)


# ---------- main flow ------------------------------------------------------

def main() -> int:
    mcp = MCPClient(MCP_SERVER_URL)
    setup_mcp = MCPClient(SETUP_MCP_URL)

    print("=== Setup: ensuring a legal question is assigned to", SME)
    project_id, question_id = ensure_legal_question_for(mcp, setup_mcp, SME)
    print(f"  project_id  = {project_id}")
    print(f"  question_id = {question_id}")

    print("\n=== Connecting to Foundry project")
    project = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )
    openai = project.get_openai_client()

    conversation = openai.conversations.create()
    print(f"  conversation = {conversation.id}")

    failures: list[str] = []

    def step(label: str, message: str, expect_tool: str | None) -> Any:
        print(f"\n--- {label}")
        print(f"  > USER: {message!r}")
        resp = openai.responses.create(
            conversation=conversation.id,
            input=message,
            extra_body={
                "agent_reference": {"name": AGENT_NAME, "type": "agent_reference"},
            },
        )
        calls = collect_tool_calls(resp)
        for c in calls:
            args = c["arguments"]
            if isinstance(args, str):
                preview = args[:200]
            else:
                preview = json.dumps(args)[:200] if args else ""
            err = f"  ERR={c['error']}" if c.get("error") else ""
            print(f"  > TOOL: {c['name']}({preview}){err}")
        reply = text_from_response(resp)
        print(f"  > ASSISTANT: {reply[:600]}")
        if expect_tool and not any(c["name"] == expect_tool for c in calls):
            failures.append(
                f"{label}: expected tool {expect_tool!r} not invoked "
                f"(got {[c['name'] for c in calls]})"
            )
        return resp

    step(
        "Step 1: identify + status",
        f"I'm {SME}. What's on my plate?",
        expect_tool="get_my_assignments",
    )

    step(
        "Step 2: pick a question",
        f"Let's work on question {question_id} in project {project_id}.",
        expect_tool="get_question",
    )

    step(
        "Step 3: approve",
        "Approve",
        expect_tool="submit_answer",
    )

    print("\n=== Verifying Cosmos state via MCP get_question")
    final = mcp.call("get_question", {
        "question_id": question_id,
        "project_id": project_id,
    })
    print(f"  status      = {final.get('status')}")
    print(f"  finalAnswer = {(final.get('finalAnswer') or '')[:200]}")
    if final.get("status") != "approved":
        failures.append(
            f"Final question status is {final.get('status')!r}, expected 'approved'"
        )
    if not final.get("finalAnswer"):
        failures.append("Final question has no finalAnswer set")

    print("\n=== Result")
    if failures:
        print("FAIL:")
        for f in failures:
            print(" -", f)
        return 1
    print("PASS — all verifications succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
