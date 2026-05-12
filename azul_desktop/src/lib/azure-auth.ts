import { isTauri } from "@tauri-apps/api/core";

const AZURE_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default";
export const AZURE_ARM_SCOPE = "https://management.azure.com/.default";
const AZURE_KEY_VAULT_SCOPE = "https://vault.azure.net/.default";
const TAURI_AUTH_EVENT = "azure-auth-callback";

interface AzureAuthCallbackPayload {
  code?: string;
  error?: string;
  error_description?: string;
  state?: string;
}

export interface AzureLoginRequest {
  tenantId: string;
  clientId: string;
}

export interface AzureLoginResult {
  accessToken: string;
  expiresOn: number;
  scope: string;
}

function base64UrlEncode(bytes: ArrayBuffer | Uint8Array): string {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  data.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomVerifier(): string {
  const bytes = new Uint8Array(64);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

async function sha256(value: string): Promise<ArrayBuffer> {
  return crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
}

function sanitizeTenant(tenantId: string): string {
  return tenantId.trim() || "common";
}

function authBaseUrl(tenantId: string): string {
  return `https://login.microsoftonline.com/${encodeURIComponent(sanitizeTenant(tenantId))}/oauth2/v2.0`;
}

async function waitForAuthCode(popup: Window, expectedState: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      if (popup.closed) {
        window.clearInterval(timer);
        reject(new Error("Microsoft login was closed before it completed."));
        return;
      }
      if (Date.now() - startedAt > 120_000) {
        window.clearInterval(timer);
        popup.close();
        reject(new Error("Microsoft login timed out."));
        return;
      }

      try {
        const url = new URL(popup.location.href);
        if (url.origin !== window.location.origin) {
          return;
        }
        const error = url.searchParams.get("error");
        if (error) {
          window.clearInterval(timer);
          popup.close();
          reject(new Error(url.searchParams.get("error_description") || error));
          return;
        }
        const code = url.searchParams.get("code");
        const state = url.searchParams.get("state");
        if (code && state === expectedState) {
          window.clearInterval(timer);
          popup.close();
          resolve(code);
        }
      } catch {
        // Cross-origin while the Microsoft login page is still active.
      }
    }, 250);
  });
}

