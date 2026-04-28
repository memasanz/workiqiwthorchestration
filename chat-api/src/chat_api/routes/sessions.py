"""Session lifecycle + SSE + approval routes (chat-api 0.5.0).

In 0.5.0 every request that boots Foundry agents (create session, post
message) builds a fresh ``OnBehalfOfCredential`` from the caller's
bearer JWT and passes it into ``make_foundry_session``. Tokens expire,
so we deliberately do NOT cache an OBO credential across requests.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

try:  # pragma: no cover - optional dep
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover
    _otel_trace = None  # type: ignore[assignment]

from ..af_orchestrator import (
    FoundrySession,
    emit_router_decision,
    make_foundry_session,
    pick_initial_agent,
    run_turn,
)
from ..auth import CallerIdentity, caller_from_request
from ..bootstrap import load_assignments
from ..sessions import get_store
from ..sse import event
from ..transcript import ApprovalDecision, Session, TranscriptMessage

log = logging.getLogger(__name__)
router = APIRouter()
_tracer = _otel_trace.get_tracer(__name__) if _otel_trace is not None else None

_USER_TOKEN_HEADER = "x-user-token"


def _user_token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.headers.get(_USER_TOKEN_HEADER)


def _annotate_span(caller: CallerIdentity) -> None:
    """Stamp ``enduser.id`` / ``user.oid`` on the current OTel span so
    every downstream call (Foundry, MCP, Cosmos) is correlated to the
    user. Mirrors ``ropesandgray/Program.cs`` lines 169-179."""
    if _otel_trace is None:
        return
    span = _otel_trace.get_current_span()
    if span is None or not span.is_recording():
        return
    span.set_attribute("enduser.id", caller.oid)
    span.set_attribute("user.oid", caller.oid)
    if caller.email:
        span.set_attribute("enduser.email", caller.email)


def _build_user_credential(cfg, caller: CallerIdentity):
    """Return an OBO credential for this request, or ``None`` if the
    backend has no user-credential factory wired (dev bypass)."""
    factory = getattr(cfg, "user_cred_factory", None)
    if factory is None or not caller.raw_token:
        return None
    return factory.for_user(caller.raw_token)


class MessageBody(BaseModel):
    text: str | None = None
    approve: str | None = None
    reject: str | None = None
    reason: str | None = None


class ApprovalBody(BaseModel):
    decision: str
    editedArgs: dict[str, Any] | None = None
    reason: str | None = None


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request) -> dict[str, Any]:
    cfg = request.app.state.cfg
    caller = caller_from_request(request, cfg)
    _annotate_span(caller)
    store = get_store()
    user_token = caller.raw_token or _user_token(request)

    session = Session(
        caller_email=caller.email,
        caller_oid=caller.oid,
        caller_name=caller.name,
    )
    rt = store.create(session)

    # Build the per-session FoundryChatClient + AgentSession. This lives for
    # the duration of the chat session and is closed when the session is
    # garbage-collected (best-effort — process restarts drop sessions).
    user_cred = _build_user_credential(cfg, caller)
    fs = make_foundry_session(cfg, user_token=user_token, user_credential=user_cred)
    rt.foundry = fs  # type: ignore[attr-defined]
    rt.user_token = user_token  # type: ignore[attr-defined]

    try:
        assignments = await load_assignments(caller, cfg, user_token=user_token)
    except Exception as e:  # noqa: BLE001
        log.warning("load_assignments failed: %s", e)
        assignments = []
    session.assignments = assignments

    initial_agent = pick_initial_agent(caller.email, assignments, None)
    rt.active_agent = initial_agent  # type: ignore[attr-defined]
    log.info(
        "session %s: caller=%s assignments=%d initial_agent=%s",
        session.session_id, caller.email, len(assignments), initial_agent,
    )

    payload = {
        "sessionId": session.session_id,
        "session_id": session.session_id,
        "caller": caller.as_dict(),
        "caller_email": caller.email,
        "assignments": [a.model_dump() for a in assignments],
    }
    await store.publish(session.session_id, event("session_start", payload))
    await emit_router_decision(store, session.session_id, initial_agent)
    return payload


@router.get("/sessions/{session_id}/assignments")
async def get_assignments(session_id: str, request: Request) -> dict[str, Any]:
    cfg = request.app.state.cfg
    store = get_store()
    try:
        rt = store.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found") from None
    caller = caller_from_request(request, cfg)
    _annotate_span(caller)
    assignments = await load_assignments(caller, cfg, user_token=caller.raw_token or _user_token(request))
    rt.session.assignments = assignments
    return {"assignments": [a.model_dump() for a in assignments]}


@router.post("/sessions/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def post_message(session_id: str, body: MessageBody, request: Request) -> dict[str, Any]:
    cfg = request.app.state.cfg
    store = get_store()
    try:
        rt = store.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found") from None
    caller = caller_from_request(request, cfg)
    _annotate_span(caller)

    token = caller.raw_token or _user_token(request)
    if token:
        rt.user_token = token  # type: ignore[attr-defined]

    # Approve / reject path: just resolve the in-flight future, no new turn.
    approve_id = body.approve
    reject_id = body.reject
    if approve_id or reject_id:
        rid = approve_id or reject_id
        decision = ApprovalDecision(
            decision="approve" if approve_id else "reject",
            reason=body.reason,
        )
        ok = store.resolve_approval(session_id, rid, decision)  # type: ignore[arg-type]
        if not ok:
            raise HTTPException(status_code=404, detail="no pending approval with that id")
        return {"ok": True, "approval_id": rid}

    if not body.text:
        raise HTTPException(status_code=400, detail="text or approve/reject required")

    session = rt.session
    msg_id = f"msg_{uuid.uuid4().hex[:10]}"
    session.transcript.append(TranscriptMessage(role="user", text=body.text, meta={"id": msg_id}))

    # Build a fresh per-request OBO credential and a fresh FoundrySession
    # so each turn talks to Foundry as the current user. Token freshness
    # matters — never reuse OBO across requests.
    user_cred = _build_user_credential(cfg, caller)
    fs: FoundrySession = make_foundry_session(
        cfg, user_token=token, user_credential=user_cred,
    )
    # Carry over already-announced participants AND per-agent thread_ids so
    # the same SME keeps its conversation history across turns. Clients
    # themselves are NOT carried over — they are bound to the previous
    # request's OBO credential which may have expired.
    prior: FoundrySession | None = getattr(rt, "foundry", None)
    if prior is not None:
        fs.announced_participants = prior.announced_participants
        fs.threads = prior.threads
    rt.foundry = fs  # type: ignore[attr-defined]

    async def _drive() -> None:
        try:
            active = getattr(rt, "active_agent", "submissions")
            new_active = await run_turn(
                session, cfg, store, active, fs,
                initial_input=body.text,
            )
            rt.active_agent = new_active  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            log.exception("turn pipeline failed")
            await store.publish(session.session_id, event("error", {"message": str(e)}))

    asyncio.create_task(_drive())
    return {"messageId": msg_id, "message_id": msg_id}


@router.get("/sessions/{session_id}/stream")
async def stream(session_id: str, request: Request) -> EventSourceResponse:
    cfg = request.app.state.cfg
    store = get_store()
    try:
        store.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found") from None
    # Note: EventSource cannot send Authorization headers natively; the SPA
    # uses fetch-event-source which can. In dev bypass mode we still honor
    # ?as_user= so legacy curl smokes work.
    caller = caller_from_request(request, cfg)
    _annotate_span(caller)

    async def event_gen():
        async for ev in store.subscribe(session_id):
            if await request.is_disconnected():
                break
            yield ev

    return EventSourceResponse(event_gen())


@router.post("/sessions/{session_id}/approvals/{request_id}")
async def post_approval(
    session_id: str, request_id: str, body: ApprovalBody, request: Request,
) -> dict[str, Any]:
    """Legacy endpoint kept for compatibility with chat-ui 0.1.1."""
    cfg = request.app.state.cfg
    store = get_store()
    try:
        store.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found") from None
    caller = caller_from_request(request, cfg)
    _annotate_span(caller)
    decision_raw = (body.decision or "").lower()
    if decision_raw not in {"approve", "edit", "reject"}:
        raise HTTPException(status_code=400, detail="decision must be approve|edit|reject")
    decision = ApprovalDecision(
        decision=decision_raw,  # type: ignore[arg-type]
        edited_args=body.editedArgs,
        reason=body.reason,
    )
    ok = store.resolve_approval(session_id, request_id, decision)
    if not ok:
        raise HTTPException(status_code=404, detail="no pending approval with that id")
    return {"ok": True}

