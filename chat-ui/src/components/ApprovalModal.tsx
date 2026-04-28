import { useState } from "react";
import { useSession } from "../state/session";
import { postApproval } from "../api";
import type { PendingApproval } from "../types";

export function ApprovalModal() {
  const { state, dispatch } = useSession();
  const pending: PendingApproval | undefined = state.pendingApprovals[0];
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [editArgs, setEditArgs] = useState<string>("");
  const [reason, setReason] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!pending) return null;

  async function send(decision: "approve" | "edit" | "reject") {
    if (!state.sessionId || !state.caller || !pending) return;
    setBusy(true);
    setError(null);
    try {
      if (decision === "approve") {
        await postApproval(state.sessionId, pending.requestId, {
          decision: "approve",
        });
      } else if (decision === "edit") {
        let parsed: any;
        try {
          parsed = JSON.parse(editArgs);
        } catch (e) {
          setError("Edited args must be valid JSON.");
          setBusy(false);
          return;
        }
        await postApproval(state.sessionId, pending.requestId, {
          decision: "edit",
          editedArgs: parsed,
        });
      } else {
        await postApproval(state.sessionId, pending.requestId, {
          decision: "reject",
          reason: reason || "no reason given",
        });
      }
      dispatch({ type: "approval_resolved", requestId: pending.requestId });
      setMode("view");
      setEditArgs("");
      setReason("");
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  function startEdit() {
    setEditArgs(JSON.stringify(pending!.args, null, 2));
    setMode("edit");
  }

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h3>Approval required</h3>
        <div className="modal-meta">
          <div>
            <strong>agent:</strong> {pending.agent}
          </div>
          <div>
            <strong>tool:</strong> <code>{pending.tool}</code>
          </div>
          <div>
            <strong>turn:</strong> {pending.turn}
          </div>
          {pending.prompt && (
            <div>
              <strong>prompt:</strong> {pending.prompt}
            </div>
          )}
        </div>

        {mode === "view" && (
          <pre className="modal-args">{JSON.stringify(pending.args, null, 2)}</pre>
        )}
        {mode === "edit" && (
          <textarea
            className="modal-edit"
            rows={12}
            value={editArgs}
            onChange={(e) => setEditArgs(e.target.value)}
          />
        )}
        {mode === "reject" && (
          <textarea
            className="modal-edit"
            rows={4}
            placeholder="Reason for rejection"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        )}

        {error && <div className="modal-error">{error}</div>}

        <div className="modal-actions">
          {mode === "view" && (
            <>
              <button
                className="btn-approve"
                disabled={busy}
                onClick={() => send("approve")}
              >
                Approve
              </button>
              <button disabled={busy} onClick={startEdit}>
                Edit
              </button>
              <button
                className="btn-reject"
                disabled={busy}
                onClick={() => setMode("reject")}
              >
                Reject
              </button>
            </>
          )}
          {mode === "edit" && (
            <>
              <button
                className="btn-approve"
                disabled={busy}
                onClick={() => send("edit")}
              >
                Approve edited args
              </button>
              <button disabled={busy} onClick={() => setMode("view")}>
                Cancel
              </button>
            </>
          )}
          {mode === "reject" && (
            <>
              <button
                className="btn-reject"
                disabled={busy}
                onClick={() => send("reject")}
              >
                Confirm reject
              </button>
              <button disabled={busy} onClick={() => setMode("view")}>
                Cancel
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
