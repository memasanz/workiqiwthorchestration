"""In-memory mock for azure.cosmos.aio ContainerProxy.

Simulates: create_item, read_item, replace_item (with etag), query_items.
"""
from __future__ import annotations

import copy
import re
import uuid
from typing import Any, AsyncIterator, Optional

import pytest
import pytest_asyncio
from azure.cosmos import exceptions as cosmos_exceptions

from mcp_server.cosmos_repo import CosmosRepo


class _AsyncIter:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def gen():
            for it in self._items:
                yield it
        return gen()


def _matches(item: dict[str, Any], query: str, params: dict[str, Any]) -> bool:
    """Tiny query matcher that handles only the queries our repo emits."""
    # WHERE clause extraction
    where_match = re.search(r"WHERE\s+(.*)", query, re.IGNORECASE | re.DOTALL)
    if not where_match:
        return True
    where = where_match.group(1).strip()

    # Split on AND
    conditions = [c.strip() for c in re.split(r"\s+AND\s+", where, flags=re.IGNORECASE)]
    for cond in conditions:
        # IN clause
        in_match = re.match(r"c\.(\w+)\s+IN\s*\(([^)]+)\)", cond, re.IGNORECASE)
        if in_match:
            field = in_match.group(1)
            placeholders = [p.strip() for p in in_match.group(2).split(",")]
            values = [params.get(p) for p in placeholders]
            if item.get(field) not in values:
                return False
            continue
        # equality
        eq_match = re.match(r"c\.(\w+)\s*=\s*(@\w+)", cond)
        if eq_match:
            field = eq_match.group(1)
            ph = eq_match.group(2)
            if item.get(field) != params.get(ph):
                return False
            continue
        # unknown clause -> fail-open (treat as match) so tests don't false-negative
    return True


def _project(item: dict[str, Any], query: str) -> dict[str, Any]:
    """Honor SELECT c.field, c.field2 ..."""
    sel_match = re.match(r"SELECT\s+(.*?)\s+FROM\s", query, re.IGNORECASE | re.DOTALL)
    if not sel_match:
        return item
    sel = sel_match.group(1).strip()
    if sel == "*":
        return item
    fields = []
    for part in sel.split(","):
        m = re.match(r"c\.(\w+)", part.strip())
        if m:
            fields.append(m.group(1))
    return {f: item.get(f) for f in fields}


class FakeContainer:
    def __init__(self, name: str, pk_field: str) -> None:
        self.name = name
        self.pk_field = pk_field
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._etag_counter = 0
        self.fail_next_replace_with_conflict = 0  # for test injection

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f"etag-{self._etag_counter}-{uuid.uuid4().hex[:6]}"

    def _key(self, item_id: str, pk: str) -> tuple[str, str]:
        return (pk, item_id)

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        item = copy.deepcopy(body)
        pk = item[self.pk_field]
        item["_etag"] = self._next_etag()
        item["_rid"] = uuid.uuid4().hex
        key = self._key(item["id"], pk)
        if key in self._items:
            raise cosmos_exceptions.CosmosResourceExistsError(
                status_code=409, message="exists"
            )
        self._items[key] = item
        return copy.deepcopy(item)

    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        key = self._key(item, partition_key)
        if key not in self._items:
            raise cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message="not found"
            )
        return copy.deepcopy(self._items[key])

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        etag: Optional[str] = None,
        match_condition: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        if self.fail_next_replace_with_conflict > 0:
            self.fail_next_replace_with_conflict -= 1
            raise cosmos_exceptions.CosmosAccessConditionFailedError(
                status_code=412, message="precondition failed"
            )
        pk = body[self.pk_field]
        key = self._key(item, pk)
        if key not in self._items:
            raise cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message="not found"
            )
        current = self._items[key]
        if etag is not None and current.get("_etag") != etag:
            raise cosmos_exceptions.CosmosAccessConditionFailedError(
                status_code=412, message="etag mismatch"
            )
        new_doc = copy.deepcopy(body)
        new_doc["_etag"] = self._next_etag()
        new_doc["_rid"] = current.get("_rid", uuid.uuid4().hex)
        self._items[key] = new_doc
        return copy.deepcopy(new_doc)

    def query_items(
        self,
        query: str,
        parameters: Optional[list[dict[str, Any]]] = None,
        partition_key: Optional[str] = None,
        enable_cross_partition_query: bool = False,
        **_: Any,
    ) -> _AsyncIter:
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results: list[dict[str, Any]] = []
        for (pk, _id), item in self._items.items():
            if partition_key is not None and pk != partition_key:
                continue
            if not _matches(item, query, param_map):
                continue
            results.append(_project(copy.deepcopy(item), query))
        return _AsyncIter(results)


@pytest_asyncio.fixture
async def repo() -> CosmosRepo:
    projects = FakeContainer("projects", "projectId")
    questions = FakeContainer("questions", "projectId")
    routing = FakeContainer("routing", "category")
    # seed routing
    await routing.create_item({
        "id": "tax",
        "category": "tax",
        "userIds": ["maya@contoso.com", "alex@contoso.com"],
        "roundRobinIndex": 0,
    })
    await routing.create_item({
        "id": "legal",
        "category": "legal",
        "userIds": ["devon@contoso.com", "rae@contoso.com"],
        "roundRobinIndex": 0,
    })
    return CosmosRepo(projects=projects, questions=questions, routing=routing)
