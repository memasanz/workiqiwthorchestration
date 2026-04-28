"""Profile-gated tool registration tests.

Each AGENT_PROFILE must expose exactly the documented tool matrix and nothing
else. We exercise this by importing the server module fresh per profile,
calling ``register_profile_tools(...)``, and inspecting the registered names.
"""
from __future__ import annotations

import importlib
import sys

import pytest


EXPECTED = {
    "submissions": {
        "get_routing",
        "create_project",
        "submit_questions",
        "update_project_status",
        "get_project",
    },
    "tax_sme": {
        "get_project",
        "get_my_assignments",
        "get_question",
        "save_draft",
        "submit_answer",
        "update_question_status",
        "assign_question",
        "set_question_classification",
    },
    "legal_sme": {
        "get_project",
        "get_my_assignments",
        "get_question",
        "save_draft",
        "submit_answer",
        "update_question_status",
        "assign_question",
        "set_question_classification",
    },
}


def _fresh_server_module():
    """Re-import the server module so each test starts with no registered tools."""
    sys.modules.pop("mcp_server.server", None)
    return importlib.import_module("mcp_server.server")


async def _list_tool_names(srv) -> set[str]:
    tools = await srv.mcp.list_tools()
    # FastMCP list_tools() returns a list of Tool objects.
    return {t.name for t in tools}


@pytest.mark.parametrize("profile", sorted(EXPECTED.keys()))
async def test_profile_registers_exact_tool_set(profile: str) -> None:
    srv = _fresh_server_module()
    registered = srv.register_profile_tools(profile)
    assert set(registered) == EXPECTED[profile], (
        f"profile {profile!r}: returned set mismatch"
    )

    names = await _list_tool_names(srv)
    assert names == EXPECTED[profile], (
        f"profile {profile!r}: registered tools {sorted(names)} "
        f"!= expected {sorted(EXPECTED[profile])}"
    )

    # Tools NOT in the matrix must not be registered for this profile.
    all_tools = set(srv._ALL_TOOLS.keys())
    not_allowed = all_tools - EXPECTED[profile]
    leaked = names & not_allowed
    assert not leaked, f"profile {profile!r} leaked tools: {sorted(leaked)}"


def test_invalid_profile_raises() -> None:
    srv = _fresh_server_module()
    with pytest.raises(ValueError):
        srv.register_profile_tools("everything")


def test_matrix_is_authoritative() -> None:
    srv = _fresh_server_module()
    for profile, expected in EXPECTED.items():
        assert set(srv.PROFILE_TOOLS[profile]) == expected, (
            f"server PROFILE_TOOLS[{profile!r}] drifted from test matrix"
        )
