"""Tests for the chat-api 0.3.1 FoundryChatClient + hosted MCP orchestrator.

These exercise routing, the hosted MCP factory, the SSE approval-mapping
contract, the approve/reject branches, and X-User-Token forwarding without
spinning up a real Foundry project.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

# Required env vars must be present at import time of config.
os.environ.setdefault("FOUNDRY_PROJECT_ENDPOINT",
                      "https://fake.services.ai.azure.com/api/projects/proj-x")
os.environ.setdefault("MODEL_DEPLOYMENT_NAME", "gpt-5_3-chat")
os.environ.setdefault("SUBMISSIONS_MCP_URL", "https://mcp-sub/mcp")
os.environ.setdefault("TAX_MCP_URL", "https://mcp-tax/mcp")
os.environ.setdefault("LEGAL_MCP_URL", "https://mcp-legal/mcp")

from chat_api import af_orchestrator as orch  # noqa: E402
from chat_api.config import AUTO_TOOLS, DESTRUCTIVE_TOOLS, load_config  # noqa: E402
from chat_api.sessions import SessionStore  # noqa: E402
from chat_api.transcript import (  # noqa: E402
    ApprovalDecision,
    Assignment,
    Session,
    TranscriptMessage,
)


# --------------------------------------------------------------------------- #
# Routing entry rule                                                          #
# --------------------------------------------------------------------------- #


def test_pick_initial_agent_no_assignments_starts_at_submissions():
    assert orch.pick_initial_agent("alice@x.com", [], None) == "submissions"


def test_pick_initial_agent_tax_only_starts_at_tax():
    asg = [Assignment(project_id="p1", question_id="q1", classification="tax", status="open")]
    assert orch.pick_initial_agent("sme@x.com", asg, None) == "tax"


def test_pick_initial_agent_legal_only_starts_at_legal():
    asg = [Assignment(project_id="p1", question_id="q1", classification="legal", status="open")]
    assert orch.pick_initial_agent("sme@x.com", asg, None) == "legal"


def test_pick_initial_agent_mixed_falls_back_to_submissions():
    asg = [
        Assignment(project_id="p1", question_id="q1", classification="tax", status="open"),
        Assignment(project_id="p1", question_id="q2", classification="legal", status="open"),
    ]
    assert orch.pick_initial_agent("sme@x.com", asg, None) == "submissions"


def test_handoff_sentinel_parses_and_strips():
    text = "OK, let's hand off.\n[HANDOFF: tax]"
    assert orch._parse_handoff_sentinel(text) == "tax"
    assert orch._strip_handoff_sentinel(text) == "OK, let's hand off."


# --------------------------------------------------------------------------- #
# 1. Hosted MCP tool factory accepts headers + approval_mode                  #
# --------------------------------------------------------------------------- #


def test_make_mcp_tool_passes_headers_and_approval_mode():
    cfg = load_config()
    captured: dict[str, Any] = {}

    fake_client = MagicMock()
    fake_client.get_mcp_tool.side_effect = lambda **kw: captured.update(kw) or "fake-tool"

    out = orch.make_mcp_tool(fake_client, "submissions", cfg, user_token="usr-tok-123")
    assert out == "fake-tool"

    assert captured["name"] == "submissions_mcp"
    assert captured["url"] == cfg.submissions_mcp_url
    # Approval mode contains both gate sets.
    am = captured["approval_mode"]
    assert set(am["always_require_approval"]) == DESTRUCTIVE_TOOLS
    assert set(am["never_require_approval"]) == AUTO_TOOLS
    # Headers carry X-User-Token (test #6).
    assert captured["headers"] == {"X-User-Token": "usr-tok-123"}


def test_make_mcp_tool_omits_headers_when_no_token():
    cfg = load_config()
    captured: dict[str, Any] = {}
    fake_client = MagicMock()
    fake_client.get_mcp_tool.side_effect = lambda **kw: captured.update(kw) or "ok"
    orch.make_mcp_tool(fake_client, "tax", cfg, user_token=None)
    assert captured["headers"] is None
    assert captured["url"] == cfg.tax_mcp_url
    assert captured["name"] == "tax_mcp"


# --------------------------------------------------------------------------- #
# 2-5. End-to-end run_turn behaviour with mocked Agent                        #
# --------------------------------------------------------------------------- #


class _FakeContent:
    """Stand-in for a FunctionApprovalRequestContent."""

    def __init__(self, rid: str, name: str, args: dict[str, Any]):
        from agent_framework import Content
        self.id = rid
        self.user_input_request = True
        self.function_call = MagicMock(name=name)
        self.function_call.name = name
        self.function_call.arguments = args
        # to_function_approval_response returns a real Content so the
        # downstream Message(...) constructor in the orchestrator accepts it.
        # We also record the approved flag for assertions.
        self.approved_calls: list[bool] = []

        def _resp(*, approved: bool):
            self.approved_calls.append(approved)
            fc = Content("function_call", call_id=rid, name=name, arguments=args)
            return Content.from_function_approval_response(
                approved=approved, id=rid, function_call=fc,
            )

        self.to_function_approval_response = MagicMock(side_effect=_resp)


class _FakeMessage:
    def __init__(self, contents: list[Any]):
        self.contents = contents


class _FakeResponse:
    def __init__(self, *, text: str = "", contents: list[Any] | None = None):
        self.text = text
        self.messages = [_FakeMessage(contents or [])] if (text or contents) else []

    @property
    def user_input_requests(self) -> list[Any]:
        return [
            c
            for m in self.messages
            for c in m.contents
            if getattr(c, "user_input_request", False)
        ]


class _FakeAgent:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[Any] = []

    async def run(self, input_, session=None):
        self.calls.append(input_)
        if not self._responses:
            return _FakeResponse(text="(done)")
        return self._responses.pop(0)


def _new_session(caller="u@x.com") -> tuple[Session, SessionStore, orch.FoundrySession]:
    store = SessionStore()
    sess = Session(caller_email=caller, caller_oid="oid", caller_name="U")
    store.create(sess)
    fs = orch.FoundrySession(
        chat_client=MagicMock(),
        credential=MagicMock(),
        agent_session=MagicMock(),
        user_token=None,
    )
    return sess, store, fs


@pytest.mark.asyncio
async def test_user_input_request_maps_to_tool_proposed_and_approval_required(monkeypatch):
    """Test #2: a single FunctionApprovalRequest from the model emits the
    paired tool_proposed + approval_required SSE events with matching
    approval_id, tool name and args."""
    cfg = load_config()
    sess, store, fs = _new_session()
    sess.transcript.append(TranscriptMessage(role="user", text="please create a project"))

    appr = _FakeContent("appr_1", "create_project", {"name": "Foo"})
    first = _FakeResponse(contents=[appr])
    final = _FakeResponse(text="created.")
    agent = _FakeAgent([first, final])
    monkeypatch.setattr(orch, "make_agent", lambda *a, **k: agent)

    async def approve_when_ready():
        for _ in range(200):
            for ev in store.get(sess.session_id).history:
                if ev["event"] == "approval_required":
                    rid = json.loads(ev["data"])["approval_id"]
                    if store.resolve_approval(sess.session_id, rid, ApprovalDecision(decision="approve")):
                        return rid
            await asyncio.sleep(0.01)
        raise AssertionError("no approval_required event")

    approver = asyncio.create_task(approve_when_ready())
    await orch.run_turn(sess, cfg, store, "submissions", fs, initial_input="please create a project")
    rid = await approver

    events = [ev["event"] for ev in store.get(sess.session_id).history]
    assert "tool_proposed" in events
    assert "approval_required" in events
    assert events.index("tool_proposed") < events.index("approval_required")

    tp = next(json.loads(ev["data"]) for ev in store.get(sess.session_id).history if ev["event"] == "tool_proposed")
    ar = next(json.loads(ev["data"]) for ev in store.get(sess.session_id).history if ev["event"] == "approval_required")
    assert tp["approval_id"] == ar["approval_id"] == rid
    assert tp["tool"] == ar["tool"] == "create_project"
    assert tp["args"] == ar["args"] == {"name": "Foo"}
    assert tp["agent"] == "submissions"


@pytest.mark.asyncio
async def test_to_function_approval_response_called_with_true_on_approve(monkeypatch):
    """Test #3: approving a request causes
    req.to_function_approval_response(approved=True) to be called and the
    result fed back into agent.run."""
    cfg = load_config()
    sess, store, fs = _new_session()
    sess.transcript.append(TranscriptMessage(role="user", text="x"))

    appr = _FakeContent("appr_x", "submit_answer", {"qid": "q1"})
    agent = _FakeAgent([_FakeResponse(contents=[appr]), _FakeResponse(text="ok.")])
    monkeypatch.setattr(orch, "make_agent", lambda *a, **k: agent)

    async def approve():
        for _ in range(200):
            for ev in store.get(sess.session_id).history:
                if ev["event"] == "approval_required":
                    rid = json.loads(ev["data"])["approval_id"]
                    store.resolve_approval(sess.session_id, rid, ApprovalDecision(decision="approve"))
                    return
            await asyncio.sleep(0.01)
        raise AssertionError("no approval")

    asyncio.create_task(approve())
    await orch.run_turn(sess, cfg, store, "tax", fs, initial_input="x")

    appr.to_function_approval_response.assert_called_once_with(approved=True)
    # Second agent.run call carried the resume message with the approval
    # response content.
    assert len(agent.calls) == 2
    resume_input = agent.calls[1]
    assert isinstance(resume_input, list)
    assert resume_input[0].role == "user"
    assert len(resume_input[0].contents) == 1
    rc = resume_input[0].contents[0]
    assert rc.type == "function_approval_response"
    assert rc.approved is True
    assert rc.id == "appr_x"


@pytest.mark.asyncio
async def test_to_function_approval_response_called_with_false_on_reject(monkeypatch):
    """Test #4: rejecting a request flows approved=False into the resume."""
    cfg = load_config()
    sess, store, fs = _new_session()
    sess.transcript.append(TranscriptMessage(role="user", text="x"))

    appr = _FakeContent("appr_y", "update_project_status", {"status": "closed"})
    agent = _FakeAgent([
        _FakeResponse(contents=[appr]),
        _FakeResponse(text="acknowledged the rejection."),
    ])
    monkeypatch.setattr(orch, "make_agent", lambda *a, **k: agent)

    async def reject():
        for _ in range(200):
            for ev in store.get(sess.session_id).history:
                if ev["event"] == "approval_required":
                    rid = json.loads(ev["data"])["approval_id"]
                    store.resolve_approval(
                        sess.session_id, rid,
                        ApprovalDecision(decision="reject", reason="not now"),
                    )
                    return
            await asyncio.sleep(0.01)
        raise AssertionError("no approval")

    asyncio.create_task(reject())
    await orch.run_turn(sess, cfg, store, "submissions", fs, initial_input="x")

    appr.to_function_approval_response.assert_called_once_with(approved=False)
    events = [ev["event"] for ev in store.get(sess.session_id).history]
    # Final still fires after the model acknowledges the rejection.
    assert events[-1] == "final"
    assert "agent_message" in events


