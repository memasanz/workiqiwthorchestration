"""Unit tests for CosmosRepo (covers all 12 MCP tool methods)."""
from __future__ import annotations

import pytest

from mcp_server.cosmos_repo import (
    ConflictError,
    CosmosRepo,
    NotFoundError,
    ValidationError,
)


pytestmark = pytest.mark.asyncio


async def _make_project_with_question(repo: CosmosRepo) -> tuple[str, str]:
    res = await repo.create_project("user1@x", "ACME-2026-Q1")
    pid = res["projectId"]
    qres = await repo.submit_questions(
        pid,
        [{"text": "Can we deduct R&D?", "classification": "tax",
          "assigned_user_id": "maya@contoso.com"}],
    )
    return pid, qres["questionIds"][0]


async def test_create_project_happy(repo: CosmosRepo):
    res = await repo.create_project("u@x", "P1")
    assert res["projectId"].startswith("proj_")
    assert res["idempotent"] is False


async def test_create_project_idempotent(repo: CosmosRepo):
    a = await repo.create_project("u@x", "P1", client_request_id="req-1")
    b = await repo.create_project("u@x", "P1", client_request_id="req-1")
    assert a["projectId"] == b["projectId"]
    assert b["idempotent"] is True


async def test_submit_questions_happy_and_idempotent(repo: CosmosRepo):
    p = await repo.create_project("u@x", "P1")
    pid = p["projectId"]
    a = await repo.submit_questions(
        pid,
        [
            {"text": "q1", "classification": "tax", "assigned_user_id": "maya@x"},
            {"text": "q2", "classification": "legal", "assigned_user_id": "devon@x"},
        ],
        client_request_id="batch-1",
    )
    assert len(a["questionIds"]) == 2
    b = await repo.submit_questions(
        pid, [{"text": "ignored", "classification": "tax", "assigned_user_id": "x@x"}],
        client_request_id="batch-1",
    )
    assert b["idempotent"] is True
    assert b["questionIds"] == a["questionIds"]


async def test_submit_questions_validation(repo: CosmosRepo):
    p = await repo.create_project("u@x", "P1")
    with pytest.raises(ValidationError):
        await repo.submit_questions(
            p["projectId"],
            [{"text": "x", "classification": "bogus", "assigned_user_id": "u"}],
        )


async def test_submit_questions_unknown_project(repo: CosmosRepo):
    with pytest.raises(NotFoundError):
        await repo.submit_questions(
            "proj_does_not_exist",
            [{"text": "x", "classification": "tax", "assigned_user_id": "u"}],
        )


async def test_submit_answer_flips_to_approved(repo: CosmosRepo):
    pid, qid = await _make_project_with_question(repo)
    res = await repo.submit_answer(qid, pid, "Yes, see IRC §174.", by="maya@x")
    assert res == {"ok": True}
    q = await repo.get_question(qid, pid)
    assert q["status"] == "approved"
    assert q["finalAnswer"].startswith("Yes")
    actions = [h["action"] for h in q["history"]]
    assert "submit_answer" in actions


async def test_save_draft_promotes_status(repo: CosmosRepo):
    pid, qid = await _make_project_with_question(repo)
    await repo.save_draft(qid, pid, "draft text", by="maya@x")
    q = await repo.get_question(qid, pid)
    assert q["status"] == "drafting"
    assert q["draft"] == "draft text"


async def test_update_question_status_valid_and_invalid(repo: CosmosRepo):
    pid, qid = await _make_project_with_question(repo)
    await repo.update_question_status(qid, pid, "drafting", by="maya@x")
    await repo.update_question_status(qid, pid, "rejected", by="maya@x", note="needs more")
    q = await repo.get_question(qid, pid)
    assert q["status"] == "rejected"

    # rejected -> approved is not allowed
    with pytest.raises(ValidationError):
        await repo.update_question_status(qid, pid, "approved", by="maya@x")
    # bogus status
    with pytest.raises(ValidationError):
        await repo.update_question_status(qid, pid, "nope", by="maya@x")


async def test_assign_question(repo: CosmosRepo):
    pid, qid = await _make_project_with_question(repo)
    await repo.assign_question(qid, pid, "alex@x", by="boss@x")
    q = await repo.get_question(qid, pid)
    assert q["assignedUserId"] == "alex@x"


async def test_set_question_classification(repo: CosmosRepo):
    pid, qid = await _make_project_with_question(repo)
    await repo.set_question_classification(qid, pid, "legal", by="maya@x")
    q = await repo.get_question(qid, pid)
    assert q["classification"] == "legal"
    with pytest.raises(ValidationError):
        await repo.set_question_classification(qid, pid, "bogus", by="maya@x")


async def test_update_project_status(repo: CosmosRepo):
    res = await repo.create_project("u@x", "P1")
    pid = res["projectId"]
    await repo.update_project_status(pid, "completed", by="u@x")
    p = await repo.get_project(pid)
    assert p["status"] == "completed"
    assert p["completedAt"] is not None


async def test_get_my_assignments_filters(repo: CosmosRepo):
    p = await repo.create_project("u@x", "P1")
    pid = p["projectId"]
    await repo.submit_questions(pid, [
        {"text": "q1", "classification": "tax", "assigned_user_id": "maya@x"},
        {"text": "q2", "classification": "legal", "assigned_user_id": "maya@x"},
        {"text": "q3", "classification": "tax", "assigned_user_id": "alex@x"},
    ])
    mine = await repo.get_my_assignments("maya@x")
    assert len(mine) == 2
    tax_only = await repo.get_my_assignments("maya@x", classification="tax")
    assert len(tax_only) == 1
    assert tax_only[0]["classification"] == "tax"
    none_active = await repo.get_my_assignments(
        "maya@x", statuses=["approved"],
    )
    assert none_active == []


async def test_get_project_includes_questions(repo: CosmosRepo):
    pid, qid = await _make_project_with_question(repo)
    p = await repo.get_project(pid)
    assert "questions" in p
    assert any(q["questionId"] == qid for q in p["questions"])


async def test_get_routing_round_robin(repo: CosmosRepo):
    a = await repo.get_routing("tax")
    b = await repo.get_routing("tax")
    c = await repo.get_routing("tax")
    users = [a["next_user_id"], b["next_user_id"], c["next_user_id"]]
    # tax has 2 users -> sequence cycles
    assert users[0] != users[1]
    assert users[2] == users[0]
    assert c["round_robin_index"] == 1


async def test_get_routing_unknown_category(repo: CosmosRepo):
    with pytest.raises(ValidationError):
        await repo.get_routing("bogus")


async def test_etag_conflict_retries(repo: CosmosRepo):
    pid, qid = await _make_project_with_question(repo)
    # inject 2 conflicts on the questions container; 3rd attempt should succeed
    repo.questions.fail_next_replace_with_conflict = 2  # type: ignore[attr-defined]
    res = await repo.submit_answer(qid, pid, "Final", by="maya@x")
    assert res == {"ok": True}


async def test_etag_conflict_exhausts(repo: CosmosRepo):
    pid, qid = await _make_project_with_question(repo)
    repo.questions.fail_next_replace_with_conflict = 99  # type: ignore[attr-defined]
    with pytest.raises(ConflictError):
        await repo.submit_answer(qid, pid, "Final", by="maya@x")


async def test_get_question_not_found(repo: CosmosRepo):
    with pytest.raises(NotFoundError):
        await repo.get_question("q_missing", "proj_missing")
