import { isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { SectionTopbarPortal } from "../../components/SectionTopbarPortal";
import {
  apiUrl,
  installSkill,
  loadInstalledSkills,
  loadSkillMarketplaceSettings,
  loadSkillMarketplace,
  loadSkillRuntimeStatus,
  refreshSkillMarketplace,
  saveSkillConfig,
  setSkillEnabled,
  uninstallSkill,
} from "../../lib/api";
import type { SkillMarketplaceSettings, SkillRuntimeStatus, SkillSummary } from "../../lib/contracts";

type SkillTab = "marketplace" | "installed";
type SkillKindFilter = "all" | SkillSummary["kind"];
type MarketplaceTutorialTarget = "intro" | "filters" | "card" | "install";

const MARKETPLACE_TUTORIAL_STORAGE_KEY = "azul.marketplace.tutorial.seen";
const MARKETPLACE_TUTORIAL_STEPS = [
  {
    target: "intro",
    title: "Welcome to the AzulClaw Marketplace",
    body: "This is where AzulClaw gets new capabilities. Browse official and company-published skills, review what they can do, and choose what belongs in your local agent.",
  },
  {
    target: "filters",
    title: "Find the right skill",
    body: "Use search, type filters, and the Marketplace or Installed tabs to narrow the catalog as your company adds more agents, tools, workflows, and connectors.",
  },
  {
    target: "card",
    title: "Preview before you install",
    body: "Each card shows the skill type, publisher, status, tags, and a generated or custom banner. Click the card body to open the full detail view with permissions and capabilities.",
  },
  {
    target: "install",
    title: "Install, configure, activate",
    body: "Use the action button to install a skill. If configuration is required, AzulClaw will guide you through the required fields before the skill can be enabled.",
  },
] satisfies Array<{ target: MarketplaceTutorialTarget; title: string; body: string }>;

interface MarketplaceTourGeometry {
  top: number;
  left: number;
  width: number;
  height: number;
  popoverTop: number;
  popoverLeft: number;
}

function skillKindLabel(kind: string) {
  switch (kind) {
    case "local_mcp":
      return "Local MCP";
    case "remote_agent":
      return "Remote agent";
    case "knowledge":
      return "Knowledge";
    case "workflow":
      return "Workflow";
    case "channel_connector":
      return "Channel";
    default:
      return "Skill";
  }
}

function skillStatus(skill: SkillSummary) {
  if (skill.enabled && !skill.external_deployment_required) return { label: "Ready", className: "status-done" };
  if (skill.enabled && skill.external_deployment_required) return { label: "Needs deployment", className: "status-waiting" };
  if (skill.installed && !skill.configured) return { label: "Needs config", className: "status-waiting" };
  if (skill.installed && skill.configured) return { label: "Installed", className: "status-idle" };
  if (skill.installed) return { label: "Installed", className: "status-waiting" };
  return { label: "Available", className: "status-idle" };
}

function buildInitialConfig(skill: SkillSummary) {
  const fields = skill.config_schema?.properties ?? {};
  const current = skill.config ?? {};
  const names = new Set<string>(Object.keys(fields));
  for (const secret of skill.secrets ?? []) {
    if (secret.field) names.add(secret.field);
  }
  return Object.fromEntries(
    Array.from(names).map((field) => {
      const schema = fields[field];
      const value = current[field];
      if (typeof value === "string" && value !== "<configured>") {
        return [field, value];
      }
      if (typeof value === "number" && Number.isFinite(value)) {
        return [field, String(value)];
      }
      if (typeof value === "boolean") {
        return [field, value ? "true" : "false"];
      }
      if (typeof schema?.default === "string") {
        return [field, schema.default];
      }
      if (typeof schema?.default === "number" && Number.isFinite(schema.default)) {
        return [field, String(schema.default)];
      }
      if (typeof schema?.default === "boolean") {
        return [field, schema.default ? "true" : "false"];
      }
      return [field, ""];
    }),
  );
}

function isDraftBooleanEnabled(value: string | undefined) {
  return String(value ?? "").trim().toLowerCase() === "true";
}

function skillConfigContextNote(skill: SkillSummary, fieldKey: string, draft: Record<string, string>) {
  if (skill.id !== "dev.azulclaw.desktop-organizer") return "";
  if (fieldKey === "semanticCategorization") {
    return isDraftBooleanEnabled(draft.semanticCategorization)
      ? "Enabled: el agente puede proponer carpetas personalizadas y el servidor valida esos destinos antes de mover archivos."
      : "Disabled: la skill usa solo las categorías fijas por extensión, como Documents o Images.";
  }
  return "";
}

function searchableText(skill: SkillSummary) {
  return [
    skill.name,
    skill.description,
    skill.publisher,
    skill.kind,
    skill.runtime_kind,
    ...(skill.tags ?? []),
    ...(skill.categories ?? []),
  ].join(" ").toLowerCase();
}

function flattenPermissions(permissions: Record<string, unknown> | undefined) {
  if (!permissions) return [];
  const items: string[] = [];
  for (const [key, value] of Object.entries(permissions)) {
    if (Array.isArray(value)) {
      if (value.length) items.push(`${key}: ${value.length}`);
      continue;
    }
    if (typeof value === "boolean") {
      items.push(`${key}: ${value ? "yes" : "no"}`);
      continue;
    }
    if (typeof value === "string" && value) {
      items.push(`${key}: ${value}`);
    }
  }
  return items;
}

function activationItems(skill: SkillSummary) {
  const activation = skill.activation ?? {};
  const items: Array<{ label: string; value: string }> = [];
  if (activation.restart_required === true) {
    items.push({ label: "Runtime restart", value: "Required after activation" });
  } else if (activation.restart_required === false) {
    items.push({ label: "Runtime restart", value: "Not required" });
  }
  if (activation.requires_azure_relay === true) {
    items.push({ label: "Azure relay", value: "Required" });
  }
  if (typeof activation.relay_function_path === "string" && activation.relay_function_path.trim()) {
    items.push({ label: "Relay code", value: activation.relay_function_path.trim() });
  }
  for (const [key, value] of Object.entries(activation)) {
    if (key === "restart_required" || key === "requires_azure_relay" || key === "relay_function_path") continue;
    if (typeof value === "boolean") {
      items.push({ label: key.replace(/_/g, " "), value: value ? "Yes" : "No" });
      continue;
    }
    if (typeof value === "string" && value.trim()) {
      items.push({ label: key.replace(/_/g, " "), value: value.trim() });
    }
  }
  return items;
}

function deploymentGuidance(skill: SkillSummary) {
  const activation = skill.activation ?? {};
  const notes: string[] = [];
  if (activation.requires_azure_relay === true) {
    notes.push("This skill depends on external Azure relay resources before channel traffic can flow.");
  }
  if (activation.restart_required === true) {
    notes.push("Enablement expects a backend restart or relay refresh before the skill is fully live.");
  }
  if (skill.kind === "remote_agent") {
    notes.push("The remote endpoint must remain reachable from the AzulClaw backend.");
  }
  return notes;
}

function deploymentResources(skill: SkillSummary) {
  const deployment = skill.deployment ?? {};
  const rows: Array<{ label: string; value: string }> = [];
  if (typeof deployment.skill_root_path === "string" && deployment.skill_root_path.trim()) {
    rows.push({ label: "Skill root", value: deployment.skill_root_path.trim() });
  }
  if (typeof deployment.runtime_path === "string" && deployment.runtime_path.trim()) {
    rows.push({ label: "Runtime", value: deployment.runtime_path.trim() });
  }
  if (typeof deployment.infra_path === "string" && deployment.infra_path.trim()) {
    rows.push({ label: "Terraform", value: deployment.infra_path.trim() });
  }
  if (typeof deployment.docs_path === "string" && deployment.docs_path.trim()) {
    rows.push({ label: "Docs", value: deployment.docs_path.trim() });
  }
  if (typeof deployment.readme_path === "string" && deployment.readme_path.trim()) {
    rows.push({ label: "README", value: deployment.readme_path.trim() });
  }
  return rows;
}

function hasConfigFields(skill: SkillSummary) {
  return Object.keys(skill.config_schema?.properties ?? {}).length > 0 || (skill.secrets?.length ?? 0) > 0;
}

function skillInitials(skill: SkillSummary) {
  return skill.name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("") || "SK";
}

function sourceLabel(skill: SkillSummary) {
  const source = skill.source?.kind ?? "unknown";
  if (source === "registry" && skill.source?.registry) return skill.source.registry;
  return source;
}

function skillVisualClass(skill: SkillSummary) {
  const configuredVariant = skill.presentation?.banner?.variant;
  if (configuredVariant) return configuredVariant;
  const id = skill.id.toLowerCase();
  if (id.includes("telegram")) return "telegram";
  if (id.includes("gemini")) return "gemini";
  if (id.includes("desktop")) return "desktop";
  if (skill.kind === "local_mcp") return "desktop";
  if (skill.kind === "remote_agent") return "gemini";
  if (skill.kind === "channel_connector") return "telegram";
  return "default";
}

function skillVisualLabel(skill: SkillSummary) {
  const configuredTitle = skill.presentation?.banner?.title?.trim();
  if (configuredTitle) return configuredTitle;
  const visual = skillVisualClass(skill);
  if (visual === "telegram") return "Telegram";
  if (visual === "gemini") return "Gemini";
  if (visual === "desktop") return "Workspace";
  return skillKindLabel(skill.kind);
}

function bannerImageUrl(skill: SkillSummary) {
  const image = skill.presentation?.banner?.image?.trim();
  if (!image) return "";
  if (/^(https?:|data:|blob:|\/)/i.test(image)) return image;
  const encodedPath = image.split("/").map((part) => encodeURIComponent(part)).join("/");
  return apiUrl(`/api/desktop/skills/${encodeURIComponent(skill.id)}/assets/${encodedPath}`);
}

export function SkillsShell({
  headerPortalTarget = null,
  onOpenMarketplaceSettings,
}: {
  headerPortalTarget?: HTMLElement | null;
  onOpenMarketplaceSettings?: () => void;
}) {
  const [activeTab, setActiveTab] = useState<SkillTab>("marketplace");
  const [kindFilter, setKindFilter] = useState<SkillKindFilter>("all");
  const [query, setQuery] = useState("");
  const [marketplace, setMarketplace] = useState<SkillSummary[]>([]);
  const [installed, setInstalled] = useState<SkillSummary[]>([]);
  const [marketplaceSettings, setMarketplaceSettings] = useState<SkillMarketplaceSettings>({ registry_url: "" });
  const [runtimeStatusBySkillId, setRuntimeStatusBySkillId] = useState<Record<string, SkillRuntimeStatus>>({});
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [busySkillId, setBusySkillId] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [configSkill, setConfigSkill] = useState<SkillSummary | null>(null);
  const [configDraft, setConfigDraft] = useState<Record<string, string>>({});
  const [configPickerError, setConfigPickerError] = useState("");
  const [pickingConfigFieldKey, setPickingConfigFieldKey] = useState("");
  const [configToastMessage, setConfigToastMessage] = useState("");
  const [tutorialStep, setTutorialStep] = useState(0);
  const [copiedDeploymentPath, setCopiedDeploymentPath] = useState("");
  const [tourGeometry, setTourGeometry] = useState<MarketplaceTourGeometry | null>(null);
  const configToastTimerRef = useRef<number | null>(null);
  const [showTutorial, setShowTutorial] = useState(() => {
    try {
      return window.localStorage.getItem(MARKETPLACE_TUTORIAL_STORAGE_KEY) !== "true";
    } catch {
      return false;
    }
  });
  const introRef = useRef<HTMLDivElement | null>(null);
  const controlsRef = useRef<HTMLDivElement | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const firstCardRef = useRef<HTMLElement | null>(null);
  const firstInstallButtonRef = useRef<HTMLButtonElement | null>(null);

  async function reload() {
    setIsLoading(true);
    setError("");
    try {
      const [marketplaceData, installedData, settingsData, runtimeData] = await Promise.all([
        loadSkillMarketplace(),
        loadInstalledSkills(),
        loadSkillMarketplaceSettings(),
        loadSkillRuntimeStatus(),
      ]);
      setMarketplace(marketplaceData.items);
      setInstalled(installedData.items);
      setMarketplaceSettings(settingsData);
      setRuntimeStatusBySkillId(
        Object.fromEntries((runtimeData.items ?? []).map((item) => [item.skill_id, item])),
      );
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not load skills.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  useEffect(() => {
    return () => {
      if (configToastTimerRef.current !== null) {
        window.clearTimeout(configToastTimerRef.current);
      }
    };
  }, []);

  function tutorialTargetElement() {
    const target = MARKETPLACE_TUTORIAL_STEPS[tutorialStep]?.target;
    if (target === "intro") return introRef.current;
    if (target === "filters") return controlsRef.current;
    if (target === "card") return firstCardRef.current ?? gridRef.current;
    if (target === "install") return firstInstallButtonRef.current ?? firstCardRef.current ?? gridRef.current;
    return introRef.current;
  }

  const visibleSkills = useMemo(() => {
    const source = activeTab === "marketplace" ? marketplace : installed;
    const normalizedQuery = query.trim().toLowerCase();
    return source.filter((skill) => {
      if (kindFilter !== "all" && skill.kind !== kindFilter) return false;
      if (normalizedQuery && !searchableText(skill).includes(normalizedQuery)) return false;
      return true;
    });
  }, [activeTab, installed, kindFilter, marketplace, query]);

  const selectedSkill = useMemo(() => {
    const source = activeTab === "marketplace" ? marketplace : installed;
    return source.find((skill) => skill.id === selectedSkillId) ?? null;
  }, [activeTab, installed, marketplace, selectedSkillId]);

  useEffect(() => {
    if (!showTutorial || selectedSkillId) {
      setTourGeometry(null);
      return;
    }

    function updateTourGeometry() {
      const element = tutorialTargetElement();
      if (!element) {
        setTourGeometry(null);
        return;
      }
      const rect = element.getBoundingClientRect();
      const padding = 8;
      const spotlightTop = Math.max(12, rect.top - padding);
      const spotlightLeft = Math.max(12, rect.left - padding);
      const spotlightWidth = Math.min(window.innerWidth - spotlightLeft - 12, rect.width + padding * 2);
      const spotlightHeight = Math.min(window.innerHeight - spotlightTop - 12, rect.height + padding * 2);
      const popoverWidth = Math.min(340, window.innerWidth - 32);
      const gap = 18;
      const rightCandidate = spotlightLeft + spotlightWidth + gap;
      const leftCandidate = spotlightLeft - popoverWidth - gap;
      const popoverLeft = rightCandidate + popoverWidth <= window.innerWidth - 16
        ? rightCandidate
        : Math.max(16, leftCandidate);
      const belowCandidate = spotlightTop + spotlightHeight + gap;
      const alignedTop = Math.min(Math.max(16, spotlightTop), Math.max(16, window.innerHeight - 300));
      const popoverTop = window.innerWidth < 780
        ? Math.min(belowCandidate, Math.max(16, window.innerHeight - 300))
        : alignedTop;

      setTourGeometry({
        top: spotlightTop,
        left: spotlightLeft,
        width: spotlightWidth,
        height: spotlightHeight,
        popoverTop,
        popoverLeft,
      });
    }

    const frame = window.requestAnimationFrame(updateTourGeometry);
    window.addEventListener("resize", updateTourGeometry);
    window.addEventListener("scroll", updateTourGeometry, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updateTourGeometry);
      window.removeEventListener("scroll", updateTourGeometry, true);
    };
  }, [activeTab, isLoading, selectedSkillId, showTutorial, tutorialStep, visibleSkills.length]);

  const kindOptions = useMemo(() => {
    const kinds = new Set<SkillSummary["kind"]>();
    [...marketplace, ...installed].forEach((skill) => kinds.add(skill.kind));
    return Array.from(kinds).sort();
  }, [installed, marketplace]);

  const registryUrl = marketplaceSettings.registry_url?.trim() ?? "";
  const registryNeedsFunctionKey =
    registryUrl &&
    marketplaceSettings.registry_auth_mode === "function_key" &&
    !marketplaceSettings.registry_consumer_key_configured;
  const registryHost = useMemo(() => {
    if (!registryUrl) return "";
    try {
      return new URL(registryUrl).host;
    } catch {
      return registryUrl;
    }
  }, [registryUrl]);

  async function runSkillAction(skillId: string, action: () => Promise<unknown>) {
    setBusySkillId(skillId);
    setError("");
    try {
      await action();
      await reload();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Skill action failed.");
    } finally {
      setBusySkillId("");
    }
  }

  function openConfig(skill: SkillSummary) {
    setConfigSkill(skill);
    setConfigDraft(buildInitialConfig(skill));
    setConfigPickerError("");
    setPickingConfigFieldKey("");
  }

  function closeConfigModal() {
    setConfigSkill(null);
    setConfigDraft({});
    setConfigPickerError("");
    setPickingConfigFieldKey("");
  }

  function showConfigToast(message: string) {
    setConfigToastMessage(message);
    if (configToastTimerRef.current !== null) {
      window.clearTimeout(configToastTimerRef.current);
    }
    configToastTimerRef.current = window.setTimeout(() => {
      setConfigToastMessage("");
      configToastTimerRef.current = null;
    }, 2600);
  }

  async function handlePickConfigDirectory(fieldKey: string, fieldTitle: string) {
    setConfigPickerError("");
    if (!isTauri()) {
      setConfigPickerError("The native selector is only available inside the Tauri desktop app.");
      return;
    }

    setPickingConfigFieldKey(fieldKey);
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        defaultPath: configDraft[fieldKey] || undefined,
        title: `Select ${fieldTitle}`,
      });
      if (typeof selected === "string" && selected.trim()) {
        setConfigDraft((current) => ({ ...current, [fieldKey]: selected }));
      }
    } catch (nextError) {
      const detail = nextError instanceof Error ? nextError.message : String(nextError);
      console.error("Skill folder picker failed", nextError);
      setConfigPickerError(`Could not open the native selector. ${detail}`);
    } finally {
      setPickingConfigFieldKey("");
    }
  }

  function finishTutorial() {
    try {
      window.localStorage.setItem(MARKETPLACE_TUTORIAL_STORAGE_KEY, "true");
    } catch {
      /* keep the tutorial dismissible even when storage is unavailable */
    }
    setShowTutorial(false);
    setTutorialStep(0);
  }

  function nextTutorialStep() {
    if (tutorialStep >= MARKETPLACE_TUTORIAL_STEPS.length - 1) {
      finishTutorial();
      return;
    }
    setTutorialStep((current) => current + 1);
  }

  function reopenTutorial() {
    setSelectedSkillId("");
    setTutorialStep(0);
    setShowTutorial(true);
  }

  function copyDeploymentPath(value: string) {
    void navigator.clipboard.writeText(value);
    setCopiedDeploymentPath(value);
    window.setTimeout(() => {
      setCopiedDeploymentPath((current) => (current === value ? "" : current));
    }, 1400);
  }

  async function saveConfig() {
    if (!configSkill) return;
    const skillId = configSkill.id;
    setBusySkillId(skillId);
    setError("");
    try {
      if (!configSkill.installed) {
        await installSkill(skillId);
      }
      await saveSkillConfig(skillId, configDraft);
      closeConfigModal();
      showConfigToast("Changes saved.");
      await reload();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not save skill config.");
    } finally {
      setBusySkillId("");
    }
  }

  const headerContent = (
    <div className="section-topbar">
      <div className="section-topbar-copy">
        <p className="eyebrow">Marketplace</p>
        <h2 className="section-topbar-title">Marketplace</h2>
      </div>
      <div className="section-topbar-actions">
        <button
          type="button"
          className="marketplace-help-button"
          aria-label="Open marketplace tutorial"
          title="Open marketplace tutorial"
          onClick={reopenTutorial}
        >
          ?
        </button>
        <button
          type="button"
          className="ghost-button"
          onClick={() => void runSkillAction("refresh", async () => { await refreshSkillMarketplace(); })}
          disabled={busySkillId === "refresh"}
        >
          Refresh
        </button>
      </div>
    </div>
  );

  function renderSkillCard(skill: SkillSummary, index: number) {
    const status = skillStatus(skill);
    const previewTags = (skill.tags ?? []).slice(0, 3);
    const isBusy = busySkillId === skill.id;
    const needsConfig = skill.installed && !skill.configured;
    const bannerImage = bannerImageUrl(skill);
    const accent = skill.presentation?.banner?.accent?.trim();
    const bannerTitle = skillVisualLabel(skill);
    return (
      <article
        key={skill.id}
        ref={index === 0 ? firstCardRef : undefined}
        className={`marketplace-card${skill.installed ? " marketplace-card-installed" : ""}`}
      >
        <button
          type="button"
          className="marketplace-card-open"
          onClick={() => setSelectedSkillId(skill.id)}
        >
          <span
            className={`marketplace-card-visual marketplace-card-visual-${skillVisualClass(skill)}`}
            style={{
              ...(bannerImage ? { backgroundImage: `url(${bannerImage})` } : {}),
              ...(accent ? { "--skill-accent": accent } as CSSProperties : {}),
            }}
          >
            <span className="marketplace-visual-grid" aria-hidden="true" />
            <span className="marketplace-visual-mark">{bannerTitle}</span>
          </span>
          <span className="marketplace-card-body">
            <span className="marketplace-card-title-row">
              <span className="marketplace-card-title">{skill.name}</span>
              <span className="marketplace-status-inline">
                <span className={`marketplace-status-dot marketplace-status-${status.className.replace("status-", "")}`} />
                {status.label}
              </span>
            </span>
            <span className="marketplace-card-desc">{skill.description}</span>
          </span>
          <span className="marketplace-card-meta">
            <span>{skillKindLabel(skill.kind)}</span>
            <span>{skill.publisher}</span>
          </span>
          <span className="marketplace-card-tags">
            {previewTags.length ? previewTags.map((tag) => (
              <span key={tag} className="skill-tag">{tag}</span>
            )) : <span className="skill-tag">{skill.runtime_kind}</span>}
          </span>
        </button>
        <div className="marketplace-card-footer">
          <button
            type="button"
            className="marketplace-card-install"
            ref={index === 0 ? firstInstallButtonRef : undefined}
            disabled={isBusy || (skill.installed && !skill.configured && !hasConfigFields(skill))}
            onClick={() => {
              if (!skill.installed) {
                void runSkillAction(skill.id, async () => { await installSkill(skill.id); });
                return;
              }
              if (needsConfig && hasConfigFields(skill)) {
                openConfig(skill);
                return;
              }
              void runSkillAction(skill.id, async () => { await setSkillEnabled(skill.id, !skill.enabled); });
            }}
          >
            {!skill.installed ? "Install" : needsConfig ? "Configure" : skill.enabled ? "Disable" : "Enable"}
          </button>
        </div>
      </article>
    );
  }

  function renderDetail(skill: SkillSummary) {
    const runtimeStatus = runtimeStatusBySkillId[skill.id];
    const activation = activationItems(skill);
    const deploymentNotes = deploymentGuidance(skill);
    const resources = deploymentResources(skill);
    return (
      <div className="marketplace-detail-page">
        <button type="button" className="marketplace-back-button" onClick={() => setSelectedSkillId("")}>
          Back to marketplace
        </button>

        <article className="marketplace-detail-hero">
          <div className="marketplace-detail-identity">
            <div className="marketplace-detail-icon">{skillInitials(skill)}</div>
            <div>
              <div className="skill-card-top">
                <span className={`status-tag ${skillStatus(skill).className}`}>{skillStatus(skill).label}</span>
                <span className="skill-kind-tag">{skillKindLabel(skill.kind)}</span>
              </div>
              <h3 className="marketplace-detail-title">{skill.name}</h3>
              <p className="marketplace-detail-desc">{skill.description}</p>
            </div>
          </div>
          <div className="skill-detail-actions">
            {renderPrimaryAction(skill)}
            {hasConfigFields(skill) ? (
              <button
                type="button"
                className="ghost-button"
                onClick={() => openConfig(skill)}
                disabled={busySkillId === skill.id}
              >
                Configure
              </button>
            ) : null}
            {skill.installed ? (
              <button
                type="button"
                className="ghost-button skill-action-danger"
                onClick={() => void runSkillAction(skill.id, async () => { await uninstallSkill(skill.id); })}
                disabled={busySkillId === skill.id}
              >
                Uninstall
              </button>
            ) : null}
          </div>
        </article>

        <div className="marketplace-detail-grid">
          <section className="marketplace-info-panel">
            <p className="runtime-kv-section-title">Details</p>
            <div className="marketplace-facts-grid">
              <div>
                <span>Publisher</span>
                <strong>{skill.publisher}</strong>
              </div>
              <div>
                <span>Version</span>
                <strong>{skill.version}</strong>
              </div>
              <div>
                <span>Runtime</span>
                <strong>{skill.runtime_kind}</strong>
              </div>
              <div>
                <span>Source</span>
                <strong>{sourceLabel(skill)}</strong>
              </div>
            </div>
            {skill.missing_required_fields.length ? (
              <div className="skill-warning">
                Missing config: {skill.missing_required_fields.join(", ")}
              </div>
            ) : null}
            {(skill.kind === "local_mcp" || skill.kind === "remote_agent" || skill.kind === "channel_connector") && skill.installed ? (
              <div className={`marketplace-runtime-note${runtimeStatus?.status === "connected" ? " marketplace-runtime-note-ready" : ""}`}>
                <strong>
                  {runtimeStatus
                    ? runtimeStatus.status === "connected"
                      ? skill.kind === "remote_agent"
                        ? "Remote agent ready"
                        : skill.kind === "channel_connector"
                          ? "Channel connector ready"
                          : "Local MCP runtime connected"
                      : skill.kind === "remote_agent"
                        ? "Remote agent error"
                        : skill.kind === "channel_connector"
                          ? "Channel connector error"
                          : "Local MCP runtime error"
                    : skill.enabled
                      ? skill.kind === "remote_agent"
                        ? "Remote agent pending"
                        : skill.kind === "channel_connector"
                          ? "Channel connector pending"
                          : "Local MCP runtime pending"
                      : skill.kind === "remote_agent"
                        ? "Enable the skill to activate this remote agent"
                        : skill.kind === "channel_connector"
                          ? "Enable the skill to activate this channel connector"
                          : "Enable the skill to start its local MCP runtime"}
                </strong>
                <p>
                  {runtimeStatus?.message
                    ?? (skill.enabled
                      ? skill.kind === "remote_agent"
                        ? "AzulClaw will call this remote endpoint when the skill is invoked."
                        : skill.kind === "channel_connector"
                          ? "This channel connector is configured through its skill manifest and external relay resources."
                        : "AzulClaw will try to connect this skill runtime when it is available."
                      : skill.kind === "remote_agent"
                        ? "This skill is only callable after it is enabled."
                        : skill.kind === "channel_connector"
                          ? "This connector only becomes active after the skill is enabled."
                        : "This skill starts its MCP runtime only when enabled.")}
                </p>
              </div>
            ) : null}
          </section>

          <section className="marketplace-info-panel">
            <p className="runtime-kv-section-title">Capabilities</p>
            <div className="skill-detail-list">
              {(skill.capabilities ?? []).map((capability) => (
                <span key={capability.id} className="skill-detail-chip">{capability.description}</span>
              ))}
            </div>
          </section>

          <section className="marketplace-info-panel">
            <p className="runtime-kv-section-title">Permissions</p>
            <div className="skill-detail-list">
              {flattenPermissions(skill.permissions).length ? (
                flattenPermissions(skill.permissions).map((permission) => (
                  <span key={permission} className="skill-detail-chip">{permission}</span>
                ))
              ) : (
                <span className="muted-text">No permissions declared.</span>
              )}
            </div>
          </section>

          <section className="marketplace-info-panel">
            <p className="runtime-kv-section-title">Deployment</p>
            {activation.length ? (
              <div className="marketplace-activation-grid">
                {activation.map((item) => (
                  <div key={`${item.label}-${item.value}`} className="marketplace-activation-item">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </div>
            ) : (
              <span className="muted-text">No extra activation requirements declared.</span>
            )}
            {deploymentNotes.length ? (
              <div className="marketplace-deployment-notes">
                {deploymentNotes.map((note) => (
                  <p key={note}>{note}</p>
                ))}
              </div>
            ) : null}
          </section>

          <section className="marketplace-info-panel">
            <p className="runtime-kv-section-title">Resources</p>
            {resources.length ? (
              <div className="marketplace-resource-list">
                {resources.map((resource) => (
                  <div key={`${resource.label}-${resource.value}`} className="marketplace-resource-row">
                    <div className="marketplace-resource-copy">
                      <span className="marketplace-resource-label">{resource.label}</span>
                      <code className="marketplace-resource-path">{resource.value}</code>
                    </div>
                    <button
                      type="button"
                      className="marketplace-resource-button"
                      onClick={() => copyDeploymentPath(resource.value)}
                    >
                      {copiedDeploymentPath === resource.value ? "Copied" : "Copy"}
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <span className="muted-text">No local deployment resources exposed for this skill.</span>
            )}
          </section>

          <section className="marketplace-info-panel">
            <p className="runtime-kv-section-title">Tags</p>
            <div className="skill-tags">
              {(skill.tags ?? []).length ? (skill.tags ?? []).map((tag) => (
                <span key={tag} className="skill-tag">{tag}</span>
              )) : <span className="muted-text">No tags declared.</span>}
            </div>
          </section>
        </div>
      </div>
    );
  }

  const renderPrimaryAction = (skill: SkillSummary) => {
    const isBusy = busySkillId === skill.id;
    if (!skill.installed) {
      return (
        <button
          type="button"
          className="primary-button"
          onClick={() => void runSkillAction(skill.id, async () => { await installSkill(skill.id); })}
          disabled={isBusy}
        >
          Install
        </button>
      );
    }
    return (
      <button
        type="button"
        className="primary-button"
        onClick={() => void runSkillAction(skill.id, async () => { await setSkillEnabled(skill.id, !skill.enabled); })}
        disabled={isBusy || (!skill.configured && !skill.enabled)}
      >
        {skill.enabled ? "Disable" : "Enable"}
      </button>
    );
  };

  return (
    <section className="single-panel-layout">
      <SectionTopbarPortal
        target={headerPortalTarget}
        fallback={<div className="section-page-header-fallback">{headerContent}</div>}
      >
        {headerContent}
      </SectionTopbarPortal>

      <div className="marketplace-shell">
        {!selectedSkill ? (
          <>
            <div className="marketplace-toolbar" ref={introRef}>
              <div>
                <h3 className="marketplace-heading">Discover installable capabilities</h3>
                <p className="marketplace-subtitle">
                  Browse local MCP tools, remote agents, knowledge packs, workflows, and channel connectors.
                </p>
              </div>
              <div className="marketplace-toolbar-meta">
                <span>{visibleSkills.length} skills</span>
                <span>{installed.filter((skill) => skill.enabled).length} active</span>
              </div>
            </div>

            <div className={`marketplace-registry-banner${registryUrl && !registryNeedsFunctionKey ? " marketplace-registry-banner-ready" : ""}`}>
              <div className="marketplace-registry-copy">
                <span className="marketplace-registry-dot" />
                <div>
                  <strong>
                    {registryNeedsFunctionKey
                      ? `Registry key missing: ${registryHost}`
                      : registryUrl ? `Registry connected: ${registryHost}` : "Local official skills only"}
                  </strong>
                  <p>
                    {registryNeedsFunctionKey
                      ? "Add the Azure Function key in Settings before refreshing the company catalog."
                      : registryUrl
                      ? "Refresh pulls approved company skills from the configured Skill Registry."
                      : "Configure a Skill Registry URL in Settings to receive company-published skills."}
                  </p>
                </div>
              </div>
              {onOpenMarketplaceSettings ? (
                <button type="button" className="ghost-button" onClick={onOpenMarketplaceSettings}>
                  Configure registry
                </button>
              ) : null}
            </div>

            <div className="marketplace-controls" ref={controlsRef}>
              <div className="runtime-lane-tabs" role="tablist" aria-label="Marketplace views">
                <button
                  type="button"
                  className={`runtime-lane-btn${activeTab === "marketplace" ? " runtime-lane-btn-active" : ""}`}
                  onClick={() => setActiveTab("marketplace")}
                >
                  <span className="runtime-lane-btn-dot" />
                  Marketplace
                </button>
                <button
                  type="button"
                  className={`runtime-lane-btn${activeTab === "installed" ? " runtime-lane-btn-active" : ""}`}
                  onClick={() => setActiveTab("installed")}
                >
                  <span className="runtime-lane-btn-dot" />
                  Installed
                </button>
              </div>

              <div className="skill-filter-row">
                <input
                  className="skill-search-input"
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search marketplace"
                />
                <select
                  className="skill-filter-select"
                  value={kindFilter}
                  onChange={(event) => setKindFilter(event.target.value as SkillKindFilter)}
                >
                  <option value="all">All types</option>
                  {kindOptions.map((kind) => (
                    <option key={kind} value={kind}>{skillKindLabel(kind)}</option>
                  ))}
                </select>
              </div>
            </div>

            {error ? <p className="error-block">{error}</p> : null}

            {isLoading ? (
              <p className="muted-text">Loading marketplace...</p>
            ) : visibleSkills.length === 0 ? (
              <div className="marketplace-empty">
                <h3>No skills found</h3>
                <p>Try a different search or refresh the marketplace catalog.</p>
              </div>
            ) : (
              <div className="marketplace-grid" ref={gridRef}>
                {visibleSkills.map((skill, index) => renderSkillCard(skill, index))}
              </div>
            )}
          </>
        ) : (
          <>
            {error ? <p className="error-block">{error}</p> : null}
            {renderDetail(selectedSkill)}
          </>
        )}
      </div>

      {showTutorial && !selectedSkill ? (
        <div className="marketplace-tour-layer">
          {tourGeometry ? (
            <div
              className="marketplace-tour-spotlight"
              style={{
                top: tourGeometry.top,
                left: tourGeometry.left,
                width: tourGeometry.width,
                height: tourGeometry.height,
              }}
            />
          ) : null}
          <section
            className="marketplace-tour-card"
            role="dialog"
            aria-modal="false"
            aria-labelledby="marketplace-tour-title"
            style={tourGeometry ? { top: tourGeometry.popoverTop, left: tourGeometry.popoverLeft } : undefined}
          >
            <button
              type="button"
              className="marketplace-tour-close"
              aria-label="Close marketplace tutorial"
              onClick={finishTutorial}
            >
              x
            </button>
            <p className="marketplace-tour-step">
              STEP {tutorialStep + 1}/{MARKETPLACE_TUTORIAL_STEPS.length}
            </p>
            <h3 id="marketplace-tour-title" className="marketplace-tour-title">
              {MARKETPLACE_TUTORIAL_STEPS[tutorialStep].title}
            </h3>
            <p className="marketplace-tour-body">
              {MARKETPLACE_TUTORIAL_STEPS[tutorialStep].body}
            </p>
            <div className="marketplace-tour-progress" aria-hidden="true">
              <span style={{ width: `${((tutorialStep + 1) / MARKETPLACE_TUTORIAL_STEPS.length) * 100}%` }} />
            </div>
            <div className="marketplace-tour-actions">
              <button type="button" className="marketplace-tour-secondary" onClick={finishTutorial}>
                Skip
              </button>
              {tutorialStep > 0 ? (
                <button
                  type="button"
                  className="marketplace-tour-secondary"
                  onClick={() => setTutorialStep((current) => Math.max(0, current - 1))}
                >
                  Back
                </button>
              ) : null}
              <button type="button" className="marketplace-tour-primary" onClick={nextTutorialStep}>
                {tutorialStep >= MARKETPLACE_TUTORIAL_STEPS.length - 1 ? "Finish" : "Next"}
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {configSkill ? (
        <div className="hw-modal-backdrop">
          <section className="hw-modal-card skill-config-modal" role="dialog" aria-modal="true">
            <div className="hw-modal-head">
              <div>
                <p className="hw-label">SKILL CONFIG</p>
                <h3 className="hw-modal-title">{configSkill.name}</h3>
              </div>
            </div>
            <div className="skill-config-fields">
              {[
                ...Object.entries(configSkill.config_schema?.properties ?? {}).map(([field, schema]) => ({
                  key: field,
                  title: schema.title ?? field,
                  description: schema.description ?? "",
                  type:
                    schema.format === "password"
                      ? "password"
                      : schema.type === "boolean"
                        ? "boolean"
                      : schema.type === "integer" || schema.type === "number"
                        ? "number"
                        : "text",
                  step: schema.type === "integer" ? 1 : undefined,
                  min: typeof schema.minimum === "number" ? schema.minimum : undefined,
                  max: typeof schema.maximum === "number" ? schema.maximum : undefined,
                  format: schema.format ?? "",
                })),
                ...(configSkill.secrets ?? [])
                  .filter((secret) => !(configSkill.config_schema?.properties?.[secret.field]))
                  .map((secret) => ({
                    key: secret.field,
                    title: secret.title || secret.field,
                    description: secret.description || "",
                    type: "password",
                    step: undefined,
                    min: undefined,
                    max: undefined,
                    format: "",
                  })),
              ].map((field) => (
                <label key={field.key} className={`skill-config-field${field.type === "boolean" ? " skill-config-field-boolean" : ""}`}>
                  <span>{field.title}</span>
                  {field.format === "directory" ? (
                    <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
                      <input
                        className="skill-config-input"
                        type={field.type}
                        value={configDraft[field.key] ?? ""}
                        placeholder={configSkill.config?.[field.key] === "<configured>" ? "<configured>" : ""}
                        onChange={(event) => setConfigDraft((current) => ({ ...current, [field.key]: event.target.value }))}
                        min={field.min}
                        max={field.max}
                        step={field.step}
                        style={{ flex: 1 }}
                      />
                      <button
                        type="button"
                        className="hw-btn-ghost"
                        onClick={() => void handlePickConfigDirectory(field.key, field.title)}
                        disabled={pickingConfigFieldKey === field.key}
                      >
                        {pickingConfigFieldKey === field.key ? "Opening..." : "Choose folder"}
                      </button>
                    </div>
                  ) : field.type === "boolean" ? (
                    <button
                      type="button"
                      className={`skill-config-toggle${isDraftBooleanEnabled(configDraft[field.key]) ? " skill-config-toggle-enabled" : ""}`}
                      role="switch"
                      aria-checked={isDraftBooleanEnabled(configDraft[field.key])}
                      onClick={() => setConfigDraft((current) => ({
                        ...current,
                        [field.key]: isDraftBooleanEnabled(current[field.key]) ? "false" : "true",
                      }))}
                    >
                      <span className="skill-config-toggle-track" aria-hidden="true">
                        <span className="skill-config-toggle-thumb" />
                      </span>
                      <span className="skill-config-toggle-text">
                        {isDraftBooleanEnabled(configDraft[field.key]) ? "Enabled" : "Disabled"}
                      </span>
                    </button>
                  ) : (
                    <input
                      className="skill-config-input"
                      type={field.type}
                      value={configDraft[field.key] ?? ""}
                      placeholder={configSkill.config?.[field.key] === "<configured>" ? "<configured>" : ""}
                      onChange={(event) => setConfigDraft((current) => ({ ...current, [field.key]: event.target.value }))}
                      min={field.min}
                      max={field.max}
                      step={field.step}
                    />
                  )}
                  {field.description ? <small className="skill-config-help">{field.description}</small> : null}
                  {skillConfigContextNote(configSkill, field.key, configDraft) ? (
                    <small className="skill-config-help skill-config-help-accent">
                      {skillConfigContextNote(configSkill, field.key, configDraft)}
                    </small>
                  ) : null}
                  {field.format === "directory" && configPickerError ? <small className="skill-config-help">{configPickerError}</small> : null}
                </label>
              ))}
            </div>
            <div className="hw-modal-actions">
              <button
                type="button"
                className="hw-btn-ghost"
                onClick={closeConfigModal}
                disabled={busySkillId === configSkill.id}
              >
                Cancel
              </button>
              <button
                type="button"
                className="hw-btn-primary"
                onClick={() => void saveConfig()}
                disabled={busySkillId === configSkill.id}
              >
                Save
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {configToastMessage ? (
        <div className="skill-config-toast" role="status" aria-live="polite">
          {configToastMessage}
        </div>
      ) : null}
    </section>
  );
}
