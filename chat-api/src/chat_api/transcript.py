"""Pydantic models for shared transcript + session state."""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


Role = Literal["system", "user", "assistant", "tool", "router"]


class TranscriptMessage(BaseModel):
    role: Role
    agent: Optional[str] = None  # "submissions" | "tax" | "legal" | None
    text: str
    ts: float = Field(default_factory=lambda: time.time())
    meta: dict[str, Any] = Field(default_factory=dict)


class Assignment(BaseModel):
    project_id: str
    question_id: str
    classification: str
    status: str
    text_preview: str = ""


class ApprovalRequest(BaseModel):
    request_id: str
    turn: int
    agent: str
    tool: str
    args: dict[str, Any]
    prompt: str = ""


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    edited_args: Optional[dict[str, Any]] = None
    reason: Optional[str] = None


class Session(BaseModel):
    session_id: str = Field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:12]}")
    caller_email: str
    caller_oid: str
    caller_name: str
    transcript: list[TranscriptMessage] = Field(default_factory=list)
    assignments: list[Assignment] = Field(default_factory=list)
    turn_counter: int = 0
