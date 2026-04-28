import { useEffect, useReducer, useRef, useState } from "react";
import { useAccount, useMsal } from "@azure/msal-react";
import { ChatView } from "./components/ChatView";
import { Composer } from "./components/Composer";
import { ApprovalModal } from "./components/ApprovalModal";
import { AssignmentsSidebar } from "./components/AssignmentsSidebar";
import { AuthGate } from "./components/AuthGate";
import { createSession, openStream } from "./api";
import {
  SessionCtx,
  initialSessionState,
  reducer,
} from "./state/session";

export default function App() {
  return (
    <AuthGate>
      <AppInner />
    </AuthGate>
  );
}

function AppInner() {
  const { instance, accounts } = useMsal();
  const account = useAccount(accounts[0] || null);
  const [state, dispatch] = useReducer(reducer, initialSessionState);
  const [bootError, setBootError] = useState<string | null>(null);
  const [booting, setBooting] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const startedRef = useRef(false);

  async function startSession() {
    if (booting || state.sessionId) return;
    setBooting(true);
    setBootError(null);
    try {
      const resp = await createSession();
      dispatch({
        type: "session_created",
        sessionId: resp.sessionId,
        caller: resp.caller,
        assignments: resp.assignments,
      });
    } catch (e: any) {
      setBootError(String(e?.message || e));
    } finally {
      setBooting(false);
    }
  }

  // Auto-start a session once the user is authenticated (replaces the old
  // email-entry login screen — identity comes from MSAL now).
  useEffect(() => {
    if (account && !state.sessionId && !startedRef.current) {
      startedRef.current = true;
      void startSession();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account, state.sessionId]);

  // Open SSE when we have a session
  useEffect(() => {
    if (!state.sessionId) return;
    const ac = new AbortController();
    abortRef.current = ac;
    let attempt = 0;

    dispatch({ type: "connection", state: "connecting", attempt: 0 });

    openStream(state.sessionId, {
      signal: ac.signal,
      onOpen: () => {
        attempt = 0;
        dispatch({ type: "connection", state: "open", attempt: 0 });
      },
      onEvent: (ev) => {
        dispatch({ type: "sse_event", ev });
      },
      onError: () => {
        attempt += 1;
        dispatch({ type: "connection", state: "reconnecting", attempt });
      },
    }).catch(() => {
      /* aborted on unmount */
    });

    return () => {
      ac.abort();
      abortRef.current = null;
    };
  }, [state.sessionId]);

  if (!state.sessionId) {
    return (
      <div className="login-gate">
        <div className="login-card">
          <h1>Group Chat — Path A</h1>
          <p className="muted">
            {booting ? "Starting your session…" : "Preparing session…"}
          </p>
          {bootError && <div className="modal-error">{bootError}</div>}
        </div>
      </div>
    );
  }

  return (
    <SessionCtx.Provider value={{ state, dispatch }}>
      <div className="app">
        <header className="app-header">
          <div className="app-title">Group Chat — Path A</div>
          <div className="conn-status">
            <span className={`dot dot-${state.connection}`} />
            {state.connection}
            {state.connection === "reconnecting" && state.reconnectAttempt > 0
              ? ` (attempt ${state.reconnectAttempt})`
              : ""}
            <span className="user-chip" title={account?.username || ""}>
              👤 {account?.name || account?.username || "user"}
            </span>
            <button
              className="logout"
              onClick={() => {
                abortRef.current?.abort();
                instance.logoutRedirect({ postLogoutRedirectUri: window.location.origin });
              }}
            >
              sign out
            </button>
          </div>
        </header>
        <div className="app-body">
          <AssignmentsSidebar />
          <main className="main-pane">
            <ChatView />
            <Composer />
          </main>
        </div>
        <ApprovalModal />
      </div>
    </SessionCtx.Provider>
  );
}

