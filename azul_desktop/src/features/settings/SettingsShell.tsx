import { useEffect, useState } from "react";

import { SectionTopbarPortal } from "../../components/SectionTopbarPortal";
import {
  DATA_WIPE_CONFIRM_PHRASE,
  ensureBackendAuth,
  loadBackendStatus,
  loadSetupProfile,
  loadMemorySettings,
  loadRuntime,
  resetLocalSetupData,
  saveMemorySettings,
} from "../../lib/api";
import { profileCanRenewAzureLogin, renewAzureLoginFromProfile } from "../../lib/azure-session";
import {
  defaultDesktopShellPreferences,
  loadDesktopShellPreferences,
  saveDesktopShellPreferences,
} from "../../lib/desktop-shell";
import { SetupWizardShell } from "../hatching/HatchingShell";
import type {
  BackendStatus,
  DesktopShellPreferences,
  MemorySettings,
  RuntimeOverview,
  SetupProfile,
} from "../../lib/contracts";
import { defaultSetupProfile, memorySettings as defaultMemorySettings, runtimeOverview } from "../../lib/mock-data";

interface SettingsShellProps {
  headerPortalTarget?: HTMLElement | null;
  onLocalDataWiped?: (profile: SetupProfile) => void;
}

type SettingsTab = "azure" | "desktop" | "runtime" | "memory" | "identity" | "security" | "data";

const initialBackendStatus: BackendStatus = {
  status: "offline",
  api_base: "http://localhost:3978",
  runtime_dir: "",
  log_dir: "",
  models_total: 0,
  models_enabled: 0,
  scheduler_running: false,
  auth: {
    mode: "entra",
    startup_enabled: true,
    status: "failed",
    detail: "Backend unreachable",
    last_error: "",
    last_success_at: "",
  },
  logs: [],
};

const SETTINGS_TABS: Array<{ id: SettingsTab; label: string }> = [
  { id: "azure", label: "Azure" },
  { id: "desktop", label: "Desktop" },
  { id: "runtime", label: "Runtime" },
  { id: "memory", label: "Memory" },
  { id: "identity", label: "Identity" },
  { id: "security", label: "Workspace & Security" },
  { id: "data", label: "Data" },
];