@pytest.mark.asyncio
async def test_handoff_emits_router_decision(monkeypatch):
    """Test #5: a [HANDOFF: tax] sentinel from submissions emits a
    router_decision event whose payload is exactly {agents: [tax]}."""
    cfg = load_config()
    sess, store, fs = _new_session()
    sess.transcript.append(TranscriptMessage(role="user", text="I have a tax question"))

    sub_resp = _FakeResponse(text="Routing to tax. [HANDOFF: tax]")
    tax_resp = _FakeResponse(text="I'll take it.")
    agents_per_id = {"submissions": _FakeAgent([sub_resp]), "tax": _FakeAgent([tax_resp])}

    def _factory(fs_, cfg_, agent_id):
        return agents_per_id[agent_id]

    monkeypatch.setattr(orch, "make_agent", _factory)

    final_agent = await orch.run_turn(sess, cfg, store, "submissions", fs, initial_input="I have a tax question")
    assert final_agent == "tax"

    history = store.get(sess.session_id).history
    rd_events = [json.loads(ev["data"]) for ev in history if ev["event"] == "router_decision"]
    assert {"agents": ["tax"]} in rd_events
    # Final assistant message had the sentinel stripped.
    msgs = [json.loads(ev["data"]) for ev in history if ev["event"] == "agent_message"]
    sub_msg = next(m for m in msgs if m["agent"] == "submissions")
    assert "[HANDOFF" not in sub_msg["text"]


