"""Cosmos DB repository - one method per MCP tool."""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from azure.cosmos import exceptions as cosmos_exceptions
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.core import MatchConditions
from azure.identity.aio import DefaultAzureCredential

from .models import (
    ALLOWED_QUESTION_TRANSITIONS,
    Classification,
    ProjectStatus,
    QuestionStatus,
    utcnow_iso,
)

log = logging.getLogger(__name__)

MAX_ETAG_RETRIES = 3


class ConflictError(RuntimeError):
    pass


class NotFoundError(RuntimeError):
    pass


class ValidationError(ValueError):
    pass


class CosmosRepo:
    """Async repository for the three Cosmos containers.

    Pass containers in for tests; in production call `from_env()`.
    """

    def __init__(
        self,
        projects: ContainerProxy,
        questions: ContainerProxy,
        routing: ContainerProxy,
        client: Optional[CosmosClient] = None,
        credential: Optional[DefaultAzureCredential] = None,
    ) -> None:
        self.projects = projects
        self.questions = questions
        self.routing = routing
        self._client = client
        self._credential = credential

    @classmethod
    def from_env(cls) -> "CosmosRepo":
        endpoint = os.environ["COSMOS_ENDPOINT"]
        database_name = os.environ.get("COSMOS_DATABASE", "workflow")
        client_id = os.environ.get("AZURE_CLIENT_ID")
        if client_id:
            credential = DefaultAzureCredential(managed_identity_client_id=client_id)
        else:
            credential = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=credential)
        db = client.get_database_client(database_name)
        return cls(
            projects=db.get_container_client("projects"),
            questions=db.get_container_client("questions"),
            routing=db.get_container_client("routing"),
            client=client,
            credential=credential,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
        if self._credential is not None:
            await self._credential.close()

    # ---------- helpers ----------

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _strip_etag(doc: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in doc.items() if not k.startswith("_")}

    async def _query_one(
        self, container: ContainerProxy, query: str, parameters: list[dict[str, Any]],
        partition_key: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        kwargs: dict[str, Any] = {"query": query, "parameters": parameters}
        if partition_key is not None:
            kwargs["partition_key"] = partition_key
        items = container.query_items(**kwargs)
        async for item in items:
            return item
        return None

    # ---------- create_project ----------

    async def create_project(
        self,
        submitter_id: str,
        display_name: str,
        client_request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if client_request_id:
            existing = await self._query_one(
                self.projects,
                "SELECT * FROM c WHERE c.clientRequestId = @crid",
                [{"name": "@crid", "value": client_request_id}],
            )
            if existing:
                return {"projectId": existing["projectId"], "idempotent": True}

        project_id = self._new_id("proj")
        now = utcnow_iso()
        doc = {
            "id": project_id,
            "projectId": project_id,
            "displayName": display_name,
            "submitterId": submitter_id,
            "status": ProjectStatus.UNCOMPLETED.value,
            "createdAt": now,
            "completedAt": None,
            "clientRequestId": client_request_id,
            "history": [
                {"at": now, "by": submitter_id, "action": "created"}
            ],
        }
        await self.projects.create_item(doc)
        return {"projectId": project_id, "idempotent": False}

    # ---------- submit_questions ----------

    async def submit_questions(
        self,
        project_id: str,
        questions: list[dict[str, Any]],
        client_request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        # ensure project exists
        try:
            await self.projects.read_item(item=project_id, partition_key=project_id)
        except cosmos_exceptions.CosmosResourceNotFoundError as e:
            raise NotFoundError(f"project {project_id} not found") from e

        if client_request_id:
            existing_ids: list[str] = []
            async for item in self.questions.query_items(
                query="SELECT c.questionId FROM c WHERE c.projectId = @pid AND c.clientRequestId = @crid",
                parameters=[
                    {"name": "@pid", "value": project_id},
                    {"name": "@crid", "value": client_request_id},
                ],
                partition_key=project_id,
            ):
                existing_ids.append(item["questionId"])
            if existing_ids:
                return {"questionIds": existing_ids, "idempotent": True}

        ids: list[str] = []
        now = utcnow_iso()
        for q in questions:
            text = q.get("text") or q.get("Text")
            classification = q.get("classification")
            assigned = q.get("assigned_user_id") or q.get("assignedUserId")
            if not text or classification not in ("tax", "legal") or not assigned:
                raise ValidationError(f"invalid question payload: {q!r}")
            qid = self._new_id("q")
            doc = {
                "id": qid,
                "questionId": qid,
                "projectId": project_id,
                "text": text,
                "classification": classification,
                "assignedUserId": assigned,
                "status": QuestionStatus.ASSIGNED.value,
                "draft": None,
                "finalAnswer": None,
                "history": [
                    {"at": now, "by": "submissions-agent", "action": "created"},
                    {"at": now, "by": "submissions-agent",
                     "action": "assigned", "to": assigned},
                ],
                "createdAt": now,
                "updatedAt": now,
                "clientRequestId": client_request_id,
            }
            await self.questions.create_item(doc)
            ids.append(qid)
        return {"questionIds": ids, "idempotent": False}

    # ---------- generic etag-update helper ----------

    async def _update_question_with_retry(
        self,
        question_id: str,
        project_id: str,
        mutate,  # callable: dict -> dict (returns mutated doc)
    ) -> dict[str, Any]:
        last_exc: Optional[Exception] = None
        for _ in range(MAX_ETAG_RETRIES):
            try:
                doc = await self.questions.read_item(
                    item=question_id, partition_key=project_id
                )
            except cosmos_exceptions.CosmosResourceNotFoundError as e:
                raise NotFoundError(f"question {question_id} not found") from e
            etag = doc.get("_etag")
            new_doc = mutate(dict(doc))
            try:
                await self.questions.replace_item(
                    item=question_id,
                    body=new_doc,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                )
                return new_doc
            except cosmos_exceptions.CosmosAccessConditionFailedError as e:
                last_exc = e
                continue
        raise ConflictError(
            f"etag conflict after {MAX_ETAG_RETRIES} attempts on question {question_id}"
        ) from last_exc

    # ---------- submit_answer ----------

    async def submit_answer(
        self, question_id: str, project_id: str, final_answer: str, by: str
    ) -> dict[str, Any]:
        if not final_answer:
            raise ValidationError("final_answer is required")

        def mutate(doc: dict[str, Any]) -> dict[str, Any]:
            now = utcnow_iso()
            doc["finalAnswer"] = final_answer
            doc["status"] = QuestionStatus.APPROVED.value
            doc["updatedAt"] = now
            doc.setdefault("history", []).append(
                {"at": now, "by": by, "action": "submit_answer"}
            )
            return doc

        await self._update_question_with_retry(question_id, project_id, mutate)
        return {"ok": True}

    # ---------- update_question_status ----------

    async def update_question_status(
        self,
        question_id: str,
        project_id: str,
        status: str,
        by: str,
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        try:
            new_status = QuestionStatus(status).value
        except ValueError as e:
            raise ValidationError(f"invalid status {status!r}") from e

        def mutate(doc: dict[str, Any]) -> dict[str, Any]:
            current = doc.get("status", QuestionStatus.ASSIGNED.value)
            allowed = ALLOWED_QUESTION_TRANSITIONS.get(current, set())
            if new_status != current and new_status not in allowed:
                raise ValidationError(
                    f"invalid transition {current!r} -> {new_status!r}"
                )
            now = utcnow_iso()
            doc["status"] = new_status
            doc["updatedAt"] = now
            entry: dict[str, Any] = {
                "at": now, "by": by, "action": "status_change",
                "from": current, "to": new_status,
            }
            if note:
                entry["note"] = note
            doc.setdefault("history", []).append(entry)
            return doc

        await self._update_question_with_retry(question_id, project_id, mutate)
        return {"ok": True}

    # ---------- assign_question ----------

    async def assign_question(
        self,
        question_id: str,
        project_id: str,
        new_assigned_user_id: str,
        by: str,
    ) -> dict[str, Any]:
        if not new_assigned_user_id:
            raise ValidationError("new_assigned_user_id is required")

        def mutate(doc: dict[str, Any]) -> dict[str, Any]:
            now = utcnow_iso()
            previous = doc.get("assignedUserId")
            doc["assignedUserId"] = new_assigned_user_id
            doc["updatedAt"] = now
            doc.setdefault("history", []).append({
                "at": now, "by": by, "action": "assigned",
                "from": previous, "to": new_assigned_user_id,
            })
            return doc

        await self._update_question_with_retry(question_id, project_id, mutate)
        return {"ok": True}

    # ---------- set_question_classification ----------

    async def set_question_classification(
        self,
        question_id: str,
        project_id: str,
        classification: str,
        by: str,
    ) -> dict[str, Any]:
        if classification not in ("tax", "legal"):
            raise ValidationError(
                f"invalid classification {classification!r}; must be tax or legal"
            )

        def mutate(doc: dict[str, Any]) -> dict[str, Any]:
            now = utcnow_iso()
            previous = doc.get("classification")
            doc["classification"] = classification
            doc["updatedAt"] = now
            doc.setdefault("history", []).append({
                "at": now, "by": by, "action": "reclassified",
                "from": previous, "to": classification,
            })
            return doc

        await self._update_question_with_retry(question_id, project_id, mutate)
        return {"ok": True}

    # ---------- save_draft ----------

    async def save_draft(
        self, question_id: str, project_id: str, draft: str, by: str
    ) -> dict[str, Any]:
        def mutate(doc: dict[str, Any]) -> dict[str, Any]:
            now = utcnow_iso()
            doc["draft"] = draft
            doc["updatedAt"] = now
            if doc.get("status") == QuestionStatus.ASSIGNED.value:
                doc["status"] = QuestionStatus.DRAFTING.value
            doc.setdefault("history", []).append(
                {"at": now, "by": by, "action": "save_draft"}
            )
            return doc

        await self._update_question_with_retry(question_id, project_id, mutate)
        return {"ok": True}

    # ---------- update_project_status ----------

    async def update_project_status(
        self, project_id: str, status: str, by: str
    ) -> dict[str, Any]:
        if status not in ("uncompleted", "completed"):
            raise ValidationError(f"invalid project status {status!r}")
        last_exc: Optional[Exception] = None
        for _ in range(MAX_ETAG_RETRIES):
            try:
                doc = await self.projects.read_item(
                    item=project_id, partition_key=project_id
                )
            except cosmos_exceptions.CosmosResourceNotFoundError as e:
                raise NotFoundError(f"project {project_id} not found") from e
            etag = doc.get("_etag")
            now = utcnow_iso()
            previous = doc.get("status")
            doc["status"] = status
            doc["completedAt"] = now if status == "completed" else None
            doc.setdefault("history", []).append({
                "at": now, "by": by, "action": "status_change",
                "from": previous, "to": status,
            })
            try:
                await self.projects.replace_item(
                    item=project_id, body=doc,
                    etag=etag, match_condition=MatchConditions.IfNotModified,
                )
                return {"ok": True}
            except cosmos_exceptions.CosmosAccessConditionFailedError as e:
                last_exc = e
                continue
        raise ConflictError(
            f"etag conflict after {MAX_ETAG_RETRIES} attempts on project {project_id}"
        ) from last_exc

    # ---------- reads ----------

    async def get_my_assignments(
        self,
        user_id: str,
        classification: Optional[str] = None,
        statuses: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        clauses = ["c.assignedUserId = @uid"]
        params: list[dict[str, Any]] = [{"name": "@uid", "value": user_id}]
        if classification:
            if classification not in ("tax", "legal"):
                raise ValidationError(f"invalid classification {classification!r}")
            clauses.append("c.classification = @cls")
            params.append({"name": "@cls", "value": classification})
        if statuses:
            placeholders = []
            for i, s in enumerate(statuses):
                key = f"@s{i}"
                placeholders.append(key)
                params.append({"name": key, "value": s})
            clauses.append(f"c.status IN ({', '.join(placeholders)})")
        query = (
            "SELECT c.questionId, c.projectId, c.text, c.classification, "
            "c.assignedUserId, c.status, c.updatedAt FROM c WHERE "
            + " AND ".join(clauses)
        )
        out: list[dict[str, Any]] = []
        async for item in self.questions.query_items(
            query=query, parameters=params,
        ):
            out.append(item)
        return out

    async def get_project(self, project_id: str) -> dict[str, Any]:
        try:
            doc = await self.projects.read_item(
                item=project_id, partition_key=project_id
            )
        except cosmos_exceptions.CosmosResourceNotFoundError as e:
            raise NotFoundError(f"project {project_id} not found") from e
        # also include question summary
        questions: list[dict[str, Any]] = []
        async for q in self.questions.query_items(
            query="SELECT c.questionId, c.text, c.classification, c.assignedUserId, c.status FROM c WHERE c.projectId = @pid",
            parameters=[{"name": "@pid", "value": project_id}],
            partition_key=project_id,
        ):
            questions.append(q)
        clean = self._strip_etag(doc)
        clean["questions"] = questions
        return clean

    async def get_question(
        self, question_id: str, project_id: str
    ) -> dict[str, Any]:
        try:
            doc = await self.questions.read_item(
                item=question_id, partition_key=project_id
            )
        except cosmos_exceptions.CosmosResourceNotFoundError as e:
            raise NotFoundError(f"question {question_id} not found") from e
        return self._strip_etag(doc)

    # ---------- get_routing (atomic round-robin) ----------

    async def get_routing(self, category: str) -> dict[str, Any]:
        if category not in ("tax", "legal"):
            raise ValidationError(f"invalid category {category!r}")
        last_exc: Optional[Exception] = None
        for _ in range(MAX_ETAG_RETRIES):
            try:
                doc = await self.routing.read_item(
                    item=category, partition_key=category
                )
            except cosmos_exceptions.CosmosResourceNotFoundError as e:
                raise NotFoundError(
                    f"routing for category {category!r} not seeded"
                ) from e
            user_ids: list[str] = doc.get("userIds") or []
            if not user_ids:
                raise ValidationError(
                    f"routing for category {category!r} has no userIds"
                )
            etag = doc.get("_etag")
            current_idx = int(doc.get("roundRobinIndex", 0)) % len(user_ids)
            next_user = user_ids[current_idx]
            new_idx = (current_idx + 1) % len(user_ids)
            doc["roundRobinIndex"] = new_idx
            try:
                await self.routing.replace_item(
                    item=category, body=doc,
                    etag=etag, match_condition=MatchConditions.IfNotModified,
                )
                return {
                    "category": category,
                    "user_ids": user_ids,
                    "next_user_id": next_user,
                    "round_robin_index": new_idx,
                }
            except cosmos_exceptions.CosmosAccessConditionFailedError as e:
                last_exc = e
                continue
        raise ConflictError(
            f"etag conflict after {MAX_ETAG_RETRIES} attempts on routing {category}"
        ) from last_exc

