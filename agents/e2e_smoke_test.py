"""End-to-end smoke test for the multi-person workflow.

Drives a complete project lifecycle through the three Foundry agents:

1. submissions-agent  (Agents v1.0 SDK: threads/messages/runs)
2. tax-sme-agent      (Responses API via agent_reference)
3. legal-sme-agent    (Responses API via agent_reference)
4. submissions-agent  (mark project completed)

Then verifies final state directly against the MCP server via JSON-RPC.

Run:
    .\\.agentvenv\\Scripts\\python.exe agents\\e2e_smoke_test.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from azure.ai.agents import AgentsClient
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# Reuse the v1.0 agents SDK pieces for the submissions agent.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from azure.ai.agents.models import (  # noqa: E402
    ListSortOrder,
    RequiredMcpToolCall,
    SubmitToolApprovalAction,
    ToolApproval,
)
from shared.foundry_client import get_project_client  # noqa: E402
from shared.mcp_tool_def import build_mcp_tool  # noqa: E402


def get_agents_client(endpoint: str) -> AgentsClient:
    return AgentsClient(endpoint=endpoint, credential=DefaultAzureCredential())


# ---------- constants -----------------------------------------------------

PROJECT_ENDPOINT = (
    "https://aif-mpwflow-dev-a3qzr7isqw476.services.ai.azure.com"
    "/api/projects/proj-mpwflow-dev"
)
MCP_SERVER_URL = (
    "https://ca-mpwflow-dev-mcp-submissions.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"
)
MCP_TAX_URL = (
    "https://ca-mpwflow-dev-mcp-tax.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"
)
MCP_LEGAL_URL = (
    "https://ca-mpwflow-dev-mcp-legal.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"
)
MCP_SERVER_LABEL = "workflow"

SUBMISSIONS_AGENT_NAME = "submissions-agent"
TAX_AGENT_NAME = "tax-sme-agent"
LEGAL_AGENT_NAME = "legal-sme-agent"

SUBMITTER_ID = "e2e-submitter@contoso.com"

# After the profile-filtered MCP split, the submissions backend already only
# exposes the submissions-side tools, so we no longer need to constrain the
# Foundry-side allowed_tools list — pass [] to allow everything the server
# advertises.
SUBMISSIONS_ALLOWED_TOOLS: list[str] = []

INITIAL_SUBMISSION = (
    f"I'm {SUBMITTER_ID}. I have 4 questions for you. "
    "(1) Can our Delaware C-corp claim the R&D tax credit for AI training "
    "compute spend? "
    "(2) Is our standard mutual NDA enforceable in California against a "
    "former employee? "
    "(3) What are the sales-tax nexus rules for SaaS sold into Texas? "
    "(4) Can we use a poison-pill defense against a hostile takeover under "
    "Delaware law?"
)


def _truncate(s: str, n: int = 300) -> str:
    if s is None:
        return ""
    if len(s) <= n:
        return s
    return s[:n] + "...<truncated>"


# ---------- direct MCP JSON-RPC client (verification only) ----------------

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
                "clientInfo": {"name": "e2e-smoke-test", "version": "0.1"},
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


# ---------- helpers shared by both SDK styles -----------------------------

def _print_tool_call(name: str, args: Any, output: Any = None, error: Any = None) -> None:
    args_s = args if isinstance(args, str) else json.dumps(args, default=str)
    print(f"  > TOOL: {name}({_truncate(args_s, 280)})")
    if output is not None:
        out_s = output if isinstance(output, str) else json.dumps(output, default=str)
        print(f"           -> {_truncate(out_s, 280)}")
    if error:
        print(f"           ERR: {_truncate(str(error), 280)}")


# ---------- submissions agent (v1.0 threads/runs SDK) ---------------------

def run_submissions_turn(
    agents_client: AgentsClient,
    agent_id: str,
    thread_id: str,
    user_message: str,
    mcp_tool: Any,
) -> tuple[list[dict[str, Any]], str]:
    """Run one turn against the submissions agent and return (tool_calls, reply)."""
    agents = agents_client
    agents.messages.create(thread_id=thread_id, role="user", content=user_message)
    run = agents.runs.create(
        thread_id=thread_id,
        agent_id=agent_id,
        tool_resources=mcp_tool.resources,
    )
    deadline = time.time() + 300
    while run.status in ("queued", "in_progress", "requires_action"):
        if time.time() > deadline:
            raise RuntimeError("submissions run timed out")
        time.sleep(2)
        run = agents.runs.get(thread_id=thread_id, run_id=run.id)
        if run.status == "requires_action" and isinstance(
            run.required_action, SubmitToolApprovalAction
        ):
            approvals = []
            for tc in run.required_action.submit_tool_approval.tool_calls or []:
                if isinstance(tc, RequiredMcpToolCall):
                    approvals.append(
                        ToolApproval(
                            tool_call_id=tc.id,
                            approve=True,
                            headers=mcp_tool.headers,
                        )
                    )
            if approvals:
                agents.runs.submit_tool_outputs(
                    thread_id=thread_id, run_id=run.id, tool_approvals=approvals
                )

    if run.status != "completed":
        print(f"  RUN STATUS: {run.status}; last_error={getattr(run, 'last_error', None)}")

    tool_calls: list[dict[str, Any]] = []
    steps = list(agents.run_steps.list(thread_id=thread_id, run_id=run.id))
    for step in steps:
        details = step.get("step_details", {}) or {}
        for call in details.get("tool_calls", []) or []:
            ctype = call.get("type")
            if ctype == "mcp":
                name = call.get("name") or call.get("mcp", {}).get("name") or "?"
                args = call.get("arguments") or call.get("mcp", {}).get("arguments")
                output = call.get("output") or call.get("mcp", {}).get("output")
                tool_calls.append({"name": name, "arguments": args, "output": output})
                _print_tool_call(name, args, output)
            else:
                _print_tool_call(f"({ctype})", call)

    # Find latest assistant text after this run.
    reply = ""
    for m in agents.messages.list(thread_id=thread_id, order=ListSortOrder.ASCENDING):
        if m.role == "assistant" and m.text_messages:
            reply = m.text_messages[-1].text.value
    print(f"  > ASSISTANT: {_truncate(reply, 600)}")
    return tool_calls, reply


# ---------- tax/legal agents (Responses API) ------------------------------

def collect_response_tool_calls(response: Any) -> list[dict[str, Any]]:
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


def run_responses_turn(
    openai_client: Any,
    conversation_id: str,
    agent_name: str,
    user_message: str,
) -> tuple[list[dict[str, Any]], str]:
    print(f"  > USER: {_truncate(user_message, 220)!r}")
    last_exc: Exception | None = None
    resp = None
    for attempt in range(6):
        try:
            resp = openai_client.responses.create(
                conversation=conversation_id,
                input=user_message,
                extra_body={
                    "agent_reference": {
                        "name": agent_name, "type": "agent_reference",
                    },
                },
            )
            break
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            transient = (
                "429" in msg or "rate_limit" in msg.lower()
                or "503" in msg or "504" in msg or "timeout" in msg.lower()
            )
            if not transient or attempt == 5:
                raise
            wait = min(60, 5 * (2 ** attempt))
            print(f"    (transient error, retry {attempt+1}/5 in {wait}s: {msg[:120]})")
            time.sleep(wait)
            last_exc = e
    if resp is None:
        raise last_exc or RuntimeError("responses.create failed without exception")
    calls = collect_response_tool_calls(resp)
    for c in calls:
        _print_tool_call(c["name"], c["arguments"], c.get("output"), c.get("error"))
    reply = text_from_response(resp)
    print(f"  > ASSISTANT: {_truncate(reply, 600)}")
    return calls, reply


# ---------- result extraction ---------------------------------------------

def _maybe_parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def extract_project_and_questions_from_calls(
    calls: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Pull project_id + per-question metadata out of submissions tool-call outputs.

    Returns (project_id, questions) where questions is the list passed to
    submit_questions enriched with the resulting questionId.
    """
    project_id: str | None = None
    submitted_questions: list[dict[str, Any]] = []
    question_ids: list[str] = []

    for c in calls:
        name = c.get("name")
        args = _maybe_parse_json(c.get("arguments"))
        output = _maybe_parse_json(c.get("output"))
        if name == "create_project" and isinstance(output, dict):
            project_id = (
                output.get("projectId")
                or output.get("id")
                or project_id
            )
        if name == "submit_questions":
            if isinstance(args, dict):
                qs = args.get("questions") or []
                if isinstance(qs, list):
                    submitted_questions = qs
                if not project_id and args.get("project_id"):
                    project_id = args["project_id"]
            if isinstance(output, dict):
                qids = output.get("questionIds") or []
                if isinstance(qids, list):
                    question_ids = qids

    # zip ids back onto the submitted questions
    enriched: list[dict[str, Any]] = []
    for i, q in enumerate(submitted_questions):
        item = dict(q)
        if i < len(question_ids):
            item["questionId"] = question_ids[i]
        enriched.append(item)
    return project_id, enriched


