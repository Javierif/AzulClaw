import React from "react";
import ReactDOM from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";

import { DesktopApp } from "./app/DesktopApp";
import "./styles/global.css";

function AzureAuthCallbackBridge() {
  const [bridgeError, setBridgeError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!isTauri()) {
      return;
    }

    void (async () => {
      const params = new URLSearchParams(window.location.search);
      const payload = {
        code: params.get("code") ?? "",
        error: params.get("error") ?? "",
        error_description: params.get("error_description") ?? "",
        state: params.get("state") ?? "",
      };

      const { getCurrentWebviewWindow } = await import("@tauri-apps/api/webviewWindow");
      const currentWindow = getCurrentWebviewWindow();
      let emitted = false;
      try {
        await currentWindow.emitTo("main", "azure-auth-callback", payload);
        emitted = true;
      } catch (error) {
        setBridgeError(
          error instanceof Error ? error.message : "Could not return your Microsoft session to AzulClaw.",
        );
      } finally {
        if (emitted) {
          await currentWindow.close().catch(() => {});
        }
      }
    })();
  }, []);

  return (
    <div className="onboarding-stage">
      <section className="onboarding-card">
        <p className="eyebrow">Microsoft Login</p>
        {bridgeError ? (
          <>
            <h1>Sign-in could not complete</h1>
            <p>{bridgeError}</p>
            <p>You can close this window and try signing in again.</p>
          </>
        ) : (
          <>
            <h1>Completing sign-in</h1>
            <p>Returning your Microsoft session to AzulClaw.</p>
          </>
        )}
      </section>
    </div>
  );
}

const isAzureAuthCallback = window.location.pathname === "/azure-auth-callback";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isAzureAuthCallback ? <AzureAuthCallbackBridge /> : <DesktopApp />}
  </React.StrictMode>,
);
