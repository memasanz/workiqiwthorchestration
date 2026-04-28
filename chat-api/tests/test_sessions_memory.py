import asyncio

import pytest

from chat_api.sessions import SessionStore, new_request_id
from chat_api.transcript import (
    ApprovalDecision,
    ApprovalRequest,
    Session,
)


def _sess() -> Session:
    return Session(
        caller_email="e2e@x.com",
        caller_oid="oid-1",
        caller_name="E2E",
    )


def test_session_create_and_get():
    store = SessionStore()
    s = _sess()
    store.create(s)
    rt = store.get(s.session_id)
    assert rt.session.caller_email == "e2e@x.com"


@pytest.mark.asyncio
async def test_publish_and_subscribe_replays_history():
    store = SessionStore()
    s = _sess()
    store.create(s)
    await store.publish(s.session_id, {"event": "a", "data": "{}"})
    await store.publish(s.session_id, {"event": "b", "data": "{}"})

    received: list[dict[str, str]] = []
    sub = store.subscribe(s.session_id)

    async def consume():
        async for ev in sub:
            received.append(ev)
            if len(received) >= 2:
                break

    task = asyncio.create_task(consume())
    await asyncio.wait_for(task, timeout=2.0)
    assert [r["event"] for r in received] == ["a", "b"]


@pytest.mark.asyncio
async def test_register_and_resolve_approval():
    store = SessionStore()
    s = _sess()
    store.create(s)
    rid = new_request_id()
    req = ApprovalRequest(
        request_id=rid, turn=1, agent="submissions",
        tool="create_project", args={"name": "x"}, prompt="?",
    )
    fut = store.register_approval(s.session_id, req)
    decision = ApprovalDecision(decision="approve")
    ok = store.resolve_approval(s.session_id, rid, decision)
    assert ok is True
    result = await asyncio.wait_for(fut, timeout=1.0)
    assert result.decision == "approve"


def test_resolve_unknown_returns_false():
    store = SessionStore()
    s = _sess()
    store.create(s)
    ok = store.resolve_approval(
        s.session_id, "nope",
        ApprovalDecision(decision="approve"),
    )
    assert ok is False
