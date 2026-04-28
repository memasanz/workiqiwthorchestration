import { useState } from "react";

interface Props {
  initial?: string;
  onSubmit: (email: string) => void;
  busy?: boolean;
  error?: string | null;
}

export function LoginGate({ initial, onSubmit, busy, error }: Props) {
  const [email, setEmail] = useState(initial || "");
  return (
    <div className="login-gate">
      <div className="login-card">
        <h1>Group Chat — Path A</h1>
        <p className="muted">
          DEV_BYPASS_AUTH is enabled on the backend. Enter the email you want to act
          as. (Stored locally; no real auth.)
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const v = email.trim();
            if (v) onSubmit(v);
          }}
        >
          <input
            type="email"
            placeholder="you@contoso.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
            required
          />
          <button type="submit" disabled={busy || !email.trim()}>
            {busy ? "Starting session…" : "Start session"}
          </button>
        </form>
        {error && <div className="modal-error">{error}</div>}
      </div>
    </div>
  );
}
