import { useEffect, useState } from "react";

import {
  DATA_WIPE_CONFIRM_PHRASE,
  ensureBackendAuth,
  loadHatching,
  loadBackendStatus,
  loadMemorySettings,
  loadRuntime,
  saveMemorySettings,
  wipeLocalUserData,
} from "../../lib/api";
import { profileCanRenewAzureLogin, renewAzureLoginFromProfile } from "../../lib/azure-session";
import { HatchingShell } from "../hatching/HatchingShell";
import type { BackendStatus, HatchingProfile, MemorySettings, RuntimeOverview } from "../../lib/contracts";
import { defaultHatchingProfile, memorySettings as defaultMemorySettings, runtimeOverview } from "../../lib/mock-data";

interface SettingsShellProps {
  /** After a successful wipe, parent should replace profile (triggers onboarding when ``is_hatched`` is false). */
  onLocalDataWiped?: (profile: HatchingProfile) => void;
}

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

export function SettingsShell({ onLocalDataWiped }: SettingsShellProps) {
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
  const [profile, setProfile] = useState<HatchingProfile>(defaultHatchingProfile);
  const [memorySettings, setMemorySettings] = useState<MemorySettings>(defaultMemorySettings);
  const [memoryPathDraft, setMemoryPathDraft] = useState("");
  const [memorySettingsBusy, setMemorySettingsBusy] = useState(false);
  const [memorySettingsMessage, setMemorySettingsMessage] = useState("");
  const [memorySettingsError, setMemorySettingsError] = useState("");
  const [azureWizardOpen, setAzureWizardOpen] = useState(false);
  const [settingsWizardStep, setSettingsWizardStep] = useState<number | null>(null);
  const azureConfig = profile.skill_configs?.Azure ?? {};
  const fastModel = runtime.models.find((model) => model.lane === "fast");
  const slowModel = runtime.models.find((model) => model.lane === "slow");
  const providerLabel = runtime.models.length > 0
    ? Array.from(new Set(runtime.models.map((model) => model.provider))).join(", ")
    : azureConfig.endpoint
      ? "azure"
      : "Not configured";

  async function refreshSettings() {
    const [runtimeData, backendData, profileData, memoryData] = await Promise.all([
      loadRuntime(),
      loadBackendStatus(),
      loadHatching(),
      loadMemorySettings(),
    ]);
    setRuntime(runtimeData);
    setBackendStatus(backendData);
    setProfile(profileData);
    setMemorySettings(memoryData);
    setMemoryPathDraft(memoryData.memory_db_path_override || "");
  }

  useEffect(() => {
    let isMounted = true;

    async function refreshMountedSettings() {
      const [runtimeData, backendData, profileData, memoryData] = await Promise.all([
        loadRuntime(),
        loadBackendStatus(),
        loadHatching(),
        loadMemorySettings(),
      ]);
      if (!isMounted) {
        return;
      }
      setRuntime(runtimeData);
      setBackendStatus(backendData);
      setProfile(profileData);
      setMemorySettings(memoryData);
      setMemoryPathDraft((current) => current || memoryData.memory_db_path_override || "");
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
    try {
      const auth = await ensureBackendAuth();
      if (auth.requires_frontend_login && profileCanRenewAzureLogin(profile)) {
        await renewAzureLoginFromProfile(profile);
      }
      const backendData = await loadBackendStatus();
      setBackendStatus(backendData);
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
      const result = await wipeLocalUserData(wipePhrase.trim());
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

  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">

        <div className="panel-heading">
          <div>
            <p className="eyebrow">Settings</p>
            <h2>Preferences and configuration</h2>
          </div>
          <div className="filter-row">
            <span className="status-pill">{runtime.default_lane}</span>
            <span className="status-pill">{runtime.models.length} models</span>
          </div>
        </div>

        <section className="subcard settings-card">
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
                    : backendStatus.auth.status === "authenticating"
                      ? "status-waiting"
                      : backendStatus.auth.status === "disabled"
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

        <section className="subcard settings-card">
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

        <section className="subcard settings-card">
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
            <div className="runtime-kv-row">
              <span className="runtime-kv-key">Status</span>
              <span className={`status-tag ${profile.skill_configs?.Azure?.connected === "true" ? "status-done" : "status-waiting"}`}>
                {profile.skill_configs?.Azure?.connected === "true" ? "Connected" : "Not connected"}
              </span>
            </div>
            {profile.skill_configs?.Azure?.endpoint && (
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Endpoint</span>
                <code className="runtime-kv-code">{profile.skill_configs.Azure.endpoint}</code>
              </div>
            )}
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
            {profile.skill_configs?.Azure?.deployment && (
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Main deployment</span>
                <code className="runtime-kv-code">{profile.skill_configs.Azure.deployment}</code>
              </div>
            )}
            {profile.skill_configs?.Azure?.lastConnectedAt && (
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Last authorized</span>
                <code className="runtime-kv-code">{new Date(profile.skill_configs.Azure.lastConnectedAt).toLocaleString()}</code>
              </div>
            )}
          </div>

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
              <HatchingShell
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

        <div className="three-column-grid">

          {/* ── Identity ──────────────────────────── */}
          <section className="subcard settings-card">
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
              Configure how AzulClaw presents itself — its name, tone, style and base role.
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
              <button
                type="button"
                className="skill-action-btn"
                onClick={() => {
                  setAzureWizardOpen(false);
                  setSettingsWizardStep(0);
                }}
              >
                Edit identity
              </button>
            </div>
          </section>

          {/* ── Models ────────────────────────────── */}
          <section className="subcard settings-card">
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
              <button
                type="button"
                className="skill-action-btn"
                onClick={() => {
                  setSettingsWizardStep(null);
                  setAzureWizardOpen(true);
                }}
              >
                Configure Azure models
              </button>
            </div>
          </section>

          {/* ── Security ──────────────────────────── */}
          <section className="subcard settings-card">
            <div className="settings-card-header">
              <div className="settings-card-icon settings-icon-security">
                <span>◈</span>
              </div>
              <div>
                <p className="eyebrow">Security</p>
                <h3 className="settings-card-title">Approvals</h3>
              </div>
            </div>

            <p className="settings-card-desc">
              Manage which actions require human confirmation and control the sandbox boundaries.
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
              <button
                type="button"
                className="skill-action-btn"
                onClick={() => {
                  setAzureWizardOpen(false);
                  setSettingsWizardStep(7);
                }}
              >
                Review workspace and approvals
              </button>
            </div>
          </section>

        </div>

        {settingsWizardStep !== null && (
          <section className="subcard settings-card">
            <div className="settings-card-header">
              <div>
                <p className="eyebrow">Edit profile</p>
                <h3 className="settings-card-title">Hatching configuration</h3>
              </div>
              <button type="button" className="ghost-button" onClick={() => setSettingsWizardStep(null)}>
                Close
              </button>
            </div>
            <div className="settings-azure-wizard">
              <HatchingShell
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

        {onLocalDataWiped && (
          <section className="subcard" style={{ borderColor: "var(--danger, #b42318)", marginTop: "8px" }}>
            <p className="eyebrow" style={{ color: "var(--danger, #b42318)" }}>Data &amp; onboarding</p>
            <h3 className="settings-card-title">Erase local AzulClaw data</h3>
            <p className="hint-text" style={{ marginBottom: "14px" }}>
              Removes the SQLite memory database and resets your hatching profile.
              You will go through onboarding again. Restart the brain process afterward so memory reopens cleanly.
            </p>
            <button
              type="button"
              className="primary-button"
              style={{ background: "var(--danger, #b42318)", borderColor: "var(--danger, #b42318)" }}
              onClick={openWipeModal}
            >
              Erase data and reset onboarding
            </button>
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
                This will permanently delete all memory and reset your profile.
                To confirm, copy and type the phrase below.
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
                  {wipeBusy ? "Wiping…" : "Erase everything"}
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
