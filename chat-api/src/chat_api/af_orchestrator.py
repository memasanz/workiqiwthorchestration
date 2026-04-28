"""Microsoft Agent Framework orchestrator (chat-api 0.7.0).

Each participant (``submissions``, ``tax``, ``legal``) is bound to a
**registered** Foundry Hosted Agent (``submissions-agent`` /
``tax-sme-agent`` / ``legal-sme-agent``) via ``agent_framework.foundry.FoundryAgent``,
which calls the Foundry responses-protocol passthrough at
``/agents/{name}/endpoint/protocols/openai/responses``. That endpoint
accepts user-context OBO tokens (audience ``https://ai.azure.com``); the
hosted MCPTool binding lives in the registered agent definition and runs
server-side under the same caller identity.

Per-agent **conversation continuity** across turns is handled via
``AgentSession``. ``FoundrySession.threads[<foundry_agent_name>]`` holds
the ``service_session_id`` returned by the Foundry responses API on the
first turn (Foundry's ``previous_response_id`` chain). Subsequent turns
for the same SME pass that id back in via
``AgentSession(service_session_id=...)`` plus ``store=True`` so the model
sees the full prior conversation context server-side without us having
to replay the transcript.

A previous iteration migrated to ``AzureAIAgentClient`` (v2 Agents API:
``/threads`` + ``/runs``) on the assumption that the responses passthrough
rejected user tokens. That assumption was wrong — it was a transient
Foundry outage. ``AzureAIAgentClient`` requires Classic-Agent ``asst_*``
ids and rejects friendly names like ``legal-sme-agent``, which is why we
reverted to ``FoundryAgent`` here.

Handoff is signalled by the assistant emitting ``[HANDOFF: <name>]`` at
the end of its message; the orchestrator parses it, emits a fresh
``router_decision`` SSE event, and resumes under the new agent. Capped
at ``MAX_AGENT_RUNS_PER_TURN`` to avoid loops.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable

from azure.identity.aio import DefaultAzureCredential

from agent_framework import (
    AgentExecutor,
    AgentSession,
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowEvent,
    handler,
)
from agent_framework.foundry import FoundryAgent

from .config import (
    AGENT_TO_FOUNDRY_NAME,
    AGENT_TO_MCP_PROFILE,
    AUTO_TOOLS,
    DESTRUCTIVE_TOOLS,
    Config,
)
from .sessions import SessionStore
from .sse import event
from .transcript import (
    ApprovalDecision,
    Session,
    TranscriptMessage,
)

log = logging.getLogger(__name__)


HANDOFF_TARGETS: dict[str, tuple[str, ...]] = {
    "submissions": ("tax", "legal"),
    "tax": ("submissions",),
    "legal": ("submissions",),
}

VALID_AGENTS = ("submissions", "tax", "legal")
MAX_AGENT_RUNS_PER_TURN = 6

# Reverse map: foundry agent name -> internal agent_id. Built lazily so a
# missing AGENT_TO_FOUNDRY_NAME entry surfaces at import time.
FOUNDRY_NAME_TO_AGENT: dict[str, str] = {v: k for k, v in AGENT_TO_FOUNDRY_NAME.items()}


# --------------------------------------------------------------------------- #
# Routing entry rule (deliberately small — not an LLM call)                   #
# --------------------------------------------------------------------------- #


def pick_initial_agent(
    caller_email: str,
    assignments: list,
    first_user_message: str | None,
) -> str:
    classes = {getattr(a, "classification", None) for a in (assignments or [])}
    classes.discard(None)
    if classes == {"tax"}:
        return "tax"
    if classes == {"legal"}:
        return "legal"
    return "submissions"


# --------------------------------------------------------------------------- #
# Per-session runtime state                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class _RuntimeCtx:
    session: Session
    cfg: Config
    store: SessionStore
    turn: int
    agent_id: str
    user_token: str | None
    next_agent: str | None = None


@dataclass
class FoundrySession:
    """Per-chat-session AF runtime.

    * ``credential`` — async Azure credential (OBO for the caller in
      production; ``DefaultAzureCredential`` in dev bypass mode).
    * ``agents`` — lazy cache of ``FoundryAgent`` keyed by Foundry agent
      name. Bound to the registered Hosted Agent via ``agent_name=...``.
    * ``threads`` — Foundry agent name → service-managed
      ``service_session_id`` (i.e. the Foundry ``previous_response_id``
      chain root) returned by the responses API on the first turn.
      Reused on subsequent turns so the same SME keeps its full
      conversation history server-side.
    * ``announced_participants`` — guards the one-shot ``participant``
      SSE emission per agent per session.
    * ``pending_*`` / ``approval_resume`` — kept for SSE-contract
      compatibility but unused under FoundryAgent (the registered
      MCPTool runs server-side with ``require_approval='never'``).
    """

    credential: Any
    user_token: str | None = None
    agents: dict[str, FoundryAgent] = field(default_factory=dict)
    threads: dict[str, str] = field(default_factory=dict)
    pending_requests: dict[str, Any] = field(default_factory=dict)
    pending_agent: dict[str, str] = field(default_factory=dict)
    approval_resume: Any | None = None
    announced_participants: set[str] = field(default_factory=set)

    async def aclose(self) -> None:
        for a in list(self.agents.values()):
            close = getattr(a, "close", None) or getattr(a, "aclose", None)
            if close is None:
                continue
            try:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001
                pass
        self.agents.clear()
        closer = self.credential
        close = getattr(closer, "close", None) or getattr(closer, "aclose", None)
        if close is None:
            return
        try:
            res = close()
            if asyncio.iscoroutine(res):
                await res
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Building blocks                                                             #
# --------------------------------------------------------------------------- #


def _build_approval_mode() -> dict[str, list[str]]:
    return {
        "always_require_approval": sorted(DESTRUCTIVE_TOOLS),
        "never_require_approval": sorted(AUTO_TOOLS),
    }


def make_foundry_session(
    cfg: Config,
    user_token: str | None = None,
    *,
    user_credential: Any = None,
) -> FoundrySession:
    """Construct a per-chat-session runtime container.

    Accepts an optional ``user_credential`` (typically an
    ``OnBehalfOfCredential`` minted from the caller's bearer token) that
    is plumbed into every ``FoundryAgent`` so calls to the registered
    Foundry agent + its hosted MCPTool happen as the end user. When the
    caller is unauthenticated (dev bypass) we fall back to the
    platform's ``DefaultAzureCredential`` (i.e. the chat-api UAMI).
    """
    cred = user_credential if user_credential is not None else DefaultAzureCredential()
    return FoundrySession(
        credential=cred,
        user_token=user_token,
    )


def get_or_create_agent(fs: FoundrySession, cfg: Config, agent_id: str) -> FoundryAgent:
    """Return the cached ``FoundryAgent`` for ``agent_id``, creating it
    on first use. Bound to the registered Hosted Agent via
    ``agent_name=<foundry_name>`` and uses the session's async credential
    for the responses-protocol passthrough.
    """
    foundry_name = AGENT_TO_FOUNDRY_NAME[agent_id]
    cached = fs.agents.get(foundry_name)
    if cached is not None:
        return cached
    agent = FoundryAgent(
        project_endpoint=cfg.foundry_project_endpoint,
        agent_name=foundry_name,
        credential=fs.credential,
        allow_preview=True,
    )
    fs.agents[foundry_name] = agent
    return agent


# --------------------------------------------------------------------------- #
# Resolved-version helper. FoundryAgent resolves the Hosted Agent             #
# server-side on each call; we no longer issue a GET /agents/{name}.          #
# --------------------------------------------------------------------------- #


async def get_resolved_version(cfg: Config, agent_id: str) -> str:
    return "latest"


# --------------------------------------------------------------------------- #
# Handoff-hint preface                                                        #
# --------------------------------------------------------------------------- #


def _handoff_preface(agent_id: str) -> str:
    return ""


_KEYWORDS: dict[str, tuple[str, ...]] = {
    "submissions": (
        "new project", "start a project", "start a new project", "create project",
        "create a project", "submit a question", "submit question", "open a project",
        "begin a project", "new submission", "intake", "submit it",
    ),
    "tax": (
        "tax", "irs", "deduction", "vat", "sales tax", "withholding", "1099", "w-2", "w2",
    ),
    "legal": (
        "legal", "contract", "nda", "non-disclosure", "agreement", "lawsuit", "compliance",
        "indemnif", "liabil", "litigation",
    ),
}


def _classify_user_message(text: str) -> str | None:
    """Lightweight keyword pre-router. Returns target agent or None to keep current."""
    if not text:
        return None
    t = text.lower()
    for agent_id, keywords in _KEYWORDS.items():
        for kw in keywords:
            if kw in t:
                return agent_id
    return None


class RouterExecutor(Executor):
    """First-hop in the workflow graph. Reads the raw user message,
    runs ``_classify_user_message`` (cheap keyword-based router),
    falls back to ``initial_agent`` if no keyword matches, then
    forwards the same string to exactly one downstream
    ``AgentExecutor`` via ``ctx.send_message(target_id=...)``.

    No LLM hop. No hidden state (the chosen agent name is reported
    via the ``decision_box`` so the caller can emit SSE).
    """

    def __init__(self, *, initial_agent: str, decision_box: list[str]) -> None:
        super().__init__(id="router")
        self._initial = initial_agent
        # decision_box: shared single-element list so the caller can read
        # back which agent the router picked (mutated synchronously inside
        # the handler, before the workflow returns).
        self._decision_box = decision_box

    @handler
    async def route(self, text: str, ctx: WorkflowContext[str]) -> None:
        keyword = _classify_user_message(text)
        target_agent = self._initial
        if keyword and keyword != self._initial:
            # Don't switch AWAY from submissions just because the user's
            # message contains tax/legal keywords — submissions IS the
            # intake orchestrator and is expected to receive lists of
            # mixed-topic questions, classify them, and create the
            # project. Keyword routing only overrides when (a) we're not
            # currently on submissions, or (b) the user is explicitly
            # asking to start a new project (keyword == "submissions").
            if self._initial != "submissions" or keyword == "submissions":
                target_agent = keyword
        if target_agent not in AGENT_TO_FOUNDRY_NAME:
            target_agent = self._initial
        self._decision_box.append(target_agent)
        target_id = AGENT_TO_FOUNDRY_NAME[target_agent]
        await ctx.send_message(text, target_id=target_id)


def _apply_handoff_preface(agent_id: str, run_input: Any) -> Any:
    preface = _handoff_preface(agent_id)
    if not preface:
        return run_input
    if isinstance(run_input, str):
        return preface + (run_input or "")
    return run_input


_HANDOFF_RE = re.compile(r"\[HANDOFF:\s*([a-z_]+)\s*\]\s*$", re.IGNORECASE)


def _parse_handoff_sentinel(text: str) -> str | None:
    if not text:
        return None
    m = _HANDOFF_RE.search(text)
    if not m:
        return None
    name = m.group(1).strip().lower()
    return name if name in VALID_AGENTS else None


def _strip_handoff_sentinel(text: str) -> str:
    return _HANDOFF_RE.sub("", text).rstrip()


# --------------------------------------------------------------------------- #
# Tool-result surfacing                                                       #
# --------------------------------------------------------------------------- #


def _summarize(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        keys = list(result.keys())[:6]
        return {"keys": keys, "preview": {k: result[k] for k in keys}}
    if isinstance(result, list):
        return {"count": len(result), "first": result[0] if result else None}
    return {"value": str(result)[:300]}


async def _surface_function_results(ctx: _RuntimeCtx, response: Any) -> None:
    """Walk the response messages and emit ``tool_executed`` SSE for any
    function/MCP results the model returned. Best-effort; schema may vary."""
    msgs = getattr(response, "messages", None) or []
    seen_calls: dict[str, dict[str, Any]] = {}
    for msg in msgs:
        for c in getattr(msg, "contents", None) or []:
            ctype = type(c).__name__
            ctype_str = getattr(c, "type", None)
            is_call = ctype == "FunctionCallContent" or ctype_str in (
                "function_call", "mcp_server_tool_call",
            )
            is_result = ctype in (
                "FunctionResultContent", "FunctionCallResultContent",
            ) or ctype_str in ("function_result", "mcp_server_tool_result")
            if is_call:
                call_id = getattr(c, "call_id", None) or getattr(c, "id", None) or ""
                tool = getattr(c, "name", None) or getattr(c, "tool_name", None) or "tool"
                args = getattr(c, "arguments", None) or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:  # noqa: BLE001
                        args = {"_raw": args}
                seen_calls[call_id] = {"tool": tool, "args": args}
            elif is_result:
                call_id = getattr(c, "call_id", None) or getattr(c, "id", None) or ""
                meta = seen_calls.pop(
                    call_id,
                    {"tool": getattr(c, "tool_name", None) or "tool", "args": {}},
                )
                result = getattr(c, "result", None)
                if result is None:
                    result = getattr(c, "output", None)
                exception = getattr(c, "exception", None)
                ok = exception is None
                summary = _summarize(result) if ok else {"value": str(exception)[:300]}
                await ctx.store.publish(
                    ctx.session.session_id,
                    event(
                        "tool_executed",
                        {
                            "turn": ctx.turn,
                            "agent": ctx.agent_id,
                            "tool": meta["tool"],
                            "ok": ok,
                            "approval_id": call_id or None,
                            "result": summary,
                            "result_summary": summary,
                            "error": None if ok else str(exception),
                        },
                    ),
                )


def _extract_text(response: Any) -> str:
    if response is None:
        return ""
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        return text
    parts: list[str] = []
    for m in getattr(response, "messages", None) or []:
        for c in getattr(m, "contents", None) or []:
            t = getattr(c, "text", None)
            if isinstance(t, str):
                parts.append(t)
    return "\n".join(parts)


def _extract_service_session_id(response: Any, session: AgentSession) -> str | None:
    """Best-effort: pull the service-managed session id (Foundry's
    ``previous_response_id`` chain root) off the AgentSession or the
    response after a run with ``store=True``. The agent runtime
    populates ``session.service_session_id`` in-place."""
    sid = getattr(session, "service_session_id", None)
    if sid:
        return sid
    for attr in ("conversation_id", "service_session_id", "response_id"):
        val = getattr(response, attr, None)
        if val:
            return val
    raw = getattr(response, "raw_representation", None)
    if raw is not None:
        for attr in ("conversation_id", "id"):
            val = getattr(raw, attr, None)
            if val:
                return val
    return None


# --------------------------------------------------------------------------- #
# SSE helpers                                                                 #
# --------------------------------------------------------------------------- #


def emit_router_decision(
    store: SessionStore, session_id: str, agent: str
) -> Awaitable[None]:
    return store.publish(
        session_id,
        event("router_decision", {"agents": [agent]}),
    )


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #


async def run_turn(
    session: Session,
    cfg: Config,
    store: SessionStore,
    active_agent: str,
    fs: FoundrySession,
    *,
    initial_input: Any = None,
) -> str:
    """Drive one user turn through an Agent Framework ``WorkflowBuilder``
    graph: ``RouterExecutor`` → one of three ``AgentExecutor``s wrapping
    ``submissions`` / ``tax`` / ``legal`` ``FoundryAgent``s.

    Per-agent service-managed session ids (Foundry's
    ``previous_response_id`` chain) are loaded from ``fs.threads`` into
    each ``AgentSession`` before the run, then written back after.
    Returns the agent that handled this turn (so the caller can update
    ``Session.active_agent``).
    """
    session.turn_counter += 1
    turn = session.turn_counter

    user_text = initial_input if isinstance(initial_input, str) else ""

    # Build per-request workflow with fresh OBO-bound FoundryAgents.
    agent_executors: dict[str, AgentExecutor] = {}
    sessions_by_agent: dict[str, AgentSession] = {}
    for agent_id in VALID_AGENTS:
        foundry_name = AGENT_TO_FOUNDRY_NAME[agent_id]
        fa = get_or_create_agent(fs, cfg, agent_id)
        prior_sid = fs.threads.get(foundry_name)
        ag_sess = AgentSession(service_session_id=prior_sid)
        sessions_by_agent[agent_id] = ag_sess
        agent_executors[agent_id] = AgentExecutor(
            fa, session=ag_sess, id=foundry_name,
        )

    decision_box: list[str] = []
    router = RouterExecutor(initial_agent=active_agent, decision_box=decision_box)

    builder = WorkflowBuilder(start_executor=router)
    for ae in agent_executors.values():
        builder.add_edge(router, ae)
    workflow = builder.build()

    announced_this_turn: set[str] = set()

    async def _emit_participant_if_new(agent_id: str) -> None:
        if agent_id in fs.announced_participants:
            return
        fs.announced_participants.add(agent_id)
        version = await get_resolved_version(cfg, agent_id)
        await store.publish(
            session.session_id,
            event(
                "participant",
                {
                    "agent_id": agent_id,
                    "foundry_name": AGENT_TO_FOUNDRY_NAME[agent_id],
                    "version": version,
                },
            ),
        )

    final_agent = active_agent

    # Per-agent text buffers — fed by streaming AgentResponseUpdate chunks,
    # flushed on executor_completed as a single agent_message (preserves
    # today's UI contract while enabling token-level events for future use).
    text_buffers: dict[str, list[str]] = {}
    last_full_response: dict[str, Any] = {}

    async def _flush_agent(agent_id: str) -> None:
        text = "".join(text_buffers.get(agent_id, [])).strip()
        if not text:
            return
        text_buffers[agent_id] = []
        await store.publish(
            session.session_id,
            event(
                "agent_message",
                {"turn": turn, "agent": agent_id, "text": text},
            ),
        )
        session.transcript.append(
            TranscriptMessage(role="assistant", agent=agent_id, text=text)
        )

    try:
        async for ev in workflow.run(user_text, stream=True):
            etype = getattr(ev, "type", None)
            eid = getattr(ev, "executor_id", None)
            edata = getattr(ev, "data", None)

            if etype == "executor_invoked" and eid in FOUNDRY_NAME_TO_AGENT:
                agent_id = FOUNDRY_NAME_TO_AGENT[eid]
                if agent_id != active_agent and agent_id not in announced_this_turn:
                    await emit_router_decision(store, session.session_id, agent_id)
                announced_this_turn.add(agent_id)
                final_agent = agent_id
                text_buffers[agent_id] = []
                await _emit_participant_if_new(agent_id)
                await store.publish(
                    session.session_id,
                    event("agent_turn_start", {"turn": turn, "agent": agent_id}),
                )

            elif etype == "output" and eid in FOUNDRY_NAME_TO_AGENT and edata is not None:
                agent_id = FOUNDRY_NAME_TO_AGENT[eid]
                # AgentExecutor in streaming mode yields AgentResponseUpdate
                # per token; in non-streaming it yields a full AgentResponse.
                # Both expose .text (and a value attr / messages on Response).
                chunk = _extract_text(edata)
                if chunk:
                    text_buffers.setdefault(agent_id, []).append(chunk)
                    await store.publish(
                        session.session_id,
                        event(
                            "assistant_token",
                            {"turn": turn, "agent": agent_id, "delta": chunk},
                        ),
                    )
                # Hold on to the most recent full response (if any) so we
                # can surface tool calls once the agent finishes.
                if hasattr(edata, "messages"):
                    last_full_response[agent_id] = edata

            elif etype == "executor_completed" and eid in FOUNDRY_NAME_TO_AGENT:
                agent_id = FOUNDRY_NAME_TO_AGENT[eid]
                full = last_full_response.pop(agent_id, None)
                if full is not None:
                    ctx_for_agent = _RuntimeCtx(
                        session=session,
                        cfg=cfg,
                        store=store,
                        turn=turn,
                        agent_id=agent_id,
                        user_token=fs.user_token,
                    )
                    await _surface_function_results(ctx_for_agent, full)
                await _flush_agent(agent_id)

            elif etype == "executor_failed" and eid in FOUNDRY_NAME_TO_AGENT:
                agent_id = FOUNDRY_NAME_TO_AGENT[eid]
                err = getattr(ev, "details", None) or edata
                log.error("agent %s executor failed: %s", agent_id, err)
                await store.publish(
                    session.session_id,
                    event("error", {"message": f"agent {agent_id}: {err}"}),
                )
    except Exception as e:  # noqa: BLE001
        log.exception("workflow run failed")
        await store.publish(
            session.session_id,
            event("error", {"message": f"workflow: {e}"}),
        )

    # Safety net: flush anything still buffered (e.g. if no executor_completed
    # fired for some agent that produced output).
    for agent_id in list(text_buffers.keys()):
        await _flush_agent(agent_id)

    # Persist updated service_session_ids for thread continuity next turn.
    for agent_id, ag_sess in sessions_by_agent.items():
        foundry_name = AGENT_TO_FOUNDRY_NAME[agent_id]
        new_sid = getattr(ag_sess, "service_session_id", None)
        if new_sid and fs.threads.get(foundry_name) != new_sid:
            fs.threads[foundry_name] = new_sid
            log.info(
                "session %s agent %s service_session_id=%s",
                session.session_id, foundry_name, new_sid,
            )

    await store.publish(
        session.session_id,
        event("final", {"turn": turn, "session_id": session.session_id}),
    )
    return final_agent


# Legacy stubs preserved for import compatibility; not used under
# FoundryAgent (the hosted MCPTool runs server-side with
# ``require_approval='never'``).


async def _wait_for_all_approvals(
    fs: FoundrySession,
    store: SessionStore,
    session_id: str,
    ids: list[str],
) -> list[Any]:
    return []


def register_approval_request(
    fs: FoundrySession,
    store: SessionStore,
    session_id: str,
    request_id: str,
) -> Any:
    rt = store.get(session_id)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[ApprovalDecision] = loop.create_future()
    rt.pending_approvals[request_id] = fut
    return fut
