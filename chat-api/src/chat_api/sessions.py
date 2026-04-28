"""In-memory session store + per-session SSE pub/sub + approval futures."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .transcript import ApprovalDecision, ApprovalRequest, Session

log = logging.getLogger(__name__)


@dataclass
class _SessionRuntime:
    session: Session
    queues: list[asyncio.Queue[dict[str, str] | None]] = field(default_factory=list)
    pending_approvals: dict[str, asyncio.Future[ApprovalDecision]] = field(default_factory=dict)
    pending_requests: dict[str, ApprovalRequest] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    history: list[dict[str, str]] = field(default_factory=list)  # buffered events for late subscribers


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, _SessionRuntime] = {}

    def create(self, session: Session) -> _SessionRuntime:
        rt = _SessionRuntime(session=session)
        self._sessions[session.session_id] = rt
        return rt

    def get(self, session_id: str) -> _SessionRuntime:
        rt = self._sessions.get(session_id)
        if rt is None:
            raise KeyError(session_id)
        return rt

    async def publish(self, session_id: str, event: dict[str, str]) -> None:
        rt = self.get(session_id)
        rt.history.append(event)
        # Cap history so memory doesn't grow forever.
        if len(rt.history) > 500:
            del rt.history[:100]
        for q in list(rt.queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("session %s queue full, dropping event", session_id)

    async def subscribe(self, session_id: str) -> AsyncIterator[dict[str, str]]:
        rt = self.get(session_id)
        q: asyncio.Queue[dict[str, str] | None] = asyncio.Queue(maxsize=1024)
        # Replay buffered history first so late subscribers don't miss session_start.
        for ev in rt.history:
            q.put_nowait(ev)
        rt.queues.append(q)
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield ev
        finally:
            try:
                rt.queues.remove(q)
            except ValueError:
                pass

    def register_approval(self, session_id: str, req: ApprovalRequest) -> asyncio.Future[ApprovalDecision]:
        rt = self.get(session_id)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        rt.pending_approvals[req.request_id] = fut
        rt.pending_requests[req.request_id] = req
        return fut

    def resolve_approval(self, session_id: str, request_id: str, decision: ApprovalDecision) -> bool:
        rt = self.get(session_id)
        fut = rt.pending_approvals.pop(request_id, None)
        rt.pending_requests.pop(request_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:10]}"


_global_store: SessionStore | None = None


def get_store() -> SessionStore:
    global _global_store
    if _global_store is None:
        _global_store = SessionStore()
    return _global_store
