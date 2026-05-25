import React from "react";
import ReactDOM from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";
import { useTranslation } from "react-i18next";

import { DesktopApp } from "./app/DesktopApp";
import "./lib/i18n";
import "./styles/global.css";

function AzureAuthCallbackBridge() {
  const { t } = useTranslation();

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
      await currentWindow.emitTo("main", "azure-auth-callback", payload);
      await currentWindow.close();
    })();
  }, []);

  return (
    <div className="onboarding-stage">
      <section className="onboarding-card">
        <p className="eyebrow">{t("auth.microsoftLogin")}</p>
        <h1>{t("auth.completingSignIn")}</h1>
        <p>{t("auth.returningSession")}</p>
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
