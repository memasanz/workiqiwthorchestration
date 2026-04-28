export type AgentName = "submissions" | "tax_sme" | "legal_sme" | "router" | string;

export interface Caller {
  email: string;
  oid?: string;
  name?: string;
}

export interface Assignment {
  // Loose typing — backend's MCP-loaded shape varies; we just render whatever's there.
  [k: string]: any;
  id?: string;
  projectId?: string;
  title?: string;
  status?: string;
  assignee?: string;
}

export interface SessionStartPayload {
  sessionId: string;
  caller: Caller;
  assignments: Assignment[];
}

export interface RouterDecisionPayload {
  turn: number;
  agents: AgentName[];
}

export interface AgentTurnStartPayload {
  turn: number;
  agent: AgentName;
}

export interface AgentMessagePayload {
  turn: number;
  agent: AgentName;
  text: string;
}

export interface ToolProposedPayload {
  turn: number;
  agent: AgentName;
  tool: string;
  args: Record<string, any>;
  rationale?: string;
}

export interface ApprovalRequiredPayload {
  turn: number;
  requestId: string;
  agent: AgentName;
  tool: string;
  args: Record<string, any>;
  prompt?: string;
}

export interface ToolExecutedPayload {
  turn: number;
  agent: AgentName;
  tool: string;
  ok: boolean;
  call_id?: string;
  result_summary?: any;
}

export interface ToolCallStartedPayload {
  turn: number;
  agent: AgentName;
  tool: string;
  call_id: string;
  args?: Record<string, any>;
}

export interface ToolRejectedPayload {
  turn: number;
  agent: AgentName;
  tool: string;
  reason?: string;
}

export interface FinalPayload {
  turn: number;
}

export interface ParticipantPayload {
  agent_id: AgentName;
  foundry_name: string;
  version: string;
}

export interface ErrorPayload {
  message: string;
}

export interface AgentThinkingPayload {
  turn: number;
  agent: AgentName;
  active: boolean;
}

export type SseEvent =
  | { type: "session_start"; data: SessionStartPayload }
  | { type: "router_decision"; data: RouterDecisionPayload }
  | { type: "agent_turn_start"; data: AgentTurnStartPayload }
  | { type: "agent_thinking"; data: AgentThinkingPayload }
  | { type: "agent_message"; data: AgentMessagePayload }
  | { type: "tool_proposed"; data: ToolProposedPayload }
  | { type: "approval_required"; data: ApprovalRequiredPayload }
  | { type: "tool_executed"; data: ToolExecutedPayload }
  | { type: "tool_call_started"; data: ToolCallStartedPayload }
  | { type: "tool_rejected"; data: ToolRejectedPayload }
  | { type: "participant"; data: ParticipantPayload }
  | { type: "final"; data: FinalPayload }
  | { type: "error"; data: ErrorPayload };

export interface ParticipantInfo {
  foundry_name: string;
  version: string;
}

export interface TranscriptEntry {
  id: string;
  kind:
    | "user"
    | "agent_message"
    | "tool_proposed"
    | "tool_pending"
    | "tool_executed"
    | "tool_rejected"
    | "router_decision"
    | "error"
    | "system";
  agent?: AgentName;
  turn?: number;
  text?: string;
  payload?: any;
}

export interface PendingApproval {
  requestId: string;
  agent: AgentName;
  tool: string;
  args: Record<string, any>;
  prompt?: string;
  turn: number;
}

export type ApprovalDecision =
  | { decision: "approve" }
  | { decision: "edit"; editedArgs: Record<string, any> }
  | { decision: "reject"; reason: string };
