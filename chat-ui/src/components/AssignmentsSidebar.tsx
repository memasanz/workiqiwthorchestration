import { useSession } from "../state/session";

export function AssignmentsSidebar() {
  const { state } = useSession();
  return (
    <aside className="sidebar">
      <h3>Caller</h3>
      <div className="caller-info">
        {state.caller ? (
          <>
            <div>{state.caller.email}</div>
            {state.sessionId && <div className="muted small">session {state.sessionId}</div>}
          </>
        ) : (
          <div className="muted">not signed in</div>
        )}
      </div>

      <h3>Open assignments ({state.assignments.length})</h3>
      {state.assignments.length === 0 && (
        <div className="muted">none — backend reported no open work for this caller.</div>
      )}
      <ul className="assignments">
        {state.assignments.map((a, i) => {
          const id = a.id || a.projectId || `#${i + 1}`;
          const title = a.title || a.name || a.summary || "(untitled)";
          const status = a.status || a.state || "";
          return (
            <li key={i} className="assignment">
              <div className="assignment-title">{title}</div>
              <div className="muted small">
                {id}
                {status ? ` · ${status}` : ""}
              </div>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
