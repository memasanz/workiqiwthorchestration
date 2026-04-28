import type { AgentName } from "../types";

const COLORS: Record<string, string> = {
  submissions: "#5b8def",
  tax_sme: "#ec8a3a",
  legal_sme: "#7d5fff",
  router: "#888",
  user: "#2a9d4a",
  system: "#888",
};

export function agentColor(agent: string): string {
  return COLORS[agent] || "#444";
}

export function AgentBadge({ agent }: { agent: AgentName }) {
  return (
    <span className="agent-badge" style={{ background: agentColor(agent) }}>
      {agent}
    </span>
  );
}
