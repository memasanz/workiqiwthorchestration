"""SSE event envelope helpers."""
from __future__ import annotations

import json
from typing import Any


def event(name: str, data: dict[str, Any]) -> dict[str, str]:
    """Return a dict that sse-starlette accepts: {event, data}."""
    return {"event": name, "data": json.dumps(data, default=str, ensure_ascii=False)}
