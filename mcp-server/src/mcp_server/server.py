"""FastMCP server exposing the multi-person workflow tools.

Tool surface is gated by the AGENT_PROFILE env var so a process started with
``AGENT_PROFILE=tax_sme`` only registers the tools tax SMEs need. There is no
``all`` profile by design — the server refuses to start without a valid
profile so we never silently expose every tool.
"""
from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Literal, Optional

from fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .cosmos_repo import (
    ConflictError,
    CosmosRepo,
    NotFoundError,
    ValidationError,
)
from .logging_setup import configure_logging

log = logging.getLogger(__name__)

VALID_PROFILES = ("submissions", "tax_sme", "legal_sme")

PROFILE_TOOLS: dict[str, frozenset[str]] = {
    "submissions": frozenset({
        "get_routing",
        "create_project",
        "submit_questions",
        "update_project_status",
        "get_project",
    }),
    "tax_sme": frozenset({
        "get_project",
        "get_my_assignments",
        "get_question",
        "save_draft",
        "submit_answer",
        "update_question_status",
        "assign_question",
        "set_question_classification",
    }),
    "legal_sme": frozenset({
        "get_project",
        "get_my_assignments",
        "get_question",
        "save_draft",
        "submit_answer",
        "update_question_status",
        "assign_question",
        "set_question_classification",
    }),
}

mcp: FastMCP = FastMCP(name="multi-person-workflow")
_repo: Optional[CosmosRepo] = None
_active_profile: Optional[str] = None


def get_repo() -> CosmosRepo:
    global _repo
    if _repo is None:
        _repo = CosmosRepo.from_env()
    return _repo


def set_repo(repo: CosmosRepo) -> None:
    """For tests."""
    global _repo
    _repo = repo


# ---------------- health endpoint ----------------

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    tools = sorted(PROFILE_TOOLS.get(_active_profile or "", set()))
    return JSONResponse({
        "status": "ok",
        "profile": _active_profile,
        "tools": tools,
    })


@mcp.custom_route("/", methods=["GET"])
async def root(_: Request) -> JSONResponse:
    return JSONResponse({
        "service": "multi-person-workflow-mcp",
        "status": "ok",
        "profile": _active_profile,
    })


# ---------------- tool implementations (plain functions) ----------------
# These are NOT decorated with @mcp.tool — registration is done in
# register_profile_tools() below so we can gate by AGENT_PROFILE.

async def create_project(
    submitter_id: Annotated[str, Field(description="User ID of the submitter")],
    display_name: Annotated[str, Field(description="Human-readable project name")],
    client_request_id: Annotated[
        Optional[str],
        Field(description="Client-supplied dedupe key; retries return same projectId"),
    ] = None,
) -> dict[str, Any]:
    """Create a new project (status='uncompleted'). Idempotent on client_request_id."""
    return await get_repo().create_project(submitter_id, display_name, client_request_id)


async def submit_questions(
    project_id: Annotated[str, Field(description="Target project ID")],
    questions: Annotated[
        list[dict[str, Any]],
        Field(description="List of {text, classification(tax|legal), assigned_user_id}"),
    ],
    client_request_id: Annotated[Optional[str], Field(description="Idempotency key")] = None,
) -> dict[str, Any]:
    """Add one or more questions to an existing project. Idempotent on client_request_id."""
    return await get_repo().submit_questions(project_id, questions, client_request_id)


async def submit_answer(
    question_id: str,
    project_id: str,
    final_answer: str,
    by: Annotated[str, Field(description="Actor user ID")],
) -> dict[str, Any]:
    """Persist a final answer for a question and flip its status to 'approved'."""
    return await get_repo().submit_answer(question_id, project_id, final_answer, by)


async def update_project_status(
    project_id: str,
    status: Literal["uncompleted", "completed"],
    by: str,
) -> dict[str, Any]:
    """Toggle project status between uncompleted and completed."""
    return await get_repo().update_project_status(project_id, status, by)


