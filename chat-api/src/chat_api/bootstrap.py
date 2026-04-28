"""Bootstrap a session by fetching the caller's open assignments from MCP."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .auth import CallerIdentity
from .config import Config
from .tools import call_tool
from .transcript import Assignment

log = logging.getLogger(__name__)


async def _fetch_one(
    mcp_url: str, classification: str, user_id: str, user_token: str | None = None
) -> list[dict[str, Any]]:
    try:
        result = await asyncio.wait_for(
            call_tool(
                mcp_url,
                "get_my_assignments",
                {"user_id": user_id, "classification": classification},
                user_token=user_token,
            ),
            timeout=20.0,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("get_my_assignments(%s) failed: %s", classification, e or type(e).__name__)
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("assignments", "items", "questions", "value"):
            v = result.get(key)
            if isinstance(v, list):
                return v
    return []


def _to_assignment(item: dict[str, Any], classification: str) -> Assignment | None:
    qid = item.get("questionId") or item.get("id") or item.get("question_id")
    pid = item.get("projectId") or item.get("project_id")
    if not qid or not pid:
        return None
    return Assignment(
        project_id=pid,
        question_id=qid,
        classification=item.get("classification") or classification,
        status=item.get("status") or "assigned",
        text_preview=(item.get("text") or item.get("preview") or "")[:140],
    )


async def load_assignments(
    caller: CallerIdentity, cfg: Config, user_token: str | None = None
) -> list[Assignment]:
    out: list[Assignment] = []
    pairs = (("tax", "tax_sme"), ("legal", "legal_sme"))
    results = await asyncio.gather(
        *[
            _fetch_one(cfg.mcp_urls[profile], cls, caller.email, user_token=user_token)
            for cls, profile in pairs
        ],
        return_exceptions=False,
    )
    for (classification, _), items in zip(pairs, results):
        for raw in items:
            a = _to_assignment(raw, classification)
            if a is not None:
                out.append(a)
    return out
