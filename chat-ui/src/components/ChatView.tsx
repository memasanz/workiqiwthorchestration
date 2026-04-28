import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import { useSession } from "../state/session";
import { AgentBadge, agentColor } from "./AgentBadge";
import { ToolCallExpando } from "./ToolCallExpando";

export function ChatView() {
  const { state } = useSession();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [state.transcript.length]);

  return (
    <div className="chat-view">
      {state.transcript.map((e) => {
        if (e.kind === "user") {
          return (
            <div key={e.id} className="msg msg-user">
              <AgentBadge agent="user" />
              <div className="bubble bubble-user">{e.text}</div>
            </div>
          );
        }
        if (e.kind === "agent_message") {
          const info = e.agent ? state.participants[e.agent] : undefined;
          return (
            <div key={e.id} className="msg msg-agent">
              <AgentBadge agent={e.agent || "agent"} />
              {info && (
                <span
                  className="participant-chip"
                  title={`registered Foundry agent: ${info.foundry_name} v${info.version}`}
                >
                  {info.foundry_name} v{info.version}
                </span>
              )}
              <div
                className="bubble"
                style={{ borderLeft: `3px solid ${agentColor(e.agent || "agent")}` }}
              >
                <ReactMarkdown>{e.text || ""}</ReactMarkdown>
              </div>
            </div>
          );
        }
        if (e.kind === "tool_proposed") {
          const p = e.payload || {};
          return (
            <div key={e.id} className="msg msg-tool">
              <ToolCallExpando
                title={`${e.agent} proposed ${p.tool}${
                  p.rationale ? ` — ${p.rationale}` : ""
                }`}
                body={p.args}
              />
            </div>
          );
        }
        if (e.kind === "tool_executed") {
          const p = e.payload || {};
          return (
            <div key={e.id} className="msg msg-tool">
              <ToolCallExpando
                title={`${e.agent} executed ${p.tool} ${p.ok ? "ok" : "FAILED"}`}
                body={p.result_summary ?? p}
                ok={p.ok}
              />
            </div>
          );
        }
        if (e.kind === "tool_rejected") {
          const p = e.payload || {};
          return (
            <div key={e.id} className="msg msg-tool">
              <ToolCallExpando
                title={`${e.agent} ${p.tool} rejected`}
                body={p}
                ok={false}
              />
            </div>
          );
        }
        if (e.kind === "router_decision") {
          return (
            <div key={e.id} className="msg msg-router">
              <span className="router-line">{e.text}</span>
            </div>
          );
        }
        if (e.kind === "error") {
          return (
            <div key={e.id} className="msg msg-error">
              <span className="error-line">error: {e.text}</span>
            </div>
          );
        }
        return (
          <div key={e.id} className="msg msg-system">
            <span className="system-line">{e.text}</span>
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}
