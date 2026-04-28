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

from agent_framework import AgentSession
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
    """Drive one user turn through the active agent via the responses
    passthrough. Per agent we maintain a single service-managed session
    id (``service_session_id``) in ``fs.threads`` so the SME keeps its
    full history across turns.

    Returns the final active agent at end-of-turn.
    """
    session.turn_counter += 1
    turn = session.turn_counter

    current = active_agent
    runs = 0
    next_input: Any = initial_input

    keyword_target = _classify_user_message(initial_input if isinstance(initial_input, str) else "")
    if keyword_target and keyword_target != current:
        log.info("pre-router: switching from %s to %s based on user message", current, keyword_target)
        current = keyword_target
        await emit_router_decision(store, session.session_id, current)

    while runs < MAX_AGENT_RUNS_PER_TURN:
        runs += 1
        ctx = _RuntimeCtx(
            session=session,
            cfg=cfg,
            store=store,
            turn=turn,
            agent_id=current,
            user_token=fs.user_token,
        )

        agent = get_or_create_agent(fs, cfg, current)
        foundry_name = AGENT_TO_FOUNDRY_NAME[current]

        if current not in fs.announced_participants:
            fs.announced_participants.add(current)
            version = await get_resolved_version(cfg, current)
            await store.publish(
                session.session_id,
                event(
                    "participant",
                    {
                        "agent_id": current,
                        "foundry_name": foundry_name,
                        "version": version,
                    },
                ),
            )

        await store.publish(
            session.session_id,
            event("agent_turn_start", {"turn": turn, "agent": current}),
        )

        run_input = _apply_handoff_preface(current, next_input)

        # Reuse this agent's prior service session id (Foundry's
        # previous_response_id chain) so the SME keeps full history
        # across turns within this chat session.
        prior_sid = fs.threads.get(foundry_name)
        agent_session = AgentSession(service_session_id=prior_sid)

        try:
            response = await agent.run(
                run_input, session=agent_session,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("agent %s run failed", current)
            await store.publish(
                session.session_id,
                event("error", {"message": f"agent {current}: {e}"}),
            )
            break

        new_sid = _extract_service_session_id(response, agent_session)
        if new_sid and fs.threads.get(foundry_name) != new_sid:
            fs.threads[foundry_name] = new_sid
            log.info(
                "session %s agent %s service_session_id=%s",
                session.session_id, foundry_name, new_sid,
            )

        await _surface_function_results(ctx, response)

        text = _extract_text(response).strip()
        handoff = _parse_handoff_sentinel(text)
        if handoff and handoff in HANDOFF_TARGETS.get(current, ()):
            ctx.next_agent = handoff
            text = _strip_handoff_sentinel(text)

        if text:
            await store.publish(
                session.session_id,
                event(
                    "agent_message",
                    {"turn": turn, "agent": current, "text": text},
                ),
            )
            session.transcript.append(
                TranscriptMessage(role="assistant", agent=current, text=text)
            )

        if ctx.next_agent and ctx.next_agent != current:
            current = ctx.next_agent
            await emit_router_decision(store, session.session_id, current)
            next_input = ""
            continue
        break

    await store.publish(
        session.session_id,
        event("final", {"turn": turn, "session_id": session.session_id}),
    )
    return current


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
