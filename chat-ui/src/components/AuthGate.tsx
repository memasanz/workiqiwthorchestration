import { useEffect } from "react";
import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { InteractionStatus } from "@azure/msal-browser";
import { loginRequest } from "../auth/msal";

interface Props {
  children: React.ReactNode;
}

export function AuthGate({ children }: Props) {
  const isAuthed = useIsAuthenticated();
  const { instance, inProgress } = useMsal();

  useEffect(() => {
    if (!isAuthed && inProgress === InteractionStatus.None) {
      instance.loginRedirect(loginRequest).catch((e) => {
        // eslint-disable-next-line no-console
        console.error("loginRedirect failed", e);
      });
    }
  }, [isAuthed, inProgress, instance]);

  if (!isAuthed) {
    return (
      <div className="login-gate">
        <div className="login-card">
          <h1>Group Chat — Path A</h1>
          <p className="muted">Signing you in…</p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