# --------------------------------------------------------------------------- #
# 6. X-User-Token forwarded on outbound MCP calls                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_user_token_forwarded_to_mcp(monkeypatch):
    """Test #6: every chat_api.tools.call_tool POST carries X-User-Token."""
    headers_seen: list[dict[str, str]] = []

    async def fake_post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        import json as _json
        headers_seen.append(dict(headers or {}))
        method = json.get("method") if isinstance(json, dict) else None
        request = httpx.Request("POST", url)
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json", "Mcp-Session-Id": "sid-1"},
                content=b'{"jsonrpc":"2.0","id":"1","result":{"protocolVersion":"2025-06-18"}}',
                request=request,
            )
        if method == "notifications/initialized":
            return httpx.Response(200, content=b'{}', headers={"Content-Type": "application/json"}, request=request)
        body = _json.dumps({
            "jsonrpc": "2.0", "id": "2",
            "result": {"content": [{"type": "text", "text": '{"ok":true}'}]},
        }).encode()
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    from chat_api.tools import call_tool
    out = await call_tool("https://mcp/example/mcp", "get_routing", {"category": "tax"},
                          user_token="abc.def.ghi")
    assert out == {"ok": True}
    for h in headers_seen:
        assert h.get("X-User-Token") == "abc.def.ghi"