# ---------- main flow -----------------------------------------------------

def main() -> int:
    failures: list[str] = []
    mcp = MCPClient(MCP_SERVER_URL)
    # get_question lives only on the SME backends (split per profile).
    mcp_tax = MCPClient(MCP_TAX_URL)
    mcp_legal = MCPClient(MCP_LEGAL_URL)

    # ============================================================
    # PHASE 1: submissions
    # ============================================================
    print("=" * 70)
    print("[AGENT submissions-agent] PHASE 1: classify + create + submit")
    print("=" * 70)

    project_client = get_project_client(PROJECT_ENDPOINT)
    openai = project_client.get_openai_client()

    project_id: str | None = None
    enriched_questions: list[dict[str, Any]] = []
    submissions_conversation_id: str | None = None

    # PHASE 1: submit via Responses API (submissions-agent is a versioned
    # agent now; the legacy AgentsClient threads/runs path no longer applies).
    submissions_conversation = openai.conversations.create()
    submissions_conversation_id = submissions_conversation.id
    print(f"Conversation: {submissions_conversation.id}")

    print("\n--- Turn 1: submit 4 questions")
    sub_calls, sub_reply = run_responses_turn(
        openai, submissions_conversation.id, SUBMISSIONS_AGENT_NAME,
        INITIAL_SUBMISSION,
    )

    names = [c["name"] for c in sub_calls]
    for required in ("create_project", "submit_questions"):
        if required not in names:
            failures.append(
                f"submissions phase: required tool {required!r} not invoked "
                f"(got {names})"
            )

    project_id, enriched_questions = extract_project_and_questions_from_calls(
        sub_calls
    )
    print(f"\nExtracted project_id     = {project_id}")
    print(f"Extracted #questions     = {len(enriched_questions)}")
    for q in enriched_questions:
        print(
            f"  - {q.get('questionId')} [{q.get('classification')}] "
            f"-> {q.get('assigned_user_id')}: "
            f"{_truncate(q.get('text', ''), 90)}"
        )

    # Backfill via MCP get_project if anything is missing.
    if project_id and (
        not enriched_questions
        or any(not q.get("questionId") for q in enriched_questions)
        or any(not q.get("assigned_user_id") for q in enriched_questions)
    ):
        print("\n  (Backfilling question metadata via MCP get_project)")
        proj = mcp.call("get_project", {"project_id": project_id})
        qs = proj.get("questions") or []
        enriched_questions = [
            {
                "questionId": q.get("questionId") or q.get("id"),
                "classification": q.get("classification"),
                "assigned_user_id": q.get("assignedUserId")
                or q.get("assigned_user_id"),
                "text": q.get("text"),
            }
            for q in qs
        ]
        for q in enriched_questions:
            print(
                f"  - {q['questionId']} [{q['classification']}] "
                f"-> {q['assigned_user_id']}"
            )

    if not project_id:
        failures.append("submissions phase: no project_id extracted")
    if len(enriched_questions) != 4:
        failures.append(
            f"submissions phase: expected 4 questions, got "
            f"{len(enriched_questions)}"
        )

    if failures:
        return _summary(failures, project_id, [])

    tax_qs = [q for q in enriched_questions if q.get("classification") == "tax"]
    legal_qs = [q for q in enriched_questions if q.get("classification") == "legal"]
    if len(tax_qs) != 2:
        failures.append(
            f"classification: expected 2 tax questions, got {len(tax_qs)}"
        )
    if len(legal_qs) != 2:
        failures.append(
            f"classification: expected 2 legal questions, got {len(legal_qs)}"
        )
    if failures:
        return _summary(failures, project_id, enriched_questions)

    tax_user = tax_qs[0]["assigned_user_id"]
    legal_user = legal_qs[0]["assigned_user_id"]
    if any(q["assigned_user_id"] != tax_user for q in tax_qs):
        failures.append("tax questions assigned to multiple users (expected one)")
    if any(q["assigned_user_id"] != legal_user for q in legal_qs):
        failures.append("legal questions assigned to multiple users (expected one)")
    print(
        f"\nTax user:   {tax_user}  "
        f"(questions: {[q['questionId'] for q in tax_qs]})"
    )
    print(
        f"Legal user: {legal_user}  "
        f"(questions: {[q['questionId'] for q in legal_qs]})"
    )

    # ============================================================
    # PHASE 2 + 3: SME agents (tax then legal) via Responses API
    # ============================================================

    def drive_sme_phase(
        agent_name: str,
        sme: str,
        questions: list[dict[str, Any]],
        label: str,
    ) -> None:
        print("\n" + "=" * 70)
        print(f"[AGENT {agent_name}] PHASE: {label} ({sme})")
        print("=" * 70)
        conversation = openai.conversations.create()
        print(f"Conversation: {conversation.id}")

        print("\n--- Turn: status query")
        calls, _ = run_responses_turn(
            openai, conversation.id, agent_name,
            f"I'm {sme}. What's on my plate?",
        )
        if not any(c["name"] == "get_my_assignments" for c in calls):
            failures.append(
                f"{label}: expected get_my_assignments not invoked "
                f"(got {[c['name'] for c in calls]})"
            )

        for idx, q in enumerate(questions, 1):
            qid = q["questionId"]
            pid = project_id
            print(f"\n--- Turn: draft question {idx}/{len(questions)} ({qid})")
            calls, _ = run_responses_turn(
                openai, conversation.id, agent_name,
                f"Let's work on question {qid} in project {pid}.",
            )
            tool_names = [c["name"] for c in calls]
            if "get_question" not in tool_names:
                failures.append(
                    f"{label} {qid}: expected get_question, got {tool_names}"
                )

            print(f"\n--- Turn: approve question {idx}/{len(questions)} ({qid})")
            calls, _ = run_responses_turn(
                openai, conversation.id, agent_name,
                "Approve",
            )
            tool_names = [c["name"] for c in calls]
            if "submit_answer" not in tool_names:
                failures.append(
                    f"{label} {qid}: expected submit_answer, got {tool_names}"
                )

    drive_sme_phase(TAX_AGENT_NAME, tax_user, tax_qs, "tax")
    drive_sme_phase(LEGAL_AGENT_NAME, legal_user, legal_qs, "legal")

    # ============================================================
    # PHASE 4: completion via submissions agent (Responses API,
    # same conversation as Phase 1)
    # ============================================================
    print("\n" + "=" * 70)
    print(f"[AGENT {SUBMISSIONS_AGENT_NAME}] PHASE 4: complete project")
    print("=" * 70)

    print("\n--- Turn: mark project completed")
    calls, _ = run_responses_turn(
        openai, submissions_conversation_id, SUBMISSIONS_AGENT_NAME,
        f"I'm {SUBMITTER_ID}. Mark project {project_id} as completed.",
    )
    if not any(c["name"] == "update_project_status" for c in calls):
        failures.append(
            f"completion phase: expected update_project_status, "
            f"got {[c['name'] for c in calls]}"
        )

    # ============================================================
    # PHASE 5: final verification via direct MCP
    # ============================================================
    print("\n" + "=" * 70)
    print("PHASE 5: verify final state via MCP")
    print("=" * 70)

    proj = mcp.call("get_project", {"project_id": project_id})
    print(f"Project status: {proj.get('status')}")
    if proj.get("status") != "completed":
        failures.append(
            f"project status is {proj.get('status')!r}, expected 'completed'"
        )

    final_q_states: list[dict[str, Any]] = []
    for q in enriched_questions:
        qid = q["questionId"]
        cls = q.get("classification")
        backend = mcp_tax if cls == "tax" else mcp_legal
        full = backend.call("get_question", {
            "question_id": qid, "project_id": project_id,
        })
        status = full.get("status")
        final_answer = full.get("finalAnswer") or ""
        history = full.get("history") or []
        print(
            f"  {qid} [{q.get('classification')}]: status={status} "
            f"finalAnswer={'yes' if final_answer else 'NO'} "
            f"history_len={len(history)}"
        )
        final_q_states.append({
            "questionId": qid,
            "classification": q.get("classification"),
            "status": status,
            "finalAnswer": final_answer,
            "history": history,
        })
        if status != "approved":
            failures.append(f"question {qid}: status={status!r}, expected 'approved'")
        if not final_answer:
            failures.append(f"question {qid}: finalAnswer is empty")
        if not history:
            failures.append(f"question {qid}: history is empty")

    return _summary(failures, project_id, final_q_states)


def _summary(
    failures: list[str],
    project_id: str | None,
    questions: list[dict[str, Any]],
) -> int:
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"project_id: {project_id}")
    for q in questions:
        if "status" in q:
            print(
                f"  - {q.get('questionId')} [{q.get('classification')}] "
                f"status={q.get('status')} "
                f"finalAnswer={'set' if q.get('finalAnswer') else 'EMPTY'}"
            )
        else:
            print(f"  - {q.get('questionId')} [{q.get('classification')}]")
    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: all phases completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
