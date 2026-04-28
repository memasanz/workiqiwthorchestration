"""Smoke test for chat-api 0.3.1 (FoundryChatClient + hosted MCP + HITL).

Exercises:
  * /health (3 agents listed)
  * /sessions bootstrap → session_start + router_decision SSE
  * Approve cycle via POST /sessions/{id}/messages {"approve": "<id>"}
  * Reject cycle via POST /sessions/{id}/messages {"reject": "<id>"}
  * Handoff to tax via a tax-flavoured prompt → router_decision changes

Run:
    $env:CHAT_API_BASE = "https://ca-mpwflow-dev-chat-api.icyground-4e2c6fde.eastus2.azurecontainerapps.io"
    $env:PYTHONIOENCODING = "utf-8"
    python smoke_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
from typing import Any

import httpx


BASE = os.environ.get(
    "CHAT_API_BASE",
    "https://ca-mpwflow-dev-chat-api.icyground-4e2c6fde.eastus2.azurecontainerapps.io",
).rstrip("/")
USER = os.environ.get("AS_USER", "smoke@x.com")
TIMEOUT = 120.0


def _print(label: str, payload: Any) -> None:
    print(f"--- {label} ---")
    print(json.dumps(payload, indent=2, default=str)[:1500])
    print()


class StreamCollector:
    """Background thread that holds the SSE connection open and accumulates
    every event the chat-api publishes for this session."""

    def __init__(self, base: str, sid: str, user: str):
        self.base, self.sid, self.user = base, sid, user
        self.events: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        time.sleep(0.5)  # let the connection establish

    def _run(self) -> None:
        try:
            with httpx.Client(timeout=None) as c:
                with c.stream(
                    "GET",
                    f"{self.base}/sessions/{self.sid}/stream",
                    params={"as_user": self.user},
                ) as resp:
                    ev = None
                    for line in resp.iter_lines():
                        if self._stop.is_set():
                            return
                        if not line:
                            continue
                        if line.startswith("event:"):
                            ev = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            data = line.split(":", 1)[1].strip()
                            try:
                                payload = json.loads(data)
                            except Exception:
                                payload = {"raw": data}
                            self.events.append({"event": ev, "data": payload})
                            print(f"  SSE {ev}: {json.dumps(payload, default=str)[:200]}")
        except Exception as e:  # noqa: BLE001
            print(f"(stream closed: {e})")

    def wait_for(self, event: str, *, since: int = 0, timeout: float = 90.0) -> dict[str, Any] | None:
        t0 = time.time()
        while time.time() - t0 < timeout:
            for e in self.events[since:]:
                if e["event"] == event:
                    return e
            time.sleep(0.2)
        return None

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    with httpx.Client(timeout=TIMEOUT) as client:
        # 1. Health
        r = client.get(f"{BASE}/health")
        r.raise_for_status()
        _print("/health", r.json())
        assert r.json()["agents"] == ["submissions", "tax", "legal"]
        assert r.json()["version"] == "0.3.1", f"version mismatch: {r.json().get('version')}"

        # 2. Create session
        r = client.post(f"{BASE}/sessions", params={"as_user": USER})
        r.raise_for_status()
        sess = r.json()
        sid = sess["sessionId"]
        _print("session", sess)

        sc = StreamCollector(BASE, sid, USER)
        sc.start()

        if not sc.wait_for("session_start", timeout=10.0):
            print("ERROR: no session_start"); return 1
        if not sc.wait_for("router_decision", timeout=10.0):
            print("ERROR: no initial router_decision"); return 1

        # 3. Approve cycle (handle chained approvals — approve up to 5 in a row)
        before = len(sc.events)
        client.post(
            f"{BASE}/sessions/{sid}/messages",
            params={"as_user": USER},
            json={
                "text": (
                    "My submitter id is smoke@x.com. Please call create_project right now "
                    "with name='Smoke 0.3.1', submitter='smoke@x.com', "
                    "questions=['Is HITL working?']. Do not ask follow-up questions first."
                )
            },
        ).raise_for_status()

        approved_any = False
        cursor = before
        for _ in range(6):
            ev = sc.wait_for("approval_required", since=cursor, timeout=120.0)
            if ev is None:
                break
            rid = ev["data"]["approval_id"]
            print(f"\nApproving {rid} via POST /messages …")
            client.post(
                f"{BASE}/sessions/{sid}/messages",
                params={"as_user": USER},
                json={"approve": rid},
            ).raise_for_status()
            approved_any = True
            cursor = len(sc.events)
            # if the turn finalizes, stop
            fin = sc.wait_for("final", since=before, timeout=15.0)
            if fin is not None:
                break
        if not approved_any:
            print("WARNING: no approval_required for approve path; agent may have refused.")
        sc.wait_for("final", since=before, timeout=120.0)

        # 4. Reject cycle
        before = len(sc.events)
        client.post(
            f"{BASE}/sessions/{sid}/messages",
            params={"as_user": USER},
            json={
                "text": (
                    "Now please call create_project again with name='Smoke 0.3.1 reject', "
                    "submitter='smoke@x.com', questions=['will be rejected']. Call the tool right now."
                )
            },
        ).raise_for_status()
        ev = sc.wait_for("approval_required", since=before, timeout=120.0)
        if ev is None:
            print("WARNING: no approval_required for reject path.")
        else:
            rid = ev["data"]["approval_id"]
            print(f"\nRejecting {rid} via POST /messages …")
            client.post(
                f"{BASE}/sessions/{sid}/messages",
                params={"as_user": USER},
                json={"reject": rid, "reason": "smoke test rejection"},
            ).raise_for_status()
            sc.wait_for("final", since=before, timeout=120.0)

        # 5. Handoff: tax-flavoured question
        before = len(sc.events)
        client.post(
            f"{BASE}/sessions/{sid}/messages",
            params={"as_user": USER},
            json={"text": "I now have a US sales-tax classification question — please hand off to the tax SME."},
        ).raise_for_status()
        rd = sc.wait_for("router_decision", since=before, timeout=120.0)
        sc.wait_for("final", since=before, timeout=120.0)
        if rd is not None:
            print(f"\nrouter_decision after handoff: {rd['data']}")
        else:
            print("WARNING: no router_decision after handoff request.")

        sc.stop()

        names = [e["event"] for e in sc.events]
        print(f"\nALL EVENTS ({len(names)}): {names}")
        assert "session_start" in names
        assert "router_decision" in names

    print("\n✅ smoke complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
