import { createContext, useContext } from "react";
import type {
  Assignment,
  Caller,
  ParticipantInfo,
  PendingApproval,
  SseEvent,
  TranscriptEntry,
} from "../types";

export interface SessionState {
  caller: Caller | null;
  sessionId: string | null;
  assignments: Assignment[];
  transcript: TranscriptEntry[];
  pendingApprovals: PendingApproval[];
  participants: Record<string, ParticipantInfo>;
  turnInFlight: boolean;
  connection: "idle" | "connecting" | "open" | "reconnecting";
  reconnectAttempt: number;
}

export const initialSessionState: SessionState = {
  caller: null,
  sessionId: null,
  assignments: [],
  transcript: [],
  pendingApprovals: [],
  participants: {},
  turnInFlight: false,
  connection: "idle",
  reconnectAttempt: 0,
};

export type Action =
  | { type: "set_caller"; caller: Caller }
  | { type: "logout" }
  | { type: "session_created"; sessionId: string; caller: Caller; assignments: Assignment[] }
  | { type: "user_sent"; text: string }
  | { type: "sse_event"; ev: SseEvent }
  | { type: "approval_resolved"; requestId: string }
  | { type: "connection"; state: SessionState["connection"]; attempt?: number };

let nextId = 0;
const eid = () => `e${++nextId}`;

export function reducer(state: SessionState, action: Action): SessionState {
  switch (action.type) {
    case "set_caller":
      return { ...state, caller: action.caller };
    case "logout":
      return { ...initialSessionState };
    case "session_created":
      return {
        ...state,
        sessionId: action.sessionId,
        caller: action.caller,
        assignments: action.assignments,
        transcript: [
          {
            id: eid(),
            kind: "system",
            text: `Session started for ${action.caller.email}.`,
          },
        ],
      };
    case "user_sent":
      return {
        ...state,
        turnInFlight: true,
        transcript: [
          ...state.transcript,
          { id: eid(), kind: "user", text: action.text },
        ],
      };
    case "approval_resolved":
      return {
        ...state,
        pendingApprovals: state.pendingApprovals.filter(
          (p) => p.requestId !== action.requestId
        ),
      };
    case "connection":
      return {
        ...state,
        connection: action.state,
        reconnectAttempt: action.attempt ?? state.reconnectAttempt,
      };
    case "sse_event": {
      const ev = action.ev;
      switch (ev.type) {
        case "session_start":
          // already applied via session_created; but if a re-subscribe replays it,
          // refresh assignments.
          return { ...state, assignments: ev.data.assignments };
        case "router_decision":
          return {
            ...state,
            transcript: [
              ...state.transcript,
              {
                id: eid(),
                kind: "router_decision",
                turn: ev.data.turn,
                text: `router → ${ev.data.agents.join(", ")}`,
                payload: ev.data,
              },
            ],
          };
        case "agent_turn_start":
          return state; // soft event; bubbles created on agent_message
        case "agent_message": {
          const cleaned = state.transcript.filter(
            (t) => t.kind !== "tool_pending",
          );
          return {
            ...state,
            transcript: [
              ...cleaned,
              {
                id: eid(),
                kind: "agent_message",
                turn: ev.data.turn,
                agent: ev.data.agent,
                text: ev.data.text,
              },
            ],
          };
        }
        case "tool_proposed":
          return {
            ...state,
            transcript: [
              ...state.transcript,
              {
                id: eid(),
                kind: "tool_proposed",
                turn: ev.data.turn,
                agent: ev.data.agent,
                payload: ev.data,
              },
            ],
          };
        case "approval_required":
          return {
            ...state,
            pendingApprovals: [
              ...state.pendingApprovals,
              {
                requestId: ev.data.requestId,
                agent: ev.data.agent,
                tool: ev.data.tool,
                args: ev.data.args,
                prompt: ev.data.prompt,
                turn: ev.data.turn,
              },
            ],
          };
        case "tool_call_started":
          return {
            ...state,
            transcript: [
              ...state.transcript,
              {
                id: eid(),
                kind: "tool_pending",
                turn: ev.data.turn,
                agent: ev.data.agent,
                payload: ev.data,
              },
            ],
          };
        case "agent_thinking": {
          const sentinel = `__thinking__:${ev.data.agent}`;
          const without = state.transcript.filter(
            (t) => !(t.kind === "tool_pending" && t.payload?.call_id === sentinel),
          );
          if (!ev.data.active) {
            return { ...state, transcript: without };
          }
          return {
            ...state,
            transcript: [
              ...without,
              {
                id: eid(),
                kind: "tool_pending",
                turn: ev.data.turn,
                agent: ev.data.agent,
                payload: { call_id: sentinel, tool: "thinking", thinking: true },
              },
            ],
          };
        }
        case "tool_executed": {
          const cid = (ev.data as any).call_id;
          const filtered = state.transcript.filter((t) => {
            if (t.kind !== "tool_pending") return true;
            if (t.payload?.thinking) return false;
            if (cid && t.payload?.call_id === cid) return false;
            return true;
          });
          return {
            ...state,
            transcript: [
              ...filtered,
              {
                id: eid(),
                kind: "tool_executed",
                turn: ev.data.turn,
                agent: ev.data.agent,
                payload: ev.data,
              },
            ],
          };
        }
        case "tool_rejected":
          return {
            ...state,
            transcript: [
              ...state.transcript,
              {
                id: eid(),
                kind: "tool_rejected",
                turn: ev.data.turn,
                agent: ev.data.agent,
                payload: ev.data,
                text: ev.data.reason || "rejected",
              },
            ],
          };
        case "participant":
          return {
            ...state,
            participants: {
              ...state.participants,
              [ev.data.agent_id]: {
                foundry_name: ev.data.foundry_name,
                version: ev.data.version,
              },
            },
          };
        case "final":
          return {
            ...state,
            turnInFlight: false,
            transcript: state.transcript.filter((t) => t.kind !== "tool_pending"),
          };
        case "error":
          return {
            ...state,
            turnInFlight: false,
            transcript: [
              ...state.transcript,
              { id: eid(), kind: "error", text: ev.data.message },
            ],
          };
      }
      return state;
    }
  }
}

export const SessionCtx = createContext<{
  state: SessionState;
  dispatch: React.Dispatch<Action>;
} | null>(null);

export function useSession() {
  const v = useContext(SessionCtx);
  if (!v) throw new Error("SessionCtx not provided");
  return v;
}