async def update_question_status(
    question_id: str,
    project_id: str,
    status: Literal[
        "assigned", "drafting", "awaiting_review",
        "approved", "rejected", "escalated",
    ],
    by: str,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Change a question's status (validated against state machine)."""
    return await get_repo().update_question_status(
        question_id, project_id, status, by, note
    )


async def assign_question(
    question_id: str,
    project_id: str,
    new_assigned_user_id: str,
    by: str,
) -> dict[str, Any]:
    """Re-assign a question to a different user."""
    return await get_repo().assign_question(
        question_id, project_id, new_assigned_user_id, by
    )


async def set_question_classification(
    question_id: str,
    project_id: str,
    classification: Literal["tax", "legal"],
    by: str,
) -> dict[str, Any]:
    """Change a question's classification. Does not auto-reassign."""
    return await get_repo().set_question_classification(
        question_id, project_id, classification, by
    )


async def save_draft(
    question_id: str,
    project_id: str,
    draft: str,
    by: str,
) -> dict[str, Any]:
    """Save an in-progress draft. Promotes 'assigned' -> 'drafting'."""
    return await get_repo().save_draft(question_id, project_id, draft, by)


async def get_my_assignments(
    user_id: str,
    classification: Optional[Literal["tax", "legal"]] = None,
    statuses: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """List questions assigned to a user, optionally filtered."""
    return await get_repo().get_my_assignments(user_id, classification, statuses)


async def get_project(project_id: str) -> dict[str, Any]:
    """Get a project plus its question summaries."""
    return await get_repo().get_project(project_id)


async def get_question(question_id: str, project_id: str) -> dict[str, Any]:
    """Get one full question record."""
    return await get_repo().get_question(question_id, project_id)


async def get_routing(
    category: Literal["tax", "legal"],
) -> dict[str, Any]:
    """Get routing for a category and atomically advance the round-robin pointer.

    Returns the user picked for this call (next_user_id) and the new index.
    """
    return await get_repo().get_routing(category)


# Map of name -> implementation. Used by register_profile_tools().
_ALL_TOOLS = {
    "create_project": create_project,
    "submit_questions": submit_questions,
    "submit_answer": submit_answer,
    "update_project_status": update_project_status,
    "update_question_status": update_question_status,
    "assign_question": assign_question,
    "set_question_classification": set_question_classification,
    "save_draft": save_draft,
    "get_my_assignments": get_my_assignments,
    "get_project": get_project,
    "get_question": get_question,
    "get_routing": get_routing,
}


def register_profile_tools(profile: str) -> list[str]:
    """Register only the tools allowed for ``profile`` on the FastMCP instance.

    Returns the sorted list of registered tool names.
    """
    if profile not in PROFILE_TOOLS:
        raise ValueError(
            f"AGENT_PROFILE={profile!r} invalid. "
            f"Expected one of: {', '.join(VALID_PROFILES)}"
        )
    global _active_profile
    _active_profile = profile
    allowed = PROFILE_TOOLS[profile]
    registered: list[str] = []
    for name in sorted(allowed):
        fn = _ALL_TOOLS[name]
        # FastMCP returns the tool but we don't need a handle; decorating
        # the bare function is what registers it.
        mcp.tool(fn)
        registered.append(name)
    return registered


# ---------------- entrypoint ----------------

def _read_profile_or_die() -> str:
    profile = os.environ.get("AGENT_PROFILE", "").strip()
    if not profile:
        raise SystemExit(
            "AGENT_PROFILE env var is required. "
            f"Set it to one of: {', '.join(VALID_PROFILES)}"
        )
    if profile not in PROFILE_TOOLS:
        raise SystemExit(
            f"AGENT_PROFILE={profile!r} is not valid. "
            f"Expected one of: {', '.join(VALID_PROFILES)}"
        )
    return profile


def main() -> None:
    configure_logging()
    profile = _read_profile_or_die()
    registered = register_profile_tools(profile)
    port = int(os.environ.get("PORT", "8080"))
    log.info(
        "Starting multi-person-workflow MCP server: profile=%s tools=%s port=%d",
        profile, registered, port,
    )
    mcp.run(transport="http", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