async function exchangeCodeForToken(
  request: AzureLoginRequest,
  code: string,
  verifier: string,
  redirectUri: string,
  scope: string,
): Promise<AzureLoginResult> {
  const body = new URLSearchParams({
    client_id: request.clientId.trim(),
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri,
    code_verifier: verifier,
    scope,
  });
  const response = await fetch(`${authBaseUrl(request.tenantId)}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const payload = await response.json() as {
    access_token?: string;
    expires_in?: number;
    error?: string;
    error_description?: string;
  };
  if (!response.ok || !payload.access_token) {
    const detail = payload.error_description || payload.error || `Token exchange failed (${response.status})`;
    if (scope.includes("vault.azure.net") && detail.includes("AADSTS650057")) {
      throw new Error(
        "The AzulClaw Desktop app registration is missing Azure Key Vault delegated permissions. " +
        "In Microsoft Entra ID, open App registrations > AzulClaw Desktop > API permissions, " +
        "add Azure Key Vault > Delegated permissions > user_impersonation, then grant consent and retry.",
      );
    }
    throw new Error(payload.error_description || payload.error || `Token exchange failed (${response.status})`);
  }
  return {
    accessToken: payload.access_token,
    expiresOn: Math.floor(Date.now() / 1000) + Number(payload.expires_in || 3600),
    scope,
  };
}

async function loginWithMicrosoftInBrowser(
  request: AzureLoginRequest,
  verifier: string,
  scope: string,
  prompt: "select_account" | "none" | "consent" | "login",
): Promise<AzureLoginResult> {
  const challenge = base64UrlEncode(await sha256(verifier));
  const state = randomVerifier();
  const redirectUri = `${window.location.origin}/azure-auth-callback`;
  const params = new URLSearchParams({
    client_id: request.clientId.trim(),
    response_type: "code",
    redirect_uri: redirectUri,
    response_mode: "query",
    scope: `openid profile ${scope}`,
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
    prompt,
  });
  const popup = window.open("", "azulclaw-azure-login", "popup=yes,width=520,height=720");
  if (!popup) {
    throw new Error("The browser blocked the Microsoft login popup.");
  }
  popup.focus();
  try {
    popup.location.href = `${authBaseUrl(request.tenantId)}/authorize?${params.toString()}`;
    const code = await waitForAuthCode(popup, state);
    return exchangeCodeForToken(request, code, verifier, redirectUri, scope);
  } catch (error) {
    popup.close();
    throw error;
  }
}

async function loginWithMicrosoftInTauri(
  request: AzureLoginRequest,
  verifier: string,
  scope: string,
  prompt: "select_account" | "none" | "consent" | "login",
): Promise<AzureLoginResult> {
  const [{ WebviewWindow }, { getCurrentWindow }] = await Promise.all([
    import("@tauri-apps/api/webviewWindow"),
    import("@tauri-apps/api/window"),
  ]);

  const challenge = base64UrlEncode(await sha256(verifier));
  const state = randomVerifier();
  const redirectUri = `${window.location.origin}/azure-auth-callback`;
  const params = new URLSearchParams({
    client_id: request.clientId.trim(),
    response_type: "code",
    redirect_uri: redirectUri,
    response_mode: "query",
    scope: `openid profile ${scope}`,
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
    prompt,
  });

  const loginWindow = new WebviewWindow(`azure-login-${crypto.randomUUID()}`, {
    url: `${authBaseUrl(request.tenantId)}/authorize?${params.toString()}`,
    title: "Microsoft sign-in",
    width: 520,
    height: 720,
    center: true,
    focus: true,
    resizable: true,
  });

  await new Promise<void>((resolve, reject) => {
    let settled = false;
    void loginWindow.once("tauri://created", () => {
      if (!settled) {
        settled = true;
        resolve();
      }
    });
    void loginWindow.once("tauri://error", (event) => {
      if (!settled) {
        settled = true;
        reject(new Error(String(event.payload || "Microsoft login window could not be created.")));
      }
    });
  });

  const currentWindow = getCurrentWindow();
  let settled = false;
  let timeoutId = 0;
  let unlistenEvent: (() => void) | undefined;
  let unlistenClose: (() => void) | undefined;
  let resolveCode: ((value: string) => void) | undefined;
  let rejectCode: ((reason?: Error) => void) | undefined;

  const cleanup = () => {
    if (timeoutId) {
      window.clearTimeout(timeoutId);
    }
    unlistenEvent?.();
    unlistenEvent = undefined;
    unlistenClose?.();
    unlistenClose = undefined;
    void loginWindow.close().catch(() => {});
  };

  const finishResolve = (value: string) => {
    if (settled) return;
    settled = true;
    cleanup();
    resolveCode?.(value);
  };

  const finishReject = (error: Error) => {
    if (settled) return;
    settled = true;
    cleanup();
    rejectCode?.(error);
  };

  const codePromise = new Promise<string>((resolve, reject) => {
    resolveCode = resolve;
    rejectCode = reject;
  });

  try {
    unlistenEvent = await currentWindow.listen<AzureAuthCallbackPayload>(TAURI_AUTH_EVENT, (event) => {
      const payload = event.payload || {};
      if (payload.state !== state) {
        return;
      }
      if (payload.error) {
        finishReject(new Error(payload.error_description || payload.error));
        return;
      }
      if (!payload.code) {
        finishReject(new Error("Microsoft login did not return an authorization code."));
        return;
      }
      finishResolve(payload.code);
    });
    unlistenClose = await loginWindow.onCloseRequested(() => {
      finishReject(new Error("Microsoft login was closed before it completed."));
    });
  } catch (error) {
    finishReject(error instanceof Error ? error : new Error(String(error)));
  }

  timeoutId = window.setTimeout(() => {
    finishReject(new Error("Microsoft login timed out."));
  }, 120_000);

  const code = await codePromise;

  return exchangeCodeForToken(request, code, verifier, redirectUri, scope);
}

export async function loginWithMicrosoft(
  request: AzureLoginRequest,
  {
    scope,
    prompt = "select_account",
  }: {
    scope: string;
    prompt?: "select_account" | "none" | "consent" | "login";
}): Promise<AzureLoginResult> {
  const clientId = request.clientId.trim();
  if (!clientId) {
    throw new Error("Azure application client ID is required.");
  }
  const verifier = randomVerifier();

  if (isTauri()) {
    return loginWithMicrosoftInTauri(request, verifier, scope, prompt);
  }
  return loginWithMicrosoftInBrowser(request, verifier, scope, prompt);
}

export async function loginWithMicrosoftForAzure(request: AzureLoginRequest): Promise<AzureLoginResult> {
  return loginWithMicrosoft(request, { scope: AZURE_OPENAI_SCOPE });
}

export async function loginWithMicrosoftForKeyVault(request: AzureLoginRequest): Promise<AzureLoginResult> {
  return loginWithMicrosoft(request, { scope: AZURE_KEY_VAULT_SCOPE });
}
