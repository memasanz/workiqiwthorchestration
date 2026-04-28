"""Pydantic models and enums for the multi-person workflow MCP server."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Classification(str, Enum):
    TAX = "tax"
    LEGAL = "legal"


class ProjectStatus(str, Enum):
    UNCOMPLETED = "uncompleted"
    COMPLETED = "completed"


class QuestionStatus(str, Enum):
    ASSIGNED = "assigned"
    DRAFTING = "drafting"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


# Allowed transitions (forward direction). 'rejected' may go back to 'drafting'.
ALLOWED_QUESTION_TRANSITIONS: dict[str, set[str]] = {
    QuestionStatus.ASSIGNED.value: {
        QuestionStatus.DRAFTING.value,
        QuestionStatus.AWAITING_REVIEW.value,
        QuestionStatus.APPROVED.value,
        QuestionStatus.ESCALATED.value,
        QuestionStatus.REJECTED.value,
    },
    QuestionStatus.DRAFTING.value: {
        QuestionStatus.AWAITING_REVIEW.value,
        QuestionStatus.APPROVED.value,
        QuestionStatus.ESCALATED.value,
        QuestionStatus.REJECTED.value,
    },
    QuestionStatus.AWAITING_REVIEW.value: {
        QuestionStatus.APPROVED.value,
        QuestionStatus.REJECTED.value,
        QuestionStatus.ESCALATED.value,
    },
    QuestionStatus.REJECTED.value: {
        QuestionStatus.DRAFTING.value,
        QuestionStatus.ESCALATED.value,
    },
    QuestionStatus.ESCALATED.value: {
        QuestionStatus.DRAFTING.value,
        QuestionStatus.ASSIGNED.value,
    },
    QuestionStatus.APPROVED.value: set(),  # terminal
}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoryEntry(BaseModel):
    at: str
    by: str
    action: str
    details: Optional[dict[str, Any]] = None


class Project(BaseModel):
    id: str
    projectId: str
    displayName: str
    submitterId: str
    status: ProjectStatus = ProjectStatus.UNCOMPLETED
    createdAt: str
    completedAt: Optional[str] = None
    clientRequestId: Optional[str] = None


class QuestionInput(BaseModel):
    text: str = Field(min_length=1)
    classification: Classification
    assigned_user_id: str = Field(min_length=1)


class Question(BaseModel):
    id: str
    questionId: str
    projectId: str
    text: str
    classification: Classification
    assignedUserId: str
    status: QuestionStatus = QuestionStatus.ASSIGNED
    draft: Optional[str] = None
    finalAnswer: Optional[str] = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    createdAt: str
    updatedAt: str
    clientRequestId: Optional[str] = None


class Routing(BaseModel):
    id: str
    category: Classification
    userIds: list[str]
    roundRobinIndex: int = 0
