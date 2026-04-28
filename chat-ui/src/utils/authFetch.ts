import {
  BrowserAuthError,
  InteractionRequiredAuthError,
} from "@azure/msal-browser";
import { msalInstance, apiScopes } from "../auth/msal";

const SILENT_FALLBACK_CODES = new Set([
  "monitor_window_timeout",
  "block_iframe_reload",
  "iframe_closed_prematurely",
  "silent_sso_error",
  "no_account_error",
  "user_login_error",
]);

function shouldFallbackToInteractive(e: unknown): boolean {
  if (e instanceof InteractionRequiredAuthError) return true;
  if (e instanceof BrowserAuthError) return SILENT_FALLBACK_CODES.has(e.errorCode);
  return false;
}

async function acquireToken(): Promise<string> {
  const account = msalInstance.getAllAccounts()[0];
  if (!account) {
    throw new Error("No signed-in account");
  }
  try {
    const r = await msalInstance.acquireTokenSilent({ account, scopes: apiScopes });
    return r.accessToken;
  } catch (e) {
    if (shouldFallbackToInteractive(e)) {
      try {
        const r = await msalInstance.acquireTokenPopup({ scopes: apiScopes, account });
        return r.accessToken;
      } catch (popupErr) {
        // Popup blockers / standalone PWAs: fall all the way back to a
        // full-page redirect so the user always has SOME way to sign in.
        await msalInstance.acquireTokenRedirect({ scopes: apiScopes, account });
        // acquireTokenRedirect navigates away; this throw is unreachable
        // in practice but keeps TS happy.
        throw popupErr;
      }
    }
    throw e;
  }
}

export async function authFetch(input: RequestInfo, init: RequestInit = {}): Promise<Response> {
  const token = await acquireToken();
  const headers = new Headers(init.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(input, { ...init, headers });
}

export async function getAccessToken(): Promise<string> {
  return acquireToken();
}
