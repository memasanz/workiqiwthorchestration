"""Smoke test for the Submissions agent.

Sends a sample 3-question submission via the Foundry Responses API and
verifies the agent called both ``create_project`` and ``submit_questions``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure.ai.projects import AIProjectClient  # noqa: E402
from azure.identity import DefaultAzureCredential  # noqa: E402

from create_agent import AGENT_NAME, PROJECT_ENDPOINT  # noqa: E402

SAMPLE_USER_MESSAGE = (
    "I'm user1@contoso.com. I have 3 questions. "
    "(1) Can our subsidiary deduct R&D expenses incurred in Ireland? "
    "(2) Is our standard mutual NDA enforceable in Texas state court? "
    "(3) What's the depreciation schedule for the new server racks we bought in March?"
)


def collect_tool_calls(response: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) in {"mcp_call", "mcp_tool_call"}:
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


def _truncate(s: str, n: int = 300) -> str:
    return s if len(s) <= n else s[:n] + "...<truncated>"


def main() -> int:
    project = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )
    openai = project.get_openai_client()
    conversation = openai.conversations.create()
    print(f"Conversation: {conversation.id}")
    print(f"Agent:        {AGENT_NAME}")
    print(f"\n> USER: {SAMPLE_USER_MESSAGE!r}\n")

    resp = openai.responses.create(
        conversation=conversation.id,
        input=SAMPLE_USER_MESSAGE,
        extra_body={
            "agent_reference": {"name": AGENT_NAME, "type": "agent_reference"},
        },
    )

    calls = collect_tool_calls(resp)
    tool_calls_seen: list[str] = []
    print("=== Tool calls ===")
    for c in calls:
        name = c["name"]
        tool_calls_seen.append(name)
        args = c["arguments"]
        preview = args if isinstance(args, str) else json.dumps(args, default=str)
        err = f"  ERR={c['error']}" if c.get("error") else ""
        print(f"- {name}({_truncate(preview)}){err}")
        if c.get("output") is not None:
            print(f"   -> {_truncate(json.dumps(c['output'], default=str))}")

    reply = text_from_response(resp)
    print("\n=== Final assistant reply ===")
    print(reply or "<no reply>")

    print("\n=== Validation ===")
    print(f"Tool calls seen: {tool_calls_seen}")
    required = {"create_project", "submit_questions"}
    missing = required - set(tool_calls_seen)
    if missing:
        print(f"FAIL: missing required tool calls: {sorted(missing)}")
        return 1
    print("PASS: create_project and submit_questions both called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
