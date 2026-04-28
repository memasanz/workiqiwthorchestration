import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { msalInstance, apiScopes } from "../auth/msal";

async function acquireToken(): Promise<string> {
  const account = msalInstance.getAllAccounts()[0];
  if (!account) {
    throw new Error("No signed-in account");
  }
  try {
    const r = await msalInstance.acquireTokenSilent({ account, scopes: apiScopes });
    return r.accessToken;
  } catch (e) {
    if (e instanceof InteractionRequiredAuthError) {
      const r = await msalInstance.acquireTokenPopup({ scopes: apiScopes, account });
      return r.accessToken;
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