export function SettingsShell({ headerPortalTarget = null, onLocalDataWiped }: SettingsShellProps) {
  const [runtime, setRuntime] = useState<RuntimeOverview>(runtimeOverview);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>(initialBackendStatus);
  const [isSaving, setIsSaving] = useState(false);
  const [wipeModalOpen, setWipeModalOpen] = useState(false);
  const [wipePhrase, setWipePhrase] = useState("");
  const [wipeBusy, setWipeBusy] = useState(false);
  const [wipeError, setWipeError] = useState("");
  const [wipeCopied, setWipeCopied] = useState(false);
  const [logsCopied, setLogsCopied] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");
  const [profile, setProfile] = useState<SetupProfile>(defaultSetupProfile);
  const [memorySettings, setMemorySettings] = useState<MemorySettings>(defaultMemorySettings);
  const [memoryPathDraft, setMemoryPathDraft] = useState("");
  const [memorySettingsBusy, setMemorySettingsBusy] = useState(false);
  const [memorySettingsMessage, setMemorySettingsMessage] = useState("");
  const [memorySettingsError, setMemorySettingsError] = useState("");
  const [desktopShellPreferences, setDesktopShellPreferences] = useState<DesktopShellPreferences>(defaultDesktopShellPreferences);
  const [desktopShellBusy, setDesktopShellBusy] = useState(false);
  const [desktopShellMessage, setDesktopShellMessage] = useState("");
  const [desktopShellError, setDesktopShellError] = useState("");
  const [azureWizardOpen, setAzureWizardOpen] = useState(false);
  const [settingsWizardStep, setSettingsWizardStep] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<SettingsTab>("azure");

  const azureConfig = profile.skill_configs?.Azure ?? {};
  const azureAuthMethod = azureConfig.authMethod === "api_key" ? "api_key" : "entra";
  const fastModel = runtime.models.find((model) => model.lane === "fast");
  const slowModel = runtime.models.find((model) => model.lane === "slow");
  const providerLabel = runtime.models.length > 0
    ? Array.from(new Set(runtime.models.map((model) => model.provider))).join(", ")
    : azureConfig.endpoint
      ? "azure"
      : "Not configured";
  const headerContent = (
    <div className="section-topbar">
      <div className="section-topbar-copy">
        <p className="eyebrow">Settings</p>
        <h2 className="section-topbar-title">Preferences and configuration</h2>
      </div>
      <div className="section-topbar-actions filter-row">
        <span className="status-pill">{runtime.default_lane}</span>
        <span className="status-pill">{runtime.models.length} models</span>
      </div>
    </div>
  );

  async function refreshSettings() {
    const [runtimeData, backendData, profileData, memoryData, shellData] = await Promise.all([
      loadRuntime(),
      loadBackendStatus(),
      loadSetupProfile(),
      loadMemorySettings(),
      loadDesktopShellPreferences(),
    ]);
    setRuntime(runtimeData);
    setBackendStatus(backendData);
    setProfile(profileData);
    setMemorySettings(memoryData);
    setMemoryPathDraft(memoryData.memory_db_path_override || "");
    setDesktopShellPreferences(shellData);
  }

  useEffect(() => {
    let isMounted = true;

    async function refreshMountedSettings() {
      const [runtimeData, backendData, profileData, memoryData, shellData] = await Promise.all([
        loadRuntime(),
        loadBackendStatus(),
        loadSetupProfile(),
        loadMemorySettings(),
        loadDesktopShellPreferences(),
      ]);
      if (!isMounted) {
        return;
      }
      setRuntime(runtimeData);
      setBackendStatus(backendData);
      setProfile(profileData);
      setMemorySettings(memoryData);
      setMemoryPathDraft((current) => current || memoryData.memory_db_path_override || "");
      setDesktopShellPreferences(shellData);
    }

    void refreshMountedSettings();
    const pollId = window.setInterval(() => {
      void refreshMountedSettings();
    }, 10000);

    return () => {
      isMounted = false;
      window.clearInterval(pollId);
    };
  }, []);

  function handleCopyPhrase() {
    void navigator.clipboard.writeText(DATA_WIPE_CONFIRM_PHRASE);
    setWipeCopied(true);
    setTimeout(() => setWipeCopied(false), 2000);
  }

  async function handleEnsureAuth() {
    setAuthBusy(true);
    setAuthError("");
    try {
      const auth = await ensureBackendAuth();
      if (auth.requires_frontend_login && profileCanRenewAzureLogin(profile)) {
        await renewAzureLoginFromProfile(profile);
      }
      const backendData = await loadBackendStatus();
      setBackendStatus(backendData);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : String(error));
    } finally {
      setAuthBusy(false);
    }
  }

  async function handleSaveMemorySettings() {
    setMemorySettingsBusy(true);
    setMemorySettingsMessage("");
    setMemorySettingsError("");
    try {
      const saved = await saveMemorySettings({
        memory_db_path_override: memoryPathDraft.trim(),
        vector_memory_enabled: memorySettings.vector_memory_enabled,
      });
      setMemorySettings(saved);
      setMemoryPathDraft(saved.memory_db_path_override || "");
      setMemorySettingsMessage(saved.reload_error ? `Saved, but reload failed: ${saved.reload_error}` : "Memory settings saved.");
    } catch (error) {
      setMemorySettingsError(error instanceof Error ? error.message : String(error));
    } finally {
      setMemorySettingsBusy(false);
    }
  }

  function handleResetMemoryPath() {
    setMemoryPathDraft("");
    setMemorySettingsMessage("");
    setMemorySettingsError("");
  }

  async function handleSaveDesktopShellPreferences() {
    setDesktopShellBusy(true);
    setDesktopShellMessage("");
    setDesktopShellError("");
    try {
      const saved = await saveDesktopShellPreferences(desktopShellPreferences);
      setDesktopShellPreferences(saved);
      setDesktopShellMessage("Desktop access settings saved.");
    } catch (error) {
      setDesktopShellError(error instanceof Error ? error.message : String(error));
    } finally {
      setDesktopShellBusy(false);
    }
  }

  function handleCopyLogs() {
    const text = backendStatus.logs
      .map((log) => `--- ${log.name} (${log.path}) ---\n${log.content || "<empty>"}`)
      .join("\n\n");
    void navigator.clipboard.writeText(text || "No backend logs available.");
    setLogsCopied(true);
    setTimeout(() => setLogsCopied(false), 2000);
  }

  function openWipeModal() {
    setWipePhrase("");
    setWipeError("");
    setWipeModalOpen(true);
  }

  function closeWipeModal() {
    if (wipeBusy) return;
    setWipeModalOpen(false);
    setWipePhrase("");
    setWipeError("");
  }

  async function handleWipeLocalData() {
    setWipeError("");
    if (wipePhrase.trim() !== DATA_WIPE_CONFIRM_PHRASE) {
      setWipeError(`Type exactly: ${DATA_WIPE_CONFIRM_PHRASE}`);
      return;
    }
    setWipeBusy(true);
    try {
      const result = await resetLocalSetupData(wipePhrase.trim());
      setWipeModalOpen(false);
      setWipePhrase("");
      onLocalDataWiped?.(result);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setWipeError(detail || "Wipe failed.");
    } finally {
      setWipeBusy(false);
    }
  }

  function openAzureWizard() {
    setSettingsWizardStep(null);
    setAzureWizardOpen(true);
    setActiveTab("azure");
  }

  function renderAzureTab() {
    return (
      <section className="subcard settings-card panel-tab-panel">
        <div className="settings-card-header">
          <div className="settings-card-icon settings-icon-models">
            <span>AZ</span>
          </div>
          <div>
            <p className="eyebrow">Azure</p>
            <h3 className="settings-card-title">Azure OpenAI connection</h3>
          </div>
        </div>

        <p className="settings-card-desc">
          Re-run the same Azure connection flow used during onboarding: sign in, discover resources, choose deployments and authorize Azure OpenAI access.
        </p>

        <div className="runtime-kv-list">
          <p className="runtime-kv-section-title">Azure OpenAI access</p>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Status</span>
            <span className={`status-tag ${profile.skill_configs?.Azure?.connected === "true" ? "status-done" : "status-waiting"}`}>
              {profile.skill_configs?.Azure?.connected === "true"
                ? azureAuthMethod === "api_key"
                  ? "Connected with API key"
                  : "Connected"
                : "Not connected"}
            </span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Connection mode</span>
            <span className="runtime-kv-val" style={{ display: "inline-flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
              <span>{azureAuthMethod === "api_key" ? "API key" : "Microsoft Entra"}</span>
              <span className={`hw-choice-badge ${azureAuthMethod === "api_key" ? "hw-choice-badge-fallback" : "hw-choice-badge-recommended"}`}>
                {azureAuthMethod === "api_key" ? "Fallback" : "Recommended"}
              </span>
            </span>
          </div>
          {profile.skill_configs?.Azure?.endpoint && (
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Endpoint</span>
              <code className="runtime-kv-code">{profile.skill_configs.Azure.endpoint}</code>
            </div>
          )}
          {profile.skill_configs?.Azure?.deployment && (
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Main deployment</span>
              <code className="runtime-kv-code">{profile.skill_configs.Azure.deployment}</code>
            </div>
          )}
          {profile.skill_configs?.Azure?.fastDeployment && (
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Fast deployment</span>
              <code className="runtime-kv-code">{profile.skill_configs.Azure.fastDeployment}</code>
            </div>
          )}
          {profile.skill_configs?.Azure?.embeddingDeployment && (
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Embedding deployment</span>
              <code className="runtime-kv-code">{profile.skill_configs.Azure.embeddingDeployment}</code>
            </div>
          )}
          {profile.skill_configs?.Azure?.lastConnectedAt && (
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">{profile.skill_configs?.Azure?.authMethod === "api_key" ? "Last connected" : "Last authorized"}</span>
              <code className="runtime-kv-code">{new Date(profile.skill_configs.Azure.lastConnectedAt).toLocaleString()}</code>
            </div>
          )}
        </div>

        {profile.skill_configs?.Azure?.authMethod !== "api_key" && (
          <div className="runtime-kv-list">
            <p className="runtime-kv-section-title">Microsoft runtime and channels</p>
            {profile.skill_configs?.Azure?.keyVaultUrl && (
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Key Vault</span>
                <code className="runtime-kv-code">{profile.skill_configs.Azure.keyVaultUrl}</code>
              </div>
            )}
            {(profile.skill_configs?.Azure?.microsoftAppIdSecretName ||
              profile.skill_configs?.Azure?.microsoftAppPasswordSecretName ||
              profile.skill_configs?.Azure?.microsoftAppTenantIdSecretName) && (
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Bot secrets</span>
                <code className="runtime-kv-code">
                  {[
                    profile.skill_configs.Azure.microsoftAppIdSecretName || "MicrosoftAppId",
                    profile.skill_configs.Azure.microsoftAppPasswordSecretName || "MicrosoftAppPassword",
                    profile.skill_configs.Azure.microsoftAppTenantIdSecretName || "MicrosoftAppTenantId",
                  ].join(", ")}
                </code>
              </div>
            )}
          </div>
        )}

        <div className="settings-card-footer" style={{ justifyContent: "flex-start" }}>
          <button
            type="button"
            className="primary-button"
            onClick={() => {
              setSettingsWizardStep(null);
              setAzureWizardOpen((open) => !open);
            }}
          >
            {azureWizardOpen ? "Close Azure setup" : "Configure Azure connection"}
          </button>
        </div>

        {azureWizardOpen && (
          <div className="settings-azure-wizard">
            <SetupWizardShell
              profile={profile}
              initialStep={4}
              forceWizard
              onProfileSaved={(saved) => {
                setProfile(saved);
                setAzureWizardOpen(false);
                void refreshSettings();
              }}
            />
          </div>
        )}
      </section>
    );
  }

  function renderDesktopTab() {
    return (
      <section className="subcard settings-card panel-tab-panel">
        <div className="settings-card-header">
          <div className="settings-card-icon settings-icon-identity">
            <span>UI</span>
          </div>
          <div>
            <p className="eyebrow">Desktop shell</p>
            <h3 className="settings-card-title">Quick access</h3>
          </div>
        </div>

        <p className="settings-card-desc">
          Add a Claude-style desktop access layer: tray icon, a global shortcut and optional close-to-tray behavior.
        </p>

        <div className="runtime-kv-list">
          <p className="runtime-kv-section-title">Activation</p>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Global shortcut</span>
            <code className="runtime-kv-code">{desktopShellPreferences.global_shortcut}</code>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Shortcut status</span>
            <span className={`status-tag ${desktopShellPreferences.global_shortcut_enabled ? "status-done" : "status-waiting"}`}>
              {desktopShellPreferences.global_shortcut_enabled ? "Enabled" : "Disabled"}
            </span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Tray icon</span>
            <span className={`status-tag ${desktopShellPreferences.tray_icon_enabled ? "status-done" : "status-waiting"}`}>
              {desktopShellPreferences.tray_icon_enabled ? "Visible" : "Hidden"}
            </span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Close action</span>
            <span className="runtime-kv-val">
              {desktopShellPreferences.close_to_tray_enabled ? "Hide to tray" : "Close normally"}
            </span>
          </div>
        </div>

        <div className="settings-shell-grid">
          <label className="settings-shell-toggle-card">
            <div>
              <strong>Enable shortcut</strong>
              <p>Press {desktopShellPreferences.global_shortcut} to bring AzulClaw to the front and focus chat.</p>
            </div>
            <input
              type="checkbox"
              checked={desktopShellPreferences.global_shortcut_enabled}
              onChange={(event) => {
                setDesktopShellPreferences((current) => ({
                  ...current,
                  global_shortcut_enabled: event.target.checked,
                }));
                setDesktopShellMessage("");
                setDesktopShellError("");
              }}
            />
          </label>

          <label className="settings-shell-toggle-card">
            <div>
              <strong>Show tray icon</strong>
              <p>Keep AzulClaw available from the system tray with open, hide and quit actions.</p>
            </div>
            <input
              type="checkbox"
              checked={desktopShellPreferences.tray_icon_enabled}
              onChange={(event) => {
                const trayEnabled = event.target.checked;
                setDesktopShellPreferences((current) => ({
                  ...current,
                  tray_icon_enabled: trayEnabled,
                  close_to_tray_enabled: trayEnabled ? current.close_to_tray_enabled : false,
                }));
                setDesktopShellMessage("");
                setDesktopShellError("");
              }}
            />
          </label>

          <label className="settings-shell-toggle-card">
            <div>
              <strong>Close to tray</strong>
              <p>When the window close button is pressed, hide AzulClaw instead of exiting.</p>
            </div>
            <input
              type="checkbox"
              checked={desktopShellPreferences.close_to_tray_enabled}
              disabled={!desktopShellPreferences.tray_icon_enabled}
              onChange={(event) => {
                setDesktopShellPreferences((current) => ({
                  ...current,
                  close_to_tray_enabled: event.target.checked,
                }));
                setDesktopShellMessage("");
                setDesktopShellError("");
              }}
            />
          </label>
        </div>

        {desktopShellMessage && (
          <p className="hw-inline-note" style={{ marginTop: "10px" }}>{desktopShellMessage}</p>
        )}
        {desktopShellError && (
          <p className="hw-inline-note hw-inline-note-warning" style={{ marginTop: "10px" }}>{desktopShellError}</p>
        )}

        <div className="settings-card-footer" style={{ gap: "10px", justifyContent: "flex-start" }}>
          <button
            type="button"
            className="primary-button"
            onClick={() => void handleSaveDesktopShellPreferences()}
            disabled={desktopShellBusy}
          >
            {desktopShellBusy ? "Saving..." : "Save desktop access"}
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={() => void loadDesktopShellPreferences().then(setDesktopShellPreferences)}
            disabled={desktopShellBusy}
          >
            Reload
          </button>
        </div>
      </section>
    );
  }

  function renderRuntimeTab() {
    return (
      <>
        <section className="subcard settings-card panel-tab-panel">
          <div className="settings-card-header">
            <div className="settings-card-icon settings-icon-security">
              <span>{backendStatus.status === "running" ? "OK" : "!"}</span>
            </div>
            <div>
              <p className="eyebrow">Backend</p>
              <h3 className="settings-card-title">Local brain process</h3>
            </div>
          </div>

          <div className="runtime-kv-list">
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">API</span>
              <span className={`status-tag ${backendStatus.status === "running" ? "status-done" : "status-failed"}`}>
                {backendStatus.status === "running" ? "Active" : "Offline"}
              </span>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Base URL</span>
              <code className="runtime-kv-code">{backendStatus.api_base || "http://localhost:3978"}</code>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Models</span>
              <span className={backendStatus.models_enabled > 0 ? "runtime-kv-val" : "status-tag status-waiting"}>
                {backendStatus.models_enabled} enabled / {backendStatus.models_total} total
              </span>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Scheduler</span>
              <span className={`status-tag ${backendStatus.scheduler_running ? "status-done" : "status-waiting"}`}>
                {backendStatus.scheduler_running ? "Running" : "Stopped"}
              </span>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Auth</span>
              <span
                className={`status-tag ${
                  backendStatus.auth.status === "authenticated"
                    ? "status-done"
                    : backendStatus.auth.status === "authenticating" || backendStatus.auth.status === "disabled"
                      ? "status-waiting"
                      : "status-failed"
                }`}
              >
                {backendStatus.auth.status}
              </span>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Auth mode</span>
              <span className="runtime-kv-val">{backendStatus.auth.mode}</span>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Auth detail</span>
              <span className="runtime-kv-val">{backendStatus.auth.detail}</span>
            </div>
            {backendStatus.auth.last_success_at && (
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Last sign-in</span>
                <code className="runtime-kv-code">{backendStatus.auth.last_success_at}</code>
              </div>
            )}
            {backendStatus.auth.last_error && (
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Auth error</span>
                <code className="runtime-kv-code">{backendStatus.auth.last_error}</code>
              </div>
            )}
            {backendStatus.runtime_dir && (
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Runtime dir</span>
                <code className="runtime-kv-code">{backendStatus.runtime_dir}</code>
              </div>
            )}
            {backendStatus.log_dir && (
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Log dir</span>
                <code className="runtime-kv-code">{backendStatus.log_dir}</code>
              </div>
            )}
          </div>

          {backendStatus.status === "running" && backendStatus.models_enabled === 0 && (
            <p className="hw-inline-note hw-inline-note-warning" style={{ marginTop: "12px" }}>
              Backend is running, but no model profile is enabled. Chat will answer with the runtime configuration warning until a provider is configured.
            </p>
          )}
          {backendStatus.status === "offline" && (
            <p className="hw-inline-note hw-inline-note-warning" style={{ marginTop: "12px" }}>
              The desktop UI cannot reach the backend. Check the packaged app logs or rebuild the installer after backend changes.
            </p>
          )}

          <div className="settings-card-footer" style={{ gap: "10px", justifyContent: "flex-start" }}>
            <button type="button" className="ghost-button" onClick={() => void loadBackendStatus().then(setBackendStatus)}>
              Refresh status
            </button>
            <button type="button" className="ghost-button" onClick={() => void handleEnsureAuth()} disabled={authBusy}>
              {authBusy ? "Authenticating..." : "Authenticate now"}
            </button>
            <button type="button" className="ghost-button" onClick={handleCopyLogs}>
              {logsCopied ? "Logs copied" : "Copy logs"}
            </button>
          </div>
          {authError && (
            <p className="hw-inline-note hw-inline-note-warning" style={{ marginTop: "12px" }}>
              Authentication failed: {authError}
            </p>
          )}

          {backendStatus.logs.length > 0 && (
            <div style={{ display: "grid", gap: "12px", marginTop: "12px" }}>
              {backendStatus.logs.map((log) => (
                <details key={log.name}>
                  <summary className="runtime-kv-key">
                    {log.name} {log.exists ? "" : "(missing)"}
                  </summary>
                  <pre className="error-code" style={{ marginTop: "8px", maxHeight: "220px", overflow: "auto", whiteSpace: "pre-wrap" }}>
                    {log.content || "No log content."}
                  </pre>
                </details>
              ))}
            </div>
          )}
        </section>

        <section className="subcard settings-card panel-tab-panel">
          <div className="settings-card-header">
            <div className="settings-card-icon settings-icon-models">
              <span>⬡</span>
            </div>
            <div>
              <p className="eyebrow">Models</p>
              <h3 className="settings-card-title">Providers</h3>
            </div>
          </div>

          <p className="settings-card-desc">
            Configure AI providers, deployments and fallback strategy for fast and slow lanes.
          </p>

          <div className="runtime-kv-list">
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Fast lane</span>
              <code className="runtime-kv-code">{fastModel?.deployment || azureConfig.fastDeployment || "Not configured"}</code>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Slow lane</span>
              <code className="runtime-kv-code">{slowModel?.deployment || azureConfig.deployment || "Not configured"}</code>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Provider</span>
              <span className="runtime-kv-val">{providerLabel}</span>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Embedding</span>
              <code className="runtime-kv-code">{azureConfig.embeddingDeployment || "Not configured"}</code>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Default lane</span>
              <code className="runtime-kv-code">{runtime.default_lane}</code>
            </div>
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Streaming</span>
              <span className="runtime-kv-val">
                {runtime.models.filter((model) => model.streaming_enabled).map((model) => model.label).join(", ") || "Disabled"}
              </span>
            </div>
          </div>

          <div className="settings-card-footer">
            <button type="button" className="skill-action-btn" onClick={openAzureWizard}>
              Configure Azure models
            </button>
          </div>
        </section>
      </>
    );
  }

  function renderMemoryTab() {
    return (
      <section className="subcard settings-card panel-tab-panel">
        <div className="settings-card-header">
          <div className="settings-card-icon settings-icon-identity">
            <span>DB</span>
          </div>
          <div>
            <p className="eyebrow">Memory</p>
            <h3 className="settings-card-title">Storage and retrieval</h3>
          </div>
        </div>

        <p className="settings-card-desc">
          Persistent memory uses a SQLite database under the workspace by default. Override it only when you want to keep memory in a different local folder.
        </p>

        <div className="runtime-kv-list">
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Active database</span>
            <code className="runtime-kv-code">{memorySettings.memory_db_path}</code>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Default database</span>
            <code className="runtime-kv-code">{memorySettings.default_memory_db_path}</code>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Vector memory</span>
            <span className={`status-tag ${memorySettings.vector_memory_enabled ? "status-done" : "status-waiting"}`}>
              {memorySettings.vector_memory_enabled ? "Enabled" : "Disabled"}
            </span>
          </div>
        </div>

        <div style={{ display: "grid", gap: "12px", marginTop: "14px" }}>
          <label className="hw-modal-field">
            <span className="hw-field-label">Memory database override</span>
            <input
              className="hw-modal-input hw-input-mono"
              type="text"
              value={memoryPathDraft}
              placeholder={memorySettings.default_memory_db_path}
              onChange={(event) => {
                setMemoryPathDraft(event.target.value);
                setMemorySettingsMessage("");
                setMemorySettingsError("");
              }}
            />
            <span className="hw-inline-note">Leave empty to use the workspace default.</span>
          </label>

          <label className="toggle-row runtime-toggle" style={{ marginTop: 0 }}>
            <input
              type="checkbox"
              checked={memorySettings.vector_memory_enabled}
              onChange={(event) => {
                setMemorySettings((current) => ({ ...current, vector_memory_enabled: event.target.checked }));
                setMemorySettingsMessage("");
                setMemorySettingsError("");
              }}
            />
            Semantic memory retrieval
          </label>
        </div>

        {memorySettingsMessage && (
          <p className="hw-inline-note" style={{ marginTop: "10px" }}>{memorySettingsMessage}</p>
        )}
        {memorySettingsError && (
          <p className="hw-inline-note hw-inline-note-warning" style={{ marginTop: "10px" }}>{memorySettingsError}</p>
        )}

        <div className="settings-card-footer" style={{ gap: "10px", justifyContent: "flex-start" }}>
          <button type="button" className="primary-button" onClick={() => void handleSaveMemorySettings()} disabled={memorySettingsBusy}>
            {memorySettingsBusy ? "Saving..." : "Save memory settings"}
          </button>
          <button type="button" className="ghost-button" onClick={handleResetMemoryPath} disabled={memorySettingsBusy}>
            Use workspace default
          </button>
        </div>
      </section>
    );
  }

  function renderIdentityTab() {
    return (
      <section className="subcard settings-card panel-tab-panel">
        <div className="settings-card-header">
          <div className="settings-card-icon settings-icon-identity">
            <span>✦</span>
          </div>
          <div>
            <p className="eyebrow">Identity</p>
            <h3 className="settings-card-title">Personality</h3>
          </div>
        </div>

        <p className="settings-card-desc">
          Configure how AzulClaw presents itself - its name, tone, style and base role.
        </p>

        <div className="runtime-kv-list">
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Name</span>
            <span className="runtime-kv-val">{profile.name || "Not configured"}</span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Archetype</span>
            <span className="runtime-kv-val">{profile.archetype || "Not configured"}</span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Tone</span>
            <span className="runtime-kv-val">{profile.tone || "Not configured"}</span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Style</span>
            <span className="runtime-kv-val">{profile.style || "Not configured"}</span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Autonomy</span>
            <span className="runtime-kv-val">{profile.autonomy || "Not configured"}</span>
          </div>
        </div>

        <div className="settings-card-footer">
          <button type="button" className="skill-action-btn" onClick={() => setSettingsWizardStep(0)}>
            Edit identity
          </button>
        </div>
      </section>
    );
  }

  function renderSecurityTab() {
    return (
      <section className="subcard settings-card panel-tab-panel">
        <div className="settings-card-header">
          <div className="settings-card-icon settings-icon-security">
            <span>◈</span>
          </div>
          <div>
            <p className="eyebrow">Security</p>
            <h3 className="settings-card-title">Workspace and approvals</h3>
          </div>
        </div>

        <p className="settings-card-desc">
          Manage confirmation boundaries, workspace location and the local guardrails around file and tool access.
        </p>

        <div className="runtime-kv-list">
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Confirm sensitive</span>
            <span className={`status-tag ${profile.confirm_sensitive_actions ? "status-done" : "status-waiting"}`}>
              {profile.confirm_sensitive_actions ? "Enabled" : "Disabled"}
            </span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Workspace</span>
            <code className="runtime-kv-code">{profile.workspace_root || "Not configured"}</code>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Path escaping</span>
            <span className="status-tag status-done">Blocked</span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Destructive ops</span>
            <span className="status-tag status-done">Guarded</span>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Credentials</span>
            <span className={`status-tag ${azureConfig.connected === "true" ? "status-done" : "status-waiting"}`}>
              {azureConfig.connected === "true" ? "Azure connected" : "Needs Azure"}
            </span>
          </div>
        </div>

        <div className="settings-card-footer">
          <button type="button" className="skill-action-btn" onClick={() => setSettingsWizardStep(7)}>
            Review workspace and approvals
          </button>
        </div>
      </section>
    );
  }

  function renderDataTab() {
    if (!onLocalDataWiped) {
      return null;
    }
    return (
      <section className="subcard settings-card panel-tab-panel" style={{ borderColor: "var(--danger, #b42318)" }}>
        <p className="eyebrow" style={{ color: "var(--danger, #b42318)" }}>Data &amp; Hatching</p>
        <h3 className="settings-card-title">Erase local AzulClaw data</h3>
        <p className="settings-card-desc">
          Removes the SQLite memory database and resets your profile setup. You will go through Hatching again.
          Restart the brain process afterward so memory reopens cleanly.
        </p>
        <div className="settings-card-footer" style={{ justifyContent: "flex-start" }}>
          <button
            type="button"
            className="primary-button"
            style={{ background: "var(--danger, #b42318)", borderColor: "var(--danger, #b42318)" }}
            onClick={openWipeModal}
          >
            Erase data and reset Hatching
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="single-panel-layout">
      <SectionTopbarPortal
        target={headerPortalTarget}
        fallback={<div className="section-page-header-fallback">{headerContent}</div>}
      >
        {headerContent}
      </SectionTopbarPortal>
      <div className="card panel-stack">
        <div className="panel-tabs" role="tablist" aria-label="Settings categories">
          {SETTINGS_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={`panel-tab${activeTab === tab.id ? " panel-tab-active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "azure" && renderAzureTab()}
        {activeTab === "desktop" && renderDesktopTab()}
        {activeTab === "runtime" && renderRuntimeTab()}
        {activeTab === "memory" && renderMemoryTab()}
        {activeTab === "identity" && renderIdentityTab()}
        {activeTab === "security" && renderSecurityTab()}
        {activeTab === "data" && renderDataTab()}

        {settingsWizardStep !== null && (
          <section className="subcard settings-card">
            <div className="settings-card-header">
              <div>
                <p className="eyebrow">Edit profile</p>
                <h3 className="settings-card-title">Profile setup</h3>
              </div>
              <button type="button" className="ghost-button" onClick={() => setSettingsWizardStep(null)}>
                Close
              </button>
            </div>
            <div className="settings-azure-wizard">
              <SetupWizardShell
                profile={profile}
                initialStep={settingsWizardStep}
                forceWizard
                onProfileSaved={(saved) => {
                  setProfile(saved);
                  setSettingsWizardStep(null);
                  void refreshSettings();
                }}
              />
            </div>
          </section>
        )}

        {wipeModalOpen && (
          <div className="hw-modal-backdrop" onClick={closeWipeModal}>
            <section
              className="hw-modal-card"
              role="dialog"
              aria-modal="true"
              style={{ maxWidth: "480px" }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="hw-modal-head">
                <div>
                  <p className="hw-label" style={{ color: "var(--danger, #b42318)" }}>DESTRUCTIVE ACTION</p>
                  <h3 className="hw-modal-title">Confirm data erasure</h3>
                </div>
                <button type="button" className="hw-btn-ghost" onClick={closeWipeModal} disabled={wipeBusy}>Close</button>
              </div>

              <p className="hw-inline-note" style={{ marginBottom: "16px" }}>
                This will permanently delete all memory and reset your profile. To confirm, copy and type the phrase below.
              </p>

              <div style={{ display: "flex", alignItems: "center", gap: "10px", background: "var(--surface-2, #1e1e2e)", borderRadius: "6px", padding: "10px 14px", marginBottom: "16px" }}>
                <code style={{ flex: 1, fontSize: "0.85rem", letterSpacing: "0.04em", userSelect: "all" }}>
                  {DATA_WIPE_CONFIRM_PHRASE}
                </code>
                <button type="button" className="ghost-button" style={{ flexShrink: 0 }} onClick={handleCopyPhrase}>
                  {wipeCopied ? "Copied" : "Copy"}
                </button>
              </div>

              <label className="hw-modal-field">
                <span className="hw-field-label">Type the phrase to confirm</span>
                <input
                  className="hw-modal-input hw-input-mono"
                  type="text"
                  autoComplete="off"
                  placeholder="Paste or type here..."
                  value={wipePhrase}
                  onChange={(e) => setWipePhrase(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !wipeBusy) void handleWipeLocalData(); }}
                  autoFocus
                />
              </label>

              {wipeError && (
                <p className="hw-inline-note hw-inline-note-warning" style={{ marginTop: "8px" }}>{wipeError}</p>
              )}

              <div className="hw-modal-actions" style={{ marginTop: "16px" }}>
                <button type="button" className="hw-btn-ghost" onClick={closeWipeModal} disabled={wipeBusy}>Cancel</button>
                <button
                  type="button"
                  className="hw-btn-primary"
                  style={{ background: "var(--danger, #b42318)", borderColor: "var(--danger, #b42318)" }}
                  disabled={wipeBusy || wipePhrase.trim() !== DATA_WIPE_CONFIRM_PHRASE}
                  onClick={() => void handleWipeLocalData()}
                >
                  {wipeBusy ? "Wiping..." : "Erase everything"}
                </button>
              </div>
            </section>
          </div>
        )}

        <div className="panel-footer">
          <p className="hint-text">Heartbeat and scheduled job settings live in the Heartbeats view.</p>
          <button
            type="button"
            className="ghost-button"
            disabled={isSaving}
            onClick={() => {
              setIsSaving(true);
              void refreshSettings().finally(() => setIsSaving(false));
            }}
          >
            {isSaving ? "Refreshing..." : "Refresh settings"}
          </button>
        </div>
      </div>
    </section>
  );
}
