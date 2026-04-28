import { fetchEventSource } from "@microsoft/fetch-event-source";
import type { ApprovalDecision, SessionStartPayload, SseEvent } from "./types";
import { authFetch, getAccessToken } from "./utils/authFetch";

// All fetches go through /api/* — proxied by Vite (dev) and nginx (prod) to the backend.
const API_BASE = "/api";

export async function createSession(): Promise<SessionStartPayload> {
  const r = await authFetch(`${API_BASE}/sessions`, { method: "POST" });
  if (!r.ok) throw new Error(`createSession failed: ${r.status} ${await r.text()}`);
  return r.json();
}

export async function postMessage(
  sessionId: string,
  text: string
): Promise<{ messageId: string }> {
  const r = await authFetch(`${API_BASE}/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!r.ok) throw new Error(`postMessage failed: ${r.status} ${await r.text()}`);
  return r.json();
}

export async function postApproval(
  sessionId: string,
  requestId: string,
  decision: ApprovalDecision
): Promise<void> {
  const body: Record<string, any> = { decision: decision.decision };
  if (decision.decision === "edit") body.editedArgs = decision.editedArgs;
  if (decision.decision === "reject") body.reason = decision.reason;
  const r = await authFetch(
    `${API_BASE}/sessions/${sessionId}/approvals/${requestId}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }
  );
  if (!r.ok) throw new Error(`postApproval failed: ${r.status} ${await r.text()}`);
}

const SSE_EVENT_TYPES = [
  "session_start",
  "router_decision",
  "agent_turn_start",
  "agent_thinking",
  "agent_message",
  "tool_proposed",
  "tool_call_started",
  "approval_required",
  "tool_executed",
  "tool_rejected",
  "participant",
  "final",
  "error",
] as const;

export interface OpenStreamHandlers {
  onEvent: (ev: SseEvent) => void;
  onOpen?: () => void;
  onError?: (err: any) => void;
  signal: AbortSignal;
}

export async function openStream(
  sessionId: string,
  { onEvent, onOpen, onError, signal }: OpenStreamHandlers
): Promise<void> {
  const token = await getAccessToken();
  const url = `${API_BASE}/sessions/${sessionId}/stream`;
  await fetchEventSource(url, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
    signal,
    openWhenHidden: true,
    onopen: async (resp) => {
      if (resp.ok && (resp.headers.get("content-type") || "").includes("text/event-stream")) {
        onOpen?.();
        return;
      }
      throw new Error(`SSE open failed: ${resp.status} ${resp.statusText}`);
    },
    onmessage: (msg) => {
      const t = msg.event || "message";
      try {
        const data = JSON.parse(msg.data);
        if ((SSE_EVENT_TYPES as readonly string[]).includes(t)) {
          onEvent({ type: t as SseEvent["type"], data } as SseEvent);
        } else if (data && typeof data === "object" && data.event && data.payload) {
          onEvent({ type: data.event, data: data.payload } as SseEvent);
        }
      } catch {
        /* ignore non-JSON frames */
      }
    },
    onerror: (err) => {
      onError?.(err);
      // Returning will let fetch-event-source retry with backoff. Throwing
      // would abort the stream entirely — we want to keep retrying.
    },
  });
}

