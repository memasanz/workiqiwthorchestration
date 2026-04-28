import React from "react";
import ReactDOM from "react-dom/client";
import { MsalProvider } from "@azure/msal-react";
import { EventType } from "@azure/msal-browser";
import App from "./App";
import { msalInstance } from "./auth/msal";
import "./styles.css";

// Process the redirect first so the active account is set before <App/> mounts.
msalInstance.initialize().then(() => {
  msalInstance.handleRedirectPromise().catch((e) => {
    // eslint-disable-next-line no-console
    console.error("MSAL redirect handling failed", e);
  });

  if (!msalInstance.getActiveAccount()) {
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length > 0) msalInstance.setActiveAccount(accounts[0]);
  }

  msalInstance.addEventCallback((ev) => {
    if (
      (ev.eventType === EventType.LOGIN_SUCCESS ||
        ev.eventType === EventType.ACQUIRE_TOKEN_SUCCESS) &&
      (ev.payload as any)?.account
    ) {
      msalInstance.setActiveAccount((ev.payload as any).account);
    }
  });

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <MsalProvider instance={msalInstance}>
        <App />
      </MsalProvider>
    </React.StrictMode>
  );
});

