import { PublicClientApplication, LogLevel } from "@azure/msal-browser";

const tenantId = import.meta.env.VITE_TENANT_ID as string;
const spaClientId = import.meta.env.VITE_SPA_CLIENT_ID as string;
const apiClientId = import.meta.env.VITE_API_CLIENT_ID as string;

if (!tenantId || !spaClientId || !apiClientId) {
  // Surfaced via the AuthGate; we don't throw at module-load to keep
  // Vite's HMR happy on first run.
  // eslint-disable-next-line no-console
  console.error(
    "Missing one of VITE_TENANT_ID / VITE_SPA_CLIENT_ID / VITE_API_CLIENT_ID — auth will fail"
  );
}

export const apiScopes = [`api://${apiClientId}/Chat.ReadWrite`];

export const msalInstance = new PublicClientApplication({
  auth: {
    clientId: spaClientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: typeof window !== "undefined" ? window.location.origin : "/",
    postLogoutRedirectUri: typeof window !== "undefined" ? window.location.origin : "/",
    navigateToLoginRequestUrl: true,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
  system: {
    loggerOptions: {
      logLevel: LogLevel.Warning,
      loggerCallback: (_lvl, msg) => {
        // eslint-disable-next-line no-console
        console.debug("[msal]", msg);
      },
      piiLoggingEnabled: false,
    },
  },
});

export const loginRequest = { scopes: apiScopes };
