import { useState } from "react";
import { useSession } from "../state/session";
import { postMessage } from "../api";

export function Composer() {
  const { state, dispatch } = useSession();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const disabled =
    !state.sessionId || !state.caller || state.turnInFlight || busy || !text.trim();

  async function send() {
    if (disabled) return;
    const t = text.trim();
    setBusy(true);
    try {
      dispatch({ type: "user_sent", text: t });
      await postMessage(state.sessionId!, t);
      setText("");
    } catch (e: any) {
      dispatch({
        type: "sse_event",
        ev: { type: "error", data: { message: String(e?.message || e) } },
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="composer">
      <textarea
        value={text}
        placeholder={
          state.turnInFlight
            ? "Waiting for current turn to finish…"
            : "Type a message. Shift+Enter for newline."
        }
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void send();
          }
        }}
        rows={3}
        disabled={state.turnInFlight}
      />
      <button onClick={send} disabled={disabled}>
        {state.turnInFlight ? "…" : "Send"}
      </button>
    </div>
  );
}
