import { isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { useEffect, useMemo, useState } from "react";

import adultMascot from "../../../../img/azulclaw.png";
import babyMascot from "../../../../img/hatching_azulclaw.png";

import { Tooltip } from "../../components/Tooltip";
import {
  connectAzure,
  discoverAzureDeployments,
  discoverAzureKeyVaults,
  discoverAzureKeyVaultSecrets,
  discoverAzureResources,
  discoverAzureSubscriptions,
  hydrateAzureKeyVaultSecrets,
  saveHatching,
} from "../../lib/api";
import { AZURE_ARM_SCOPE, loginWithMicrosoft, loginWithMicrosoftForAzure, loginWithMicrosoftForKeyVault } from "../../lib/azure-auth";
import { clearAzureOpenAiApiKey, loadAzureOpenAiApiKey } from "../../lib/desktop-secrets";
import type {
  AzureDeploymentOption,
  AzureKeyVaultOption,
  AzureKeyVaultSecretOption,
  AzureOpenAIResourceOption,
  AzureSubscriptionOption,
  HatchingProfile,
} from "../../lib/contracts";
import { defaultHatchingProfile } from "../../lib/mock-data";

interface HatchingShellProps {
  profile?: HatchingProfile;
  onboardingRequired?: boolean;
  forceWizard?: boolean;
  initialStep?: number;
  onProfileSaved?: (profile: HatchingProfile) => void;
}

type StepType = "text" | "textarea" | "azure" | "skills" | "superpowers" | "path";
type NavDir = "forward" | "back";
type SkillFieldType = "text" | "password" | "url";

interface WizardQuestion {
  id: string;
  title: string;
  helper: string;
  placeholder: string;
  type: StepType;
  emoji: string;
}

interface SkillField {
  id: string;
  label: string;
  helper: string;
  placeholder: string;
  type: SkillFieldType;
}

interface SkillDefinition {
  id: string;
  title: string;
  description: string;
  fields: SkillField[];
}

interface WizardState {
  answers: string[];
  azureConfig: AzureConfig;
  configuredSkills: string[];
  skillConfigs: Record<string, Record<string, string>>;
  workspaceRoot: string;
  confirmSensitiveActions: boolean;
}

interface AzureConfig {
  tenantId: string;
  clientId: string;
  accountKind: "work" | "personal";
  mode: "guided" | "manual";
  authMethod: "entra" | "api_key";
  apiKey: string;
  apiKeyStored: boolean;
  endpoint: string;
  deployment: string;
  fastDeployment: string;
  embeddingDeployment: string;
  connected: boolean;
  lastConnectedAt: string;
  subscriptionId: string;
  resourceGroup: string;
  accountName: string;
  keyVaultName: string;
  keyVaultResourceGroup: string;
  keyVaultUrl: string;
  microsoftAppIdSecretName: string;
  microsoftAppPasswordSecretName: string;
  microsoftAppTenantIdSecretName: string;
}

const wizardQuestions: WizardQuestion[] = [
  { id: "NAME", title: "What do you want to call your AzulClaw?", helper: "That will be its name in the app. You can change it whenever you like.", placeholder: "AzulClaw, Atlas, Clawy...", type: "text", emoji: "Paw" },
  { id: "ROLE", title: "What do you want it to be for you?", helper: "Answer freely. There is no wrong answer.", placeholder: "I want you to be my technical assistant for organising tasks, code and decisions.", type: "textarea", emoji: "Role" },
  { id: "MISSION", title: "What should its main mission be?", helper: "This defines where it should focus when working with you.", placeholder: "Help me move forward with focus, context and order.", type: "textarea", emoji: "Mission" },
  { id: "CHARACTER", title: "How do you want it to talk and act?", helper: "Describe its tone, style and how much autonomy you want to give it.", placeholder: "Direct, clear, technical and proactive, but confirm sensitive actions.", type: "textarea", emoji: "Tone" },
  { id: "AZURE", title: "Connect your Azure", helper: "Sign in with Microsoft so AzulClaw can use your Azure resources without local API keys.", placeholder: "", type: "azure", emoji: "Azure" },
  { id: "CAPABILITIES", title: "Which integrations do you want to activate?", helper: "Connect external tools like email or messaging. Click an integration to configure it — it activates once you complete the setup.", placeholder: "", type: "skills", emoji: "Skills" },
  { id: "SUPERPOWERS", title: "Give it superpowers", helper: "", placeholder: "", type: "superpowers", emoji: "Power" },
  { id: "WORKSPACE", title: "Which folder will be its workspace?", helper: "Pick a folder: file tools use it as the sandbox, and persistent memory (SQLite) is stored in a .azul subfolder inside it. You can change the memory path later from Settings.", placeholder: "~/Documents/dev/AzulWorkspace", type: "path", emoji: "Desk" },
];

const SKILL_CATALOG: SkillDefinition[] = [
  {
    id: "Email",
    title: "Email",
    description: "Read and prepare emails from a connected account.",
    fields: [
      { id: "provider", label: "Provider", helper: "Example: Gmail or Outlook.", placeholder: "Gmail", type: "text" },
      { id: "email", label: "Email address", helper: "Main address AzulClaw will use.", placeholder: "you@domain.com", type: "text" },
      { id: "token", label: "Token or app password", helper: "Credential needed to authenticate access.", placeholder: "Paste your credential here", type: "password" },
    ],
  },
  {
    id: "Telegram",
    title: "Telegram",
    description: "Send notifications and operate through a bot.",
    fields: [
      { id: "botToken", label: "Bot token", helper: "Token generated by BotFather.", placeholder: "123456:ABC...", type: "password" },
      { id: "chatId", label: "Chat ID", helper: "Chat or user authorised to talk with AzulClaw.", placeholder: "987654321", type: "text" },
    ],
  },
  {
    id: "Slack",
    title: "Slack",
    description: "Post messages and handle events in work channels.",
    fields: [
      { id: "workspaceUrl", label: "Workspace URL", helper: "Workspace base URL.", placeholder: "https://my-team.slack.com", type: "url" },
      { id: "botToken", label: "Bot token", helper: "Bot token with the required permissions.", placeholder: "xoxb-...", type: "password" },
      { id: "defaultChannel", label: "Default channel", helper: "Main channel where it can write.", placeholder: "#azulclaw", type: "text" },
    ],
  },
  {
    id: "Alexa",
    title: "Alexa",
    description: "Connect voice actions and home routines.",
    fields: [
      { id: "skillId", label: "Skill ID", helper: "Skill or integration identifier.", placeholder: "amzn1.ask.skill...", type: "text" },
      { id: "clientSecret", label: "Client secret", helper: "Credential used to validate requests.", placeholder: "Paste your secret here", type: "password" },
    ],
  },
];

const SKILL_IDS = new Set(SKILL_CATALOG.map((skill) => skill.id));

/** Matches backend: ``<workspace>/.azul/azul_memory.db``. */
function previewMemoryDbPath(workspaceRoot: string): string {
  const trimmed = workspaceRoot.trim();
  if (!trimmed) return "";
  const sep = trimmed.includes("\\") ? "\\" : "/";
  return `${trimmed.replace(/[/\\]$/, "")}${sep}.azul${sep}azul_memory.db`;
}

function buildTextAnswers(profile: HatchingProfile) {
  return [profile.name, profile.role, profile.mission, [profile.tone, profile.style, profile.autonomy].filter(Boolean).join(", ")];
}

function buildAzureConfig(profile: HatchingProfile): AzureConfig {
  const saved = profile.skill_configs?.Azure ?? {};
  return {
    tenantId: saved.tenantId ?? "",
    clientId: saved.clientId ?? "",
    accountKind: saved.accountKind === "personal" ? "personal" : "work",
    mode: saved.mode === "manual" ? "manual" : "guided",
    authMethod: saved.authMethod === "api_key" ? "api_key" : "entra",
    apiKey: saved.apiKey ?? "",
    apiKeyStored: saved.apiKeyStored === "true" || Boolean(saved.apiKey),
    endpoint: saved.endpoint ?? "",
    deployment: saved.deployment ?? "gpt-4o",
    fastDeployment: saved.fastDeployment ?? "gpt-4o-mini",
    embeddingDeployment: saved.embeddingDeployment ?? "text-embedding-3-large",
    connected: saved.connected === "true",
    lastConnectedAt: saved.lastConnectedAt ?? "",
    subscriptionId: saved.subscriptionId ?? "",
    resourceGroup: saved.resourceGroup ?? "",
    accountName: saved.accountName ?? "",
    keyVaultName: saved.keyVaultName ?? "",
    keyVaultResourceGroup: saved.keyVaultResourceGroup ?? "",
    keyVaultUrl: saved.keyVaultUrl ?? saved.keyVaultUri ?? "",
    microsoftAppIdSecretName: saved.microsoftAppIdSecretName ?? "",
    microsoftAppPasswordSecretName: saved.microsoftAppPasswordSecretName ?? "",
    microsoftAppTenantIdSecretName: saved.microsoftAppTenantIdSecretName ?? "",
  };
}

function serializeAzureConfig(config: AzureConfig): Record<string, string> {
  return {
    tenantId: config.tenantId.trim(),
    clientId: config.clientId.trim(),
    accountKind: config.accountKind,
    mode: config.mode,
    authMethod: config.authMethod,
    apiKey: config.apiKey.trim(),
    apiKeyStored: config.apiKeyStored ? "true" : "false",
    endpoint: config.endpoint.trim(),
    deployment: config.deployment.trim(),
    fastDeployment: config.fastDeployment.trim(),
    embeddingDeployment: config.embeddingDeployment.trim(),
    connected: config.connected ? "true" : "false",
    lastConnectedAt: config.lastConnectedAt,
    subscriptionId: config.subscriptionId.trim(),
    resourceGroup: config.resourceGroup.trim(),
    accountName: config.accountName.trim(),
    keyVaultName: config.keyVaultName.trim(),
    keyVaultResourceGroup: config.keyVaultResourceGroup.trim(),
    keyVaultUrl: config.keyVaultUrl.trim().replace(/\/$/, ""),
    microsoftAppIdSecretName: config.microsoftAppIdSecretName.trim(),
    microsoftAppPasswordSecretName: config.microsoftAppPasswordSecretName.trim(),
    microsoftAppTenantIdSecretName: config.microsoftAppTenantIdSecretName.trim(),
  };
}

function buildWizardState(profile: HatchingProfile, onboardingRequired: boolean): WizardState {
  return {
    answers: buildTextAnswers(profile),
    azureConfig: buildAzureConfig(profile),
    configuredSkills: onboardingRequired ? [] : profile.skills.filter((skill) => SKILL_IDS.has(skill)),
    skillConfigs: onboardingRequired ? {} : profile.skill_configs,
    workspaceRoot: profile.workspace_root,
    confirmSensitiveActions: profile.confirm_sensitive_actions,
  };
}



function deriveTone(value: string, fallback: string) {
  const v = value.toLowerCase();
  if (v.includes("serious") || v.includes("formal")) return "Serious";
  if (v.includes("warm") || v.includes("friendly") || v.includes("casual")) return "Warm";
  if (v.includes("direct") || v.includes("clear") || v.includes("concise")) return "Direct";
  return fallback || "Direct";
}

function deriveStyle(value: string, fallback: string) {
  const v = value.toLowerCase();
  if (v.includes("brief") || v.includes("short") || v.includes("concise")) return "Brief";
  if (v.includes("technical") || v.includes("deep") || v.includes("detailed")) return "Technical";
  if (v.includes("explain") || v.includes("detail") || v.includes("thorough")) return "Explanatory";
  return fallback || "Explanatory";
}

function deriveAutonomy(value: string, fallback: string) {
  const v = value.toLowerCase();
  if (v.includes("confirm") || v.includes("ask") || v.includes("cautious") || v.includes("check")) return "Confirmatory";
  if (v.includes("autonomo") || v.includes("autonomous") || v.includes("initiative") || v.includes("proactive")) return "High autonomy";
  return fallback || "Moderately autonomous";
}

function buildProfileFromWizard(base: HatchingProfile, state: WizardState): HatchingProfile {
  const [name, role, mission, temper] = state.answers;
  const nextSkillConfigs = {
    ...state.skillConfigs,
    Azure: serializeAzureConfig(state.azureConfig),
  };
  return {
    ...base,
    name: name.trim() || base.name,
    role: role.trim() || base.role,
    mission: mission.trim() || base.mission,
    tone: deriveTone(temper, base.tone),
    style: deriveStyle(temper, base.style),
    autonomy: deriveAutonomy(temper, base.autonomy),

    workspace_root: state.workspaceRoot.trim() || base.workspace_root,
    confirm_sensitive_actions: state.confirmSensitiveActions,
    skills: state.configuredSkills,
    skill_configs: nextSkillConfigs,
  };
}

function getStepEmoji(label: string) {
  return label;
}

function chooseDeploymentByCapability(
  deployments: AzureDeploymentOption[],
  capability: "main" | "fast" | "embedding",
): string {
  const exact = deployments.find((item) => item.capabilities.includes(capability));
  if (exact) return exact.name;
  if (capability === "embedding") {
    return deployments.find((item) => item.model_name.toLowerCase().includes("embedding"))?.name ?? "";
  }
  if (capability === "fast") {
    return deployments.find((item) => item.model_name.toLowerCase().includes("mini"))?.name ?? deployments[0]?.name ?? "";
  }
  return deployments.find((item) => !item.model_name.toLowerCase().includes("embedding"))?.name ?? deployments[0]?.name ?? "";
}

function chooseSecretName(
  secrets: AzureKeyVaultSecretOption[],
  currentValue: string,
  envKey: string,
): string {
  const enabled = secrets.filter((item) => item.enabled);
  if (currentValue && enabled.some((item) => item.name === currentValue)) {
    return currentValue;
  }
  const hyphenName = envKey.replace(/_/g, "-");
  return (
    enabled.find((item) => item.name === envKey)?.name ??
    enabled.find((item) => item.name === hyphenName)?.name ??
    ""
  );
}

export function HatchingShell({
  profile: incomingProfile,
  onboardingRequired = false,
  forceWizard = false,
  initialStep = 0,
  onProfileSaved,
}: HatchingShellProps) {
  const initial = incomingProfile ?? defaultHatchingProfile;
  const initialState = buildWizardState(initial, onboardingRequired);
  const initialCurrentStep = Math.min(Math.max(initialStep, 0), wizardQuestions.length);

  const [profile, setProfile] = useState<HatchingProfile>(initial);
  const [answers, setAnswers] = useState<string[]>(initialState.answers);
  const [azureConfig, setAzureConfig] = useState<AzureConfig>(initialState.azureConfig);
  const [azureManagementToken, setAzureManagementToken] = useState("");
  const [azureKeyVaultToken, setAzureKeyVaultToken] = useState("");
  const [azureBusy, setAzureBusy] = useState(false);
  const [azureDiscoveryBusy, setAzureDiscoveryBusy] = useState(false);
  const [azureError, setAzureError] = useState("");
  const [azureSubscriptions, setAzureSubscriptions] = useState<AzureSubscriptionOption[]>([]);
  const [azureResources, setAzureResources] = useState<AzureOpenAIResourceOption[]>([]);
  const [azureKeyVaults, setAzureKeyVaults] = useState<AzureKeyVaultOption[]>([]);
  const [azureKeyVaultSecrets, setAzureKeyVaultSecrets] = useState<AzureKeyVaultSecretOption[]>([]);
  const [azureDeployments, setAzureDeployments] = useState<AzureDeploymentOption[]>([]);
  const [configuredSkills, setConfiguredSkills] = useState<string[]>(initialState.configuredSkills);
  const [skillConfigs, setSkillConfigs] = useState<Record<string, Record<string, string>>>(initialState.skillConfigs);
  const [workspaceRoot, setWorkspaceRoot] = useState(initialState.workspaceRoot);
  const [confirmSensitiveActions, setConfirmSensitiveActions] = useState(initialState.confirmSensitiveActions);
  const [currentStep, setCurrentStep] = useState(initialCurrentStep);
  const [isSaving, setIsSaving] = useState(false);
  const [isPreparing, setIsPreparing] = useState(false);
  const [isAllSet, setIsAllSet] = useState(false);
  const [isExiting, setIsExiting] = useState(false);
  const [navDir, setNavDir] = useState<NavDir>("forward");
  const [isPickingWorkspace, setIsPickingWorkspace] = useState(false);
  const [workspacePickerError, setWorkspacePickerError] = useState("");
  const [activeSkillId, setActiveSkillId] = useState<string | null>(null);
  const [skillDraft, setSkillDraft] = useState<Record<string, string>>({});
  const [skillModalError, setSkillModalError] = useState("");
  const [showAzureSkipWarning, setShowAzureSkipWarning] = useState(false);
  const [showApiKeyModeWarning, setShowApiKeyModeWarning] = useState(false);
  const [showApiKeyConnectWarning, setShowApiKeyConnectWarning] = useState(false);
  const [editingStoredApiKey, setEditingStoredApiKey] = useState(false);

  async function ensureAzureKeyVaultToken(): Promise<string> {
    if (azureKeyVaultToken) {
      return azureKeyVaultToken;
    }
    const login = await loginWithMicrosoftForKeyVault({
      tenantId: azureConfig.tenantId,
      clientId: azureConfig.clientId,
    });
    setAzureKeyVaultToken(login.accessToken);
    return login.accessToken;
  }

  useEffect(() => {
    setCurrentStep(Math.min(Math.max(initialStep, 0), wizardQuestions.length));
  }, [initialStep]);

  useEffect(() => {
    if (!incomingProfile) return;
    const nextState = buildWizardState(incomingProfile, onboardingRequired);
    setProfile(incomingProfile);
    setAnswers(nextState.answers);
    setAzureConfig(nextState.azureConfig);
    setAzureManagementToken("");
    setAzureKeyVaultToken("");
    setAzureSubscriptions([]);
    setAzureResources([]);
    setAzureKeyVaults([]);
    setAzureKeyVaultSecrets([]);
    setAzureDeployments([]);
    setAzureError("");
    setConfiguredSkills(nextState.configuredSkills);
    setSkillConfigs(nextState.skillConfigs);
    setWorkspaceRoot(nextState.workspaceRoot);
    setConfirmSensitiveActions(nextState.confirmSensitiveActions);
    setEditingStoredApiKey(false);
  }, [incomingProfile, onboardingRequired]);

  useEffect(() => {
    if (!azureManagementToken || !azureConfig.subscriptionId || azureConfig.mode !== "guided") {
      return;
    }
    void handleLoadAzureResources(azureConfig.subscriptionId);
  }, [azureManagementToken, azureConfig.subscriptionId, azureConfig.mode]);

  useEffect(() => {
    if (
      !azureManagementToken ||
      azureConfig.mode !== "guided" ||
      !azureConfig.subscriptionId ||
      !azureConfig.resourceGroup ||
      !azureConfig.accountName
    ) {
      return;
    }
    const resource = azureResources.find(
      (item) =>
        item.subscription_id === azureConfig.subscriptionId &&
        item.resource_group === azureConfig.resourceGroup &&
        item.name === azureConfig.accountName,
    );
    if (resource) {
      void handleLoadAzureDeployments(resource);
    }
  }, [
    azureManagementToken,
    azureConfig.accountName,
    azureConfig.mode,
    azureConfig.resourceGroup,
    azureConfig.subscriptionId,
    azureResources,
  ]);

  const draftProfile = useMemo(
    () => buildProfileFromWizard(profile, { answers, azureConfig, configuredSkills, skillConfigs, workspaceRoot, confirmSensitiveActions }),
    [answers, azureConfig, configuredSkills, confirmSensitiveActions, profile, skillConfigs, workspaceRoot],
  );

  const isFinalStep = currentStep === wizardQuestions.length;
  const activeQuestion = wizardQuestions[currentStep];
  const stepNumber = Math.min(currentStep + 1, wizardQuestions.length);
  const mascotImage = onboardingRequired && !draftProfile.is_hatched ? babyMascot : adultMascot;
  const activeSkill = activeSkillId ? SKILL_CATALOG.find((skill) => skill.id === activeSkillId) ?? null : null;
  const azureDiscoveryReady = Boolean(azureManagementToken && azureSubscriptions.length > 0);
  const hasAzureDeployments = azureDeployments.length > 0;
  const azureTenantOptions = useMemo(() => {
    const tenants = new Map<string, string>();
    azureSubscriptions.forEach((subscription) => {
      const tenantId = subscription.tenant_id?.trim();
      if (!tenantId) return;
      tenants.set(tenantId, tenantId);
    });
    return Array.from(tenants, ([id, label]) => ({ id, label }));
  }, [azureSubscriptions]);

  function navigate(toStep: number, dir: NavDir) {
    if (isExiting || activeSkill) return;
    setNavDir(dir);
    setIsExiting(true);
    setTimeout(() => {
      setCurrentStep(toStep);
      setIsExiting(false);
    }, 260);
  }

  function handleNext() {
    if (activeQuestion?.type === "azure" && !azureConfig.connected) {
      setShowAzureSkipWarning(true);
      return;
    }
    navigate(Math.min(currentStep + 1, wizardQuestions.length), "forward");
  }

  function confirmAzureSkip() {
    setShowAzureSkipWarning(false);
    navigate(Math.min(currentStep + 1, wizardQuestions.length), "forward");
  }

  function handleBack() {
    navigate(Math.max(currentStep - 1, 0), "back");
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Enter" || event.shiftKey || isExiting || isFinalStep || activeSkill) return;
      const target = event.target as HTMLElement | null;
      if (!target || target.tagName === "TEXTAREA" || target.tagName === "BUTTON") return;
      event.preventDefault();
      handleNext();
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeSkill, isExiting, isFinalStep, currentStep]);

  function handleAnswerChange(value: string) {
    setAnswers((current) => current.map((item, index) => (index === currentStep ? value : item)));
  }

  function handleAzureConfigChange(field: keyof AzureConfig, value: string) {
    setAzureConfig((current) => ({
      ...current,
      [field]: value,
      connected: field === "lastConnectedAt" ? current.connected : false,
      lastConnectedAt: field === "lastConnectedAt" ? value : "",
    }));
    if (field === "subscriptionId") {
      setAzureResources([]);
      setAzureKeyVaults([]);
      setAzureKeyVaultSecrets([]);
      setAzureDeployments([]);
    }
    if (field === "accountName" || field === "resourceGroup") {
      setAzureDeployments([]);
    }
    setAzureError("");
  }

  function enableApiKeyMode() {
    setAzureConfig((current) => ({
      ...current,
      authMethod: "api_key",
      connected: false,
      lastConnectedAt: "",
    }));
    setShowApiKeyModeWarning(false);
    setAzureError("");
  }

  function handleSelectApiKeyMode() {
    if (azureConfig.authMethod === "api_key") return;
    setShowApiKeyModeWarning(true);
  }

  async function handleClearStoredApiKey() {
    await clearAzureOpenAiApiKey();
    setAzureConfig((current) => ({
      ...current,
      apiKey: "",
      apiKeyStored: false,
      connected: false,
      lastConnectedAt: "",
    }));
    setEditingStoredApiKey(false);
    setAzureError("");
  }

  function handleAzureSubscriptionChange(subscriptionId: string) {
    const selected = azureSubscriptions.find((item) => item.id === subscriptionId);
    setAzureConfig((current) => ({
      ...current,
      subscriptionId,
      tenantId: selected?.tenant_id?.trim() || current.tenantId,
      connected: false,
      lastConnectedAt: "",
    }));
    setAzureResources([]);
    setAzureKeyVaults([]);
    setAzureKeyVaultSecrets([]);
    setAzureDeployments([]);
    setAzureError("");
  }

  async function handleStartAzureDiscovery() {
    setAzureError("");
    if (!azureConfig.clientId.trim()) {
      setAzureError("Enter the Azure app registration client ID.");
      return;
    }
    if (azureConfig.accountKind === "personal" && !azureConfig.tenantId.trim()) {
      setAzureError("Paste the Directory (tenant) ID for the Azure directory that contains your subscription.");
      return;
    }
    setAzureDiscoveryBusy(true);
    try {
      const login = await loginWithMicrosoft(
        {
          tenantId: azureConfig.accountKind === "personal" ? azureConfig.tenantId : azureConfig.tenantId || "organizations",
          clientId: azureConfig.clientId,
        },
        {
          scope: AZURE_ARM_SCOPE,
        },
      );
      const subscriptions = await discoverAzureSubscriptions(login.accessToken);
      setAzureManagementToken(login.accessToken);
      setAzureKeyVaultToken("");
      setAzureSubscriptions(subscriptions);
      const nextTenantId = subscriptions[0]?.tenant_id?.trim() || azureConfig.tenantId;
      const nextSubscriptionId =
        azureConfig.subscriptionId && subscriptions.some((item) => item.id === azureConfig.subscriptionId)
          ? azureConfig.subscriptionId
          : subscriptions[0]?.id ?? "";
      setAzureConfig((current) => ({
        ...current,
        tenantId: nextTenantId,
        subscriptionId: nextSubscriptionId,
        connected: false,
        lastConnectedAt: "",
      }));
    } catch (error) {
      setAzureError(error instanceof Error ? error.message : String(error));
    } finally {
      setAzureDiscoveryBusy(false);
    }
  }

  async function handleLoadAzureResources(subscriptionId: string) {
    const safeSubscriptionId = subscriptionId.trim();
    if (!azureManagementToken || !safeSubscriptionId) {
      return;
    }
    setAzureDiscoveryBusy(true);
    setAzureError("");
    try {
      const [resources, keyVaults] = await Promise.all([
        discoverAzureResources(azureManagementToken, safeSubscriptionId),
        discoverAzureKeyVaults(azureManagementToken, safeSubscriptionId),
      ]);
      setAzureResources(resources);
      setAzureKeyVaults(keyVaults);
      const selected =
        resources.find((item) => item.name === azureConfig.accountName && item.resource_group === azureConfig.resourceGroup) ??
        resources[0];
      const selectedVault =
        keyVaults.find((item) => item.name === azureConfig.keyVaultName && item.resource_group === azureConfig.keyVaultResourceGroup) ??
        keyVaults[0];
      let secrets: AzureKeyVaultSecretOption[] = [];
      if (selectedVault) {
        try {
          const keyVaultToken = await ensureAzureKeyVaultToken();
          secrets = await discoverAzureKeyVaultSecrets(
            keyVaultToken,
            selectedVault.vault_uri,
          );
        } catch (error) {
          setAzureError(
            `Key Vault selected, but secret discovery failed. Verify Azure ARM permissions, Key Vault access policy/RBAC, and network/API access, or type the secret names manually. ${error instanceof Error ? error.message : String(error)}`,
          );
        }
      }
      setAzureKeyVaultSecrets(secrets);
      setAzureConfig((current) => ({
        ...current,
        subscriptionId: safeSubscriptionId,
        endpoint: selected?.endpoint ?? current.endpoint,
        resourceGroup: selected?.resource_group ?? current.resourceGroup,
        accountName: selected?.name ?? current.accountName,
        keyVaultName: selectedVault?.name ?? "",
        keyVaultResourceGroup: selectedVault?.resource_group ?? "",
        keyVaultUrl: selectedVault?.vault_uri ?? "",
        microsoftAppIdSecretName: chooseSecretName(secrets, current.microsoftAppIdSecretName, "MicrosoftAppId"),
        microsoftAppPasswordSecretName: chooseSecretName(secrets, current.microsoftAppPasswordSecretName, "MicrosoftAppPassword"),
        microsoftAppTenantIdSecretName: chooseSecretName(secrets, current.microsoftAppTenantIdSecretName, "MicrosoftAppTenantId"),
        connected: false,
        lastConnectedAt: "",
      }));
      setAzureDeployments([]);
    } catch (error) {
      setAzureError(error instanceof Error ? error.message : String(error));
    } finally {
      setAzureDiscoveryBusy(false);
    }
  }

  async function handleLoadAzureKeyVaultSecrets(vault: AzureKeyVaultOption) {
    if (!azureManagementToken) {
      return;
    }
    setAzureDiscoveryBusy(true);
    setAzureError("");
    try {
      const keyVaultToken = await ensureAzureKeyVaultToken();
      const secrets = await discoverAzureKeyVaultSecrets(
        keyVaultToken,
        vault.vault_uri,
      );
      setAzureKeyVaultSecrets(secrets);
      setAzureConfig((current) => ({
        ...current,
        keyVaultName: vault.name,
        keyVaultResourceGroup: vault.resource_group,
        keyVaultUrl: vault.vault_uri,
        microsoftAppIdSecretName: chooseSecretName(secrets, current.microsoftAppIdSecretName, "MicrosoftAppId"),
        microsoftAppPasswordSecretName: chooseSecretName(secrets, current.microsoftAppPasswordSecretName, "MicrosoftAppPassword"),
        microsoftAppTenantIdSecretName: chooseSecretName(secrets, current.microsoftAppTenantIdSecretName, "MicrosoftAppTenantId"),
        connected: false,
        lastConnectedAt: "",
      }));
    } catch (error) {
      setAzureKeyVaultSecrets([]);
      setAzureConfig((current) => ({
        ...current,
        keyVaultName: vault.name,
        keyVaultResourceGroup: vault.resource_group,
        keyVaultUrl: vault.vault_uri,
        connected: false,
        lastConnectedAt: "",
      }));
      setAzureError(
        `Key Vault selected, but secret discovery failed. Verify Azure ARM permissions, Key Vault access policy/RBAC, and network/API access, or type the secret names manually. ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setAzureDiscoveryBusy(false);
    }
  }

  async function handleLoadAzureDeployments(resource: AzureOpenAIResourceOption) {
    if (!azureManagementToken) {
      return;
    }
    setAzureDiscoveryBusy(true);
    setAzureError("");
    try {
      const deployments = await discoverAzureDeployments(
        azureManagementToken,
        resource.subscription_id,
        resource.resource_group,
        resource.name,
      );
      setAzureDeployments(deployments);
      setAzureConfig((current) => ({
        ...current,
        subscriptionId: resource.subscription_id,
        resourceGroup: resource.resource_group,
        accountName: resource.name,
        endpoint: resource.endpoint,
        deployment: deployments.some((item) => item.name === current.deployment)
          ? current.deployment
          : chooseDeploymentByCapability(deployments, "main"),
        fastDeployment: deployments.some((item) => item.name === current.fastDeployment)
          ? current.fastDeployment
          : chooseDeploymentByCapability(deployments, "fast"),
        embeddingDeployment: deployments.some((item) => item.name === current.embeddingDeployment)
          ? current.embeddingDeployment
          : chooseDeploymentByCapability(deployments, "embedding"),
        connected: false,
        lastConnectedAt: "",
      }));
    } catch (error) {
      setAzureError(error instanceof Error ? error.message : String(error));
    } finally {
      setAzureDiscoveryBusy(false);
    }
  }

  async function handleConnectAzure(skipApiKeyWarning = false) {
    setAzureError("");
    const endpoint = azureConfig.endpoint.trim().replace(/\/$/, "");
    const authMethod = azureConfig.mode === "manual" ? azureConfig.authMethod : "entra";
    if (authMethod === "api_key" && !skipApiKeyWarning) {
      setShowApiKeyConnectWarning(true);
      return;
    }
    if (authMethod === "entra" && !azureConfig.clientId.trim()) {
      setAzureError("Enter the Azure app registration client ID.");
      return;
    }
    if (!endpoint) {
      setAzureError("Select an Azure OpenAI resource or enter the endpoint manually.");
      return;
    }
    if (!azureConfig.deployment.trim()) {
      setAzureError("Choose the main deployment.");
      return;
    }

    setAzureBusy(true);
    try {
      if (authMethod === "entra") {
        const login = await loginWithMicrosoftForAzure({
          tenantId: azureConfig.tenantId,
          clientId: azureConfig.clientId,
        });
        if (azureConfig.keyVaultUrl.trim()) {
          const keyVaultLogin = await loginWithMicrosoftForKeyVault({
            tenantId: azureConfig.tenantId,
            clientId: azureConfig.clientId,
          });
          await hydrateAzureKeyVaultSecrets({
            key_vault_url: azureConfig.keyVaultUrl.trim().replace(/\/$/, ""),
            access_token: keyVaultLogin.accessToken,
            expires_on: keyVaultLogin.expiresOn,
            microsoft_app_id_secret_name: azureConfig.microsoftAppIdSecretName.trim(),
            microsoft_app_password_secret_name: azureConfig.microsoftAppPasswordSecretName.trim(),
            microsoft_app_tenant_id_secret_name: azureConfig.microsoftAppTenantIdSecretName.trim(),
          });
        }
        const connectedAt = new Date().toISOString();
        await connectAzure({
          auth_mode: "entra",
          tenant_id: azureConfig.tenantId.trim(),
          client_id: azureConfig.clientId.trim(),
          endpoint,
          deployment: azureConfig.deployment.trim(),
          fast_deployment: azureConfig.fastDeployment.trim(),
          embedding_deployment: azureConfig.embeddingDeployment.trim(),
          key_vault_url: azureConfig.keyVaultUrl.trim().replace(/\/$/, ""),
          access_token: login.accessToken,
          expires_on: login.expiresOn,
          scope: login.scope,
        });
        setAzureConfig((current) => ({
          ...current,
          endpoint,
          connected: true,
          lastConnectedAt: connectedAt,
        }));
      } else {
        let apiKey = azureConfig.apiKey.trim();
        if (!apiKey && azureConfig.apiKeyStored && !editingStoredApiKey) {
          apiKey = (await loadAzureOpenAiApiKey()) ?? "";
        }
        if (!apiKey) {
          setAzureError("Enter the Azure OpenAI API key.");
          return;
        }
        const connectedAt = new Date().toISOString();
        await connectAzure({
          auth_mode: "api_key",
          tenant_id: "",
          client_id: "",
          endpoint,
          deployment: azureConfig.deployment.trim(),
          fast_deployment: azureConfig.fastDeployment.trim(),
          embedding_deployment: azureConfig.embeddingDeployment.trim(),
          api_key: apiKey,
        });
        setAzureConfig((current) => ({
          ...current,
          apiKey: "",
          apiKeyStored: true,
          endpoint,
          keyVaultUrl: "",
          keyVaultName: "",
          keyVaultResourceGroup: "",
          microsoftAppIdSecretName: "",
          microsoftAppPasswordSecretName: "",
          microsoftAppTenantIdSecretName: "",
          connected: true,
          lastConnectedAt: connectedAt,
        }));
        setEditingStoredApiKey(false);
      }
    } catch (error) {
      setAzureError(error instanceof Error ? error.message : String(error));
    } finally {
      setAzureBusy(false);
    }
  }

  function openSkillConfig(skillId: string) {
    setActiveSkillId(skillId);
    setSkillDraft(skillConfigs[skillId] ?? {});
    setSkillModalError("");
  }

  function closeSkillConfig() {
    setActiveSkillId(null);
    setSkillDraft({});
    setSkillModalError("");
  }

  function handleSkillFieldChange(fieldId: string, value: string) {
    setSkillDraft((current) => ({ ...current, [fieldId]: value }));
  }

  function saveSkillConfig() {
    if (!activeSkill) return;
    const missingField = activeSkill.fields.find((field) => !skillDraft[field.id]?.trim());
    if (missingField) {
      setSkillModalError(`Please complete "${missingField.label}".`);
      return;
    }

    setSkillConfigs((current) => ({ ...current, [activeSkill.id]: skillDraft }));
    setConfiguredSkills((current) => (current.includes(activeSkill.id) ? current : [...current, activeSkill.id]));
    closeSkillConfig();
  }

  function deactivateSkill(skillId: string) {
    setConfiguredSkills((current) => current.filter((item) => item !== skillId));
    setSkillConfigs((current) => {
      const next = { ...current };
      delete next[skillId];
      return next;
    });
    closeSkillConfig();
  }

  async function handlePickWorkspace() {
    setWorkspacePickerError("");
    if (!isTauri()) {
      setWorkspacePickerError("The native selector is only available inside the Tauri desktop app.");
      return;
    }

    setIsPickingWorkspace(true);
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        defaultPath: workspaceRoot || undefined,
        title: "Select the AzulClaw workspace folder",
      });
      if (typeof selected === "string" && selected.trim()) setWorkspaceRoot(selected);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Workspace picker failed", error);
      setWorkspacePickerError(`Could not open the native selector. ${detail}`);
    } finally {
      setIsPickingWorkspace(false);
    }
  }

  async function handleSave(markAsHatched: boolean) {
    setIsSaving(true);
    try {
      const saved = await saveHatching({ ...draftProfile, is_hatched: markAsHatched || profile.is_hatched });
      const nextState = buildWizardState(saved, false);
      setProfile(saved);
      setAnswers(nextState.answers);
      setAzureConfig(nextState.azureConfig);
      setConfiguredSkills(nextState.configuredSkills);
      setSkillConfigs(nextState.skillConfigs);
      setWorkspaceRoot(nextState.workspaceRoot);
      setConfirmSensitiveActions(nextState.confirmSensitiveActions);

      if (markAsHatched && onboardingRequired) {
        setIsPreparing(true);
        setTimeout(() => {
          setIsAllSet(true);
          setTimeout(() => {
            onProfileSaved?.(saved);
          }, 2000);
        }, 2000);
        return;
      }

      onProfileSaved?.(saved);
    } finally {
      setIsSaving(false);
    }
  }

  const contentAnim = isExiting
    ? navDir === "forward" ? "hw-exit-fwd" : "hw-exit-back"
    : navDir === "forward" ? "hw-enter-fwd" : "hw-enter-back";

  const shellClass = onboardingRequired ? "hw-fullscreen" : "hw-contained card";
  const canSkipStep = (activeQuestion?.type === "skills" && configuredSkills.length === 0) || activeQuestion?.type === "superpowers" || (activeQuestion?.type === "azure" && !azureConfig.connected);
  const nextButtonLabel = canSkipStep ? "Skip for now ->" : "Next ->";
  const nextHint = canSkipStep ? "Press Enter to skip for now" : "Press Enter to continue";

  if (isPreparing) {
    return (
      <div style={{ position: "fixed", inset: 0, background: "#020617", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "28px", textAlign: "center", padding: "0 24px" }}>
        <img
          src={adultMascot}
          alt="AzulClaw"
          className="hw-celebrate-img"
          style={{ animation: "hw-pulse 1.4s ease-in-out infinite" }}
        />
        <div>
          <p className="hw-label">PREPARING</p>
          <h1 className="hw-title" style={{ marginBottom: "8px" }}>Setting up your environment</h1>
          <p className="hw-helper">Creating workspace folders, initialising memory database...</p>
        </div>
        {isAllSet && (
          <div style={{ position: "absolute", bottom: "48px", display: "flex", alignItems: "center", gap: "10px", animation: "hwEnterFwd 0.4s ease both" }}>
            <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
              <circle cx="11" cy="11" r="11" fill="#2563eb" />
              <path d="M6 11.5l3.5 3.5 6.5-7" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span style={{ fontSize: "1rem", fontWeight: 600, color: "#e2e8f0", letterSpacing: "0.01em" }}>All set</span>
          </div>
        )}
      </div>
    );
  }

  if (!onboardingRequired && !forceWizard) {
    return (
      <section className="single-panel-layout">
        <div className="card panel-stack">
          <div className="panel-heading" style={{ borderBottom: "1px solid var(--line)", paddingBottom: "20px", marginBottom: "4px" }}>
            <div>
              <p className="eyebrow">Identity</p>
              <h2>Hatching profile</h2>
              <p className="hint-text" style={{ margin: "4px 0 0" }}>Adjust your agent's core personality and environment at any time.</p>
            </div>
            <button type="button" className="primary-button" onClick={() => void handleSave(true)} disabled={isSaving}>
              {isSaving ? "Saving..." : "Apply changes"}
            </button>
          </div>

          <div className="two-column-grid">
            <label className="form-field">
              <span>Agent name</span>
              <input
                type="text"
                value={answers[0] ?? ""}
                onChange={(e) => setAnswers(c => { const n = [...c]; n[0] = e.target.value; return n; })}
              />
            </label>
            <label className="form-field">
              <span>Behaviour and tone</span>
              <input
                type="text"
                value={answers[3] ?? ""}
                onChange={(e) => setAnswers(c => { const n = [...c]; n[3] = e.target.value; return n; })}
              />
            </label>
          </div>

          <div className="two-column-grid">
            <label className="form-field">
              <span>Main role</span>
              <textarea
                rows={4}
                value={answers[1] ?? ""}
                onChange={(e) => setAnswers(c => { const n = [...c]; n[1] = e.target.value; return n; })}
              />
            </label>
            <label className="form-field">
              <span>Mission or goal</span>
              <textarea
                rows={4}
                value={answers[2] ?? ""}
                onChange={(e) => setAnswers(c => { const n = [...c]; n[2] = e.target.value; return n; })}
              />
            </label>
          </div>

          <div className="subcard form-section">
            <p className="eyebrow">Secure workspace</p>
            <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
              <Tooltip text={workspaceRoot} className="workspace-banner-path">
                {workspaceRoot}
              </Tooltip>
              <button type="button" className="ghost-button" onClick={() => void handlePickWorkspace()}>
                Choose folder
              </button>
            </div>
            <label className="form-field" style={{ marginTop: "8px" }}>
              <span>Path</span>
              <input
                type="text"
                value={workspaceRoot}
                onChange={(e) => setWorkspaceRoot(e.target.value)}
                style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}
              />
            </label>
            {workspacePickerError && <p style={{ margin: 0, color: "#fca5a5", fontSize: "0.85rem" }}>{workspacePickerError}</p>}

            <div style={{ marginTop: "8px" }}>
              <p className="eyebrow" style={{ marginBottom: "10px" }}>Sensitive action confirmation</p>
              <div className="filter-row">
                <button type="button" className={confirmSensitiveActions ? "primary-button" : "ghost-button"} onClick={() => setConfirmSensitiveActions(true)}>
                  Ask before acting
                </button>
                <button type="button" className={!confirmSensitiveActions ? "primary-button" : "ghost-button"} onClick={() => setConfirmSensitiveActions(false)}>
                  Full autonomy
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>
    );
  }

  return (
    <div className={shellClass}>
      <header className="hw-header">
        <div className="hw-brand">
          <img className="hw-mascot" src={mascotImage} alt="AzulClaw" />
          <div>
            <p className="hw-eyebrow">Hatching</p>
            <h2 className="hw-brand-title">{onboardingRequired ? "Create your AzulClaw" : "Reconfigure your AzulClaw"}</h2>
          </div>
        </div>
        <div className="hw-dots" aria-label={`Step ${stepNumber} of ${wizardQuestions.length}`}>
          {wizardQuestions.map((_, index) => (
            <span key={index} className={`hw-dot${index < currentStep ? " hw-dot-done" : index === currentStep ? " hw-dot-active" : ""}`} />
          ))}
        </div>
      </header>

      <div className="hw-progress" role="progressbar">
        <span style={{ width: `${(currentStep / wizardQuestions.length) * 100}%` }} />
      </div>

      <main className="hw-main">
        {!isFinalStep ? (
          <div key={currentStep} className={`hw-content ${contentAnim}`}>
            <div className="hw-question">
              <span className="hw-emoji">{getStepEmoji(activeQuestion.emoji)}</span>
              <p className="hw-label">{activeQuestion.id}</p>
              <h1 className="hw-title">{activeQuestion.title}</h1>
              <p className="hw-helper">{activeQuestion.helper}</p>
            </div>

            <div className="hw-answer-wrap">
              {activeQuestion.type === "text" && (
                <input id={`hw-answer-${currentStep}`} className="hw-input-line" type="text" value={answers[currentStep] ?? ""} placeholder={activeQuestion.placeholder} onChange={(event) => handleAnswerChange(event.target.value)} autoFocus />
              )}

              {activeQuestion.type === "textarea" && (
                <textarea id={`hw-answer-${currentStep}`} className="hw-textarea" value={answers[currentStep] ?? ""} placeholder={activeQuestion.placeholder} onChange={(event) => handleAnswerChange(event.target.value)} autoFocus />
              )}

              {activeQuestion.type === "azure" && (
                <div className="hw-azure-wrap">
                  <div className="hw-azure-panel">
                    <div className="hw-azure-hero">
                      <div className="hw-azure-orb" aria-hidden="true">Azure</div>
                      <div>
                        <div className="hw-azure-status-row">
                          <span className={`hw-azure-status${azureConfig.connected ? " hw-azure-status-connected" : ""}`}>
                            {azureConfig.connected ? "Connected" : "Action required"}
                          </span>
                          {azureConfig.lastConnectedAt && (
                            <span className="hw-inline-note">Last login {new Date(azureConfig.lastConnectedAt).toLocaleString()}</span>
                          )}
                        </div>
                        <p className="hw-azure-lead">
                          {azureConfig.mode === "manual" && azureConfig.authMethod === "api_key"
                            ? "Use your Azure OpenAI endpoint, deployment names, and API key to connect directly. Choose this only when Microsoft login is not available for your tenant."
                            : "Start with Microsoft sign-in using the account that owns your Azure subscription. Work, school and personal Microsoft accounts are supported."}
                        </p>
                      </div>
                    </div>

                    <div className="hw-azure-mode-row">
                      <button
                        type="button"
                        className={`hw-azure-mode-btn${azureConfig.mode === "guided" ? " hw-azure-mode-btn-active" : ""}`}
                        onClick={() => setAzureConfig((current) => ({ ...current, mode: "guided" }))}
                      >
                        Discover automatically
                      </button>
                      <button
                        type="button"
                        className={`hw-azure-mode-btn${azureConfig.mode === "manual" ? " hw-azure-mode-btn-active" : ""}`}
                        onClick={() => setAzureConfig((current) => ({ ...current, mode: "manual" }))}
                      >
                        Enter manually
                      </button>
                    </div>

                    <div className="hw-azure-step hw-azure-step-active">
                      <div className="hw-azure-step-index">1</div>
                      <div className="hw-azure-step-body">
                        <div className="hw-azure-step-head">
                          <div>
                            <p className="hw-field-label">Microsoft login</p>
                            <h3>Connect to your Azure tenant</h3>
                          </div>
                        </div>
                        {azureConfig.mode === "guided" ? (
                          <>
                            <div className="hw-azure-choice-row">
                              <button
                                type="button"
                                className={`hw-azure-choice${azureConfig.accountKind === "work" ? " hw-azure-choice-active" : ""}`}
                                onClick={() => setAzureConfig((current) => ({ ...current, accountKind: "work", tenantId: "", connected: false, lastConnectedAt: "" }))}
                              >
                                <span>Work or school account</span>
                                <small>Use Microsoft sign-in and discover tenants automatically.</small>
                              </button>
                              <button
                                type="button"
                                className={`hw-azure-choice${azureConfig.accountKind === "personal" ? " hw-azure-choice-active" : ""}`}
                                onClick={() => setAzureConfig((current) => ({ ...current, accountKind: "personal", connected: false, lastConnectedAt: "" }))}
                              >
                                <span>Personal Outlook Azure</span>
                                <small>Use this when your subscription is paid with @outlook.com/.es.</small>
                              </button>
                            </div>
                            <label className="hw-modal-field">
                              <span className="hw-field-label">App registration client ID</span>
                              <input
                                className="hw-modal-input"
                                type="text"
                                value={azureConfig.clientId}
                                placeholder="00000000-0000-0000-0000-000000000000"
                                onChange={(event) => handleAzureConfigChange("clientId", event.target.value)}
                                disabled={azureDiscoveryBusy}
                              />
                            </label>
                            {azureConfig.accountKind === "personal" && (
                              <label className="hw-modal-field">
                                <span className="hw-field-label">Directory (tenant) ID</span>
                                <input
                                  className="hw-modal-input"
                                  type="text"
                                  value={azureConfig.tenantId}
                                  placeholder="00000000-0000-0000-0000-000000000000"
                                  onChange={(event) => handleAzureConfigChange("tenantId", event.target.value)}
                                  disabled={azureDiscoveryBusy}
                                />
                                <span className="hw-inline-note">Find it in Azure Portal under Microsoft Entra ID or in the App Registration overview.</span>
                              </label>
                            )}
                            <div className="hw-azure-actions">
                              <button type="button" className="hw-btn-primary" onClick={() => void handleStartAzureDiscovery()} disabled={azureDiscoveryBusy}>
                                {azureDiscoveryBusy ? "Opening Microsoft..." : azureDiscoveryReady ? "Refresh discovery" : "Sign in with Microsoft"}
                              </button>
                              <span className="hw-inline-note">
                                {azureConfig.accountKind === "personal"
                                  ? "Personal Microsoft accounts must authenticate through the Azure tenant that owns the subscription."
                                  : "Automatic discovery starts with login. Tenant, subscription, resource and deployments unlock after Microsoft confirms your account."}
                              </span>
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="hw-azure-choice-row">
                              <button
                                type="button"
                                className={`hw-azure-choice${azureConfig.authMethod === "entra" ? " hw-azure-choice-active" : ""}`}
                                onClick={() => {
                                  setEditingStoredApiKey(false);
                                  setAzureConfig((current) => ({ ...current, authMethod: "entra", connected: false, lastConnectedAt: "" }));
                                }}
                              >
                                <span className="hw-azure-choice-title">
                                  <span>Microsoft login</span>
                                  <span className="hw-choice-badge hw-choice-badge-recommended">Recommended</span>
                                </span>
                                <small>Sign in with Microsoft to discover your Azure resources and keep the standard authorization flow.</small>
                              </button>
                              <button
                                type="button"
                                className={`hw-azure-choice${azureConfig.authMethod === "api_key" ? " hw-azure-choice-active" : ""}`}
                                onClick={handleSelectApiKeyMode}
                              >
                                <span className="hw-azure-choice-title">
                                  <span>API key</span>
                                  <span className="hw-choice-badge hw-choice-badge-fallback">Fallback</span>
                                </span>
                                <small>Connect directly to Azure OpenAI with your endpoint, deployment names, and API key.</small>
                              </button>
                            </div>
                            <div className="hw-azure-grid">
                              {azureConfig.authMethod === "entra" ? (
                                <>
                                  <label className="hw-modal-field">
                                    <span className="hw-field-label">Application client ID</span>
                                    <input className="hw-modal-input" type="text" value={azureConfig.clientId} placeholder="00000000-0000-0000-0000-000000000000" onChange={(event) => handleAzureConfigChange("clientId", event.target.value)} />
                                  </label>
                                  <label className="hw-modal-field">
                                    <span className="hw-field-label">Tenant scope</span>
                                    <input className="hw-modal-input" type="text" value={azureConfig.tenantId} placeholder="common" onChange={(event) => handleAzureConfigChange("tenantId", event.target.value)} />
                                  </label>
                                </>
                              ) : (
                                <label className="hw-modal-field hw-azure-wide">
                                  <span className="hw-field-label">Azure OpenAI API key</span>
                                  {azureConfig.apiKeyStored && !editingStoredApiKey && !azureConfig.apiKey.trim() ? (
                                    <>
                                      <div className="hw-azure-secret-state">
                                        <span className="hw-inline-note">Stored locally on this machine.</span>
                                        <div className="hw-azure-secret-actions">
                                          <button type="button" className="hw-btn-ghost" onClick={() => setEditingStoredApiKey(true)}>Replace key</button>
                                          <button type="button" className="hw-btn-ghost" onClick={() => void handleClearStoredApiKey()}>Clear key</button>
                                        </div>
                                      </div>
                                    </>
                                  ) : (
                                    <input className="hw-modal-input" type="password" value={azureConfig.apiKey} placeholder="Paste the Azure OpenAI key" onChange={(event) => handleAzureConfigChange("apiKey", event.target.value)} />
                                  )}
                                  <span className="hw-inline-note hw-inline-note-warning">This key is stored locally on this machine. Use this mode only as a fallback when Microsoft login is not available.</span>
                                </label>
                              )}
                            </div>
                          </>
                        )}
                      </div>
                    </div>

                    {azureConfig.mode === "guided" ? (
                      <div className="hw-azure-flow">
                        {!azureDiscoveryReady && (
                          <div className="hw-azure-locked">
                            <span className="hw-azure-lock-dot" aria-hidden="true" />
                            <p>Sign in first to unlock tenant, subscription and resource discovery.</p>
                          </div>
                        )}

                        {azureDiscoveryReady && (
                        <div className="hw-azure-step hw-azure-step-active">
                          <div className="hw-azure-step-index">2</div>
                          <div className="hw-azure-step-body">
                            <div className="hw-azure-step-head">
                              <div>
                                <p className="hw-field-label">Resource</p>
                                <h3>Choose where AzulClaw will run</h3>
                              </div>
                            </div>
                        <div className="hw-azure-grid">
                          <label className="hw-modal-field">
                            <span className="hw-field-label">Tenant</span>
                            <select className="hw-modal-input" value={azureConfig.tenantId} onChange={(event) => handleAzureConfigChange("tenantId", event.target.value)} disabled={azureTenantOptions.length === 0 || azureDiscoveryBusy}>
                              {azureTenantOptions.length === 0 && <option value="">Tenant from Microsoft login</option>}
                              {azureTenantOptions.map((tenant) => (
                                <option key={tenant.id} value={tenant.id}>{tenant.label}</option>
                              ))}
                            </select>
                          </label>
                          <label className="hw-modal-field">
                            <span className="hw-field-label">Subscription</span>
                            <select className="hw-modal-input" value={azureConfig.subscriptionId} onChange={(event) => handleAzureSubscriptionChange(event.target.value)} disabled={azureSubscriptions.length === 0 || azureDiscoveryBusy}>
                              <option value="">Select subscription</option>
                              {azureSubscriptions.map((item) => (
                                <option key={item.id} value={item.id}>{item.display_name}</option>
                              ))}
                            </select>
                          </label>
                          <label className="hw-modal-field hw-azure-wide">
                            <span className="hw-field-label">Azure OpenAI resource</span>
                            <select
                              className="hw-modal-input"
                              value={azureConfig.accountName ? `${azureConfig.resourceGroup}/${azureConfig.accountName}` : ""}
                              onChange={(event) => {
                                const selected = azureResources.find((item) => `${item.resource_group}/${item.name}` === event.target.value);
                                      if (selected) {
                                        setAzureConfig((current) => ({
                                          ...current,
                                          endpoint: selected.endpoint,
                                          resourceGroup: selected.resource_group,
                                          accountName: selected.name,
                                          connected: false,
                                          lastConnectedAt: "",
                                        }));
                                        void handleLoadAzureDeployments(selected);
                                      }
                                    }}
                              disabled={azureResources.length === 0 || azureDiscoveryBusy}
                            >
                              <option value="">Select resource</option>
                              {azureResources.map((item) => (
                                <option key={item.id} value={`${item.resource_group}/${item.name}`}>{item.name} · {item.location}</option>
                              ))}
                            </select>
                          </label>
                          <label className="hw-modal-field hw-azure-wide">
                            <span className="hw-field-label">Endpoint</span>
                            <input className="hw-modal-input" type="url" value={azureConfig.endpoint} placeholder="Auto-filled from the selected resource" readOnly />
                          </label>
                          <label className="hw-modal-field hw-azure-wide">
                            <span className="hw-field-label">Key Vault</span>
                            <select
                              className="hw-modal-input"
                              value={azureConfig.keyVaultName ? `${azureConfig.keyVaultResourceGroup}/${azureConfig.keyVaultName}` : ""}
                              onChange={(event) => {
                                const selected = azureKeyVaults.find((item) => `${item.resource_group}/${item.name}` === event.target.value);
                                if (selected) {
                                  void handleLoadAzureKeyVaultSecrets(selected);
                                }
                              }}
                              disabled={azureKeyVaults.length === 0 || azureDiscoveryBusy}
                            >
                              <option value="">Select Key Vault</option>
                              {azureKeyVaults.map((item) => (
                                <option key={item.id} value={`${item.resource_group}/${item.name}`}>{item.name} · {item.location}</option>
                              ))}
                            </select>
                            <span className="hw-inline-note">Runtime settings and secrets will be loaded from this vault.</span>
                          </label>
                          <label className="hw-modal-field hw-azure-wide">
                            <span className="hw-field-label">Key Vault URL</span>
                            <input className="hw-modal-input" type="url" value={azureConfig.keyVaultUrl} placeholder="Auto-filled from the selected vault" readOnly />
                          </label>
                          <label className="hw-modal-field">
                            <span className="hw-field-label">MicrosoftAppId secret</span>
                            {azureKeyVaultSecrets.length > 0 ? (
                              <select className="hw-modal-input" value={azureConfig.microsoftAppIdSecretName} onChange={(event) => handleAzureConfigChange("microsoftAppIdSecretName", event.target.value)} disabled={azureDiscoveryBusy}>
                                <option value="">Default: MicrosoftAppId</option>
                                {azureKeyVaultSecrets.map((item) => (
                                  <option key={item.id || item.name} value={item.name}>{item.name}{item.enabled ? "" : " (disabled)"}</option>
                                ))}
                              </select>
                            ) : (
                              <input className="hw-modal-input" type="text" value={azureConfig.microsoftAppIdSecretName} placeholder="MicrosoftAppId" onChange={(event) => handleAzureConfigChange("microsoftAppIdSecretName", event.target.value)} disabled={azureDiscoveryBusy} />
                            )}
                          </label>
                          <label className="hw-modal-field">
                            <span className="hw-field-label">MicrosoftAppPassword secret</span>
                            {azureKeyVaultSecrets.length > 0 ? (
                              <select className="hw-modal-input" value={azureConfig.microsoftAppPasswordSecretName} onChange={(event) => handleAzureConfigChange("microsoftAppPasswordSecretName", event.target.value)} disabled={azureDiscoveryBusy}>
                                <option value="">Default: MicrosoftAppPassword</option>
                                {azureKeyVaultSecrets.map((item) => (
                                  <option key={item.id || item.name} value={item.name}>{item.name}{item.enabled ? "" : " (disabled)"}</option>
                                ))}
                              </select>
                            ) : (
                              <input className="hw-modal-input" type="text" value={azureConfig.microsoftAppPasswordSecretName} placeholder="MicrosoftAppPassword" onChange={(event) => handleAzureConfigChange("microsoftAppPasswordSecretName", event.target.value)} disabled={azureDiscoveryBusy} />
                            )}
                          </label>
                          <label className="hw-modal-field">
                            <span className="hw-field-label">MicrosoftAppTenantId secret</span>
                            {azureKeyVaultSecrets.length > 0 ? (
                              <select className="hw-modal-input" value={azureConfig.microsoftAppTenantIdSecretName} onChange={(event) => handleAzureConfigChange("microsoftAppTenantIdSecretName", event.target.value)} disabled={azureDiscoveryBusy}>
                                <option value="">Default: MicrosoftAppTenantId</option>
                                {azureKeyVaultSecrets.map((item) => (
                                  <option key={item.id || item.name} value={item.name}>{item.name}{item.enabled ? "" : " (disabled)"}</option>
                                ))}
                              </select>
                            ) : (
                              <input className="hw-modal-input" type="text" value={azureConfig.microsoftAppTenantIdSecretName} placeholder="MicrosoftAppTenantId" onChange={(event) => handleAzureConfigChange("microsoftAppTenantIdSecretName", event.target.value)} disabled={azureDiscoveryBusy} />
                            )}
                          </label>
                        </div>
                          </div>
                        </div>
                        )}
                        {azureConfig.endpoint && (
                        <div className="hw-azure-step hw-azure-step-active">
                          <div className="hw-azure-step-index">3</div>
                          <div className="hw-azure-step-body">
                            <div className="hw-azure-step-head">
                              <div>
                                <p className="hw-field-label">Deployments</p>
                                <h3>Confirm model routes</h3>
                              </div>
                            </div>
                        <div className="hw-azure-grid">
                          <label className="hw-modal-field">
                            <span className="hw-field-label">Main deployment</span>
                            {hasAzureDeployments ? (
                              <select className="hw-modal-input" value={azureConfig.deployment} onChange={(event) => handleAzureConfigChange("deployment", event.target.value)} disabled={azureDiscoveryBusy}>
                                <option value="">Select deployment</option>
                                {azureDeployments.filter((item) => item.capabilities.includes("chat")).map((item) => (
                                  <option key={item.id} value={item.name}>{item.name} · {item.model_name}</option>
                                ))}
                              </select>
                            ) : (
                              <input className="hw-modal-input" type="text" value={azureConfig.deployment} placeholder="gpt-4o" onChange={(event) => handleAzureConfigChange("deployment", event.target.value)} disabled={azureDiscoveryBusy} />
                            )}
                          </label>
                          <label className="hw-modal-field">
                            <span className="hw-field-label">Fast deployment</span>
                            {hasAzureDeployments ? (
                              <select className="hw-modal-input" value={azureConfig.fastDeployment} onChange={(event) => handleAzureConfigChange("fastDeployment", event.target.value)} disabled={azureDiscoveryBusy}>
                                <option value="">Select deployment</option>
                                {azureDeployments.filter((item) => item.capabilities.includes("chat")).map((item) => (
                                  <option key={item.id} value={item.name}>{item.name} · {item.model_name}</option>
                                ))}
                              </select>
                            ) : (
                              <input className="hw-modal-input" type="text" value={azureConfig.fastDeployment} placeholder="gpt-4o-mini" onChange={(event) => handleAzureConfigChange("fastDeployment", event.target.value)} disabled={azureDiscoveryBusy} />
                            )}
                          </label>
                          <label className="hw-modal-field hw-azure-wide">
                            <span className="hw-field-label">Embedding deployment</span>
                            {hasAzureDeployments ? (
                              <select className="hw-modal-input" value={azureConfig.embeddingDeployment} onChange={(event) => handleAzureConfigChange("embeddingDeployment", event.target.value)} disabled={azureDiscoveryBusy}>
                                <option value="">Select deployment</option>
                                {azureDeployments.filter((item) => item.capabilities.includes("embedding")).map((item) => (
                                  <option key={item.id} value={item.name}>{item.name} · {item.model_name}</option>
                                ))}
                              </select>
                            ) : (
                              <input className="hw-modal-input" type="text" value={azureConfig.embeddingDeployment} placeholder="text-embedding-3-large" onChange={(event) => handleAzureConfigChange("embeddingDeployment", event.target.value)} disabled={azureDiscoveryBusy} />
                            )}
                          </label>
                        </div>
                            {!hasAzureDeployments && !azureDiscoveryBusy && (
                              <div className="hw-azure-empty">
                                <p>No deployments were returned for this resource. You can type the deployment names manually, or refresh if you just created them in Azure.</p>
                                <button
                                  type="button"
                                  className="hw-btn-ghost"
                                  onClick={() => {
                                    const selected = azureResources.find(
                                      (item) =>
                                        item.subscription_id === azureConfig.subscriptionId &&
                                        item.resource_group === azureConfig.resourceGroup &&
                                        item.name === azureConfig.accountName,
                                    );
                                    if (selected) void handleLoadAzureDeployments(selected);
                                  }}
                                >
                                  Refresh deployments
                                </button>
                              </div>
                            )}
                          </div>
                        </div>
                        )}
                      </div>
                    ) : (
                      <>
                        {azureConfig.authMethod === "api_key" && (
                          <p className="hw-inline-note">
                            This mode sets up direct Azure OpenAI access with one endpoint, one API key, and the deployment names below.
                          </p>
                        )}
                      <div className="hw-azure-grid">
                        <label className="hw-modal-field hw-azure-wide">
                          <span className="hw-field-label">Azure OpenAI endpoint</span>
                          <input className="hw-modal-input" type="url" value={azureConfig.endpoint} placeholder="https://your-resource.openai.azure.com" onChange={(event) => handleAzureConfigChange("endpoint", event.target.value)} />
                        </label>
                        {azureConfig.authMethod === "entra" && (
                          <>
                            <label className="hw-modal-field hw-azure-wide">
                              <span className="hw-field-label">Key Vault URL</span>
                              <input className="hw-modal-input" type="url" value={azureConfig.keyVaultUrl} placeholder="https://your-vault.vault.azure.net" onChange={(event) => handleAzureConfigChange("keyVaultUrl", event.target.value)} />
                            </label>
                            <label className="hw-modal-field">
                              <span className="hw-field-label">MicrosoftAppId secret</span>
                              <input className="hw-modal-input" type="text" value={azureConfig.microsoftAppIdSecretName} placeholder="MicrosoftAppId" onChange={(event) => handleAzureConfigChange("microsoftAppIdSecretName", event.target.value)} />
                            </label>
                            <label className="hw-modal-field">
                              <span className="hw-field-label">MicrosoftAppPassword secret</span>
                              <input className="hw-modal-input" type="text" value={azureConfig.microsoftAppPasswordSecretName} placeholder="MicrosoftAppPassword" onChange={(event) => handleAzureConfigChange("microsoftAppPasswordSecretName", event.target.value)} />
                            </label>
                            <label className="hw-modal-field">
                              <span className="hw-field-label">MicrosoftAppTenantId secret</span>
                              <input className="hw-modal-input" type="text" value={azureConfig.microsoftAppTenantIdSecretName} placeholder="MicrosoftAppTenantId" onChange={(event) => handleAzureConfigChange("microsoftAppTenantIdSecretName", event.target.value)} />
                            </label>
                          </>
                        )}
                        <label className="hw-modal-field">
                          <span className="hw-field-label">Main deployment</span>
                          <input className="hw-modal-input" type="text" value={azureConfig.deployment} placeholder="gpt-4o" onChange={(event) => handleAzureConfigChange("deployment", event.target.value)} />
                        </label>
                        <label className="hw-modal-field">
                          <span className="hw-field-label">Fast deployment</span>
                          <input className="hw-modal-input" type="text" value={azureConfig.fastDeployment} placeholder="gpt-4o-mini" onChange={(event) => handleAzureConfigChange("fastDeployment", event.target.value)} />
                        </label>
                        <label className="hw-modal-field hw-azure-wide">
                          <span className="hw-field-label">Embedding deployment</span>
                          <input className="hw-modal-input" type="text" value={azureConfig.embeddingDeployment} placeholder="text-embedding-3-large" onChange={(event) => handleAzureConfigChange("embeddingDeployment", event.target.value)} />
                        </label>
                      </div>
                      </>
                    )}

                    {azureError && <p className="hw-inline-note hw-inline-note-warning">{azureError}</p>}

                    {(azureConfig.mode === "manual" || azureConfig.endpoint) && (
                      <div className={`hw-azure-final${azureConfig.connected ? " hw-azure-final-connected" : ""}`}>
                        {azureConfig.connected ? (
                          <>
                            <div className="hw-azure-success-mark" aria-hidden="true">
                              <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                                <path d="M4 9.5L7.2 12.7L14 5.8" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                              </svg>
                            </div>
                            <div>
                              <p className="hw-field-label">Azure connected</p>
                              <p className="hw-inline-note">
                                {azureConfig.authMethod === "api_key"
                                  ? `Azure OpenAI is configured with a local API key. ${azureConfig.lastConnectedAt ? `Last connected ${new Date(azureConfig.lastConnectedAt).toLocaleString()}.` : ""}`
                                  : `Azure OpenAI access is authorized. ${azureConfig.keyVaultName ? `Key Vault: ${azureConfig.keyVaultName}. ` : ""}${azureConfig.lastConnectedAt ? `Last authorized ${new Date(azureConfig.lastConnectedAt).toLocaleString()}.` : ""}`}
                              </p>
                            </div>
                            <button type="button" className="hw-btn-ghost" onClick={() => void handleConnectAzure()} disabled={azureBusy}>
                              {azureBusy ? (azureConfig.authMethod === "api_key" ? "Connecting..." : "Authorizing...") : (azureConfig.authMethod === "api_key" ? "Reconnect with API key" : "Reauthorize")}
                            </button>
                          </>
                        ) : (
                          <>
                            <div>
                              <p className="hw-field-label">Finish</p>
                              <p className="hw-inline-note">
                                {azureConfig.authMethod === "api_key"
                                  ? "Advanced mode. AzulClaw will use the endpoint, deployment names and API key directly, without Microsoft login."
                                  : "AzulClaw will request an Azure OpenAI token, apply the selected Key Vault URL locally, and keep the token in memory."}
                              </p>
                            </div>
                            <button type="button" className="hw-btn-primary" onClick={() => void handleConnectAzure()} disabled={azureBusy}>
                              {azureBusy ? (azureConfig.authMethod === "api_key" ? "Connecting..." : "Authorizing...") : (azureConfig.authMethod === "api_key" ? "Connect with API key" : "Authorize Azure OpenAI access")}
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {activeQuestion.type === "skills" && (
                <div className="hw-skills-wrap">
                  <div className="hw-skills-copy">
                    <p className="hw-inline-note">Here you don't type free text. You can only activate skills from the available catalogue.</p>
                    <p className="hw-inline-note">Clicking a skill opens its configuration. It won't be activated until you complete that popup.</p>
                  </div>

                  <div className="hw-skills-grid">
                    {SKILL_CATALOG.map((skill) => {
                      const isConfigured = configuredSkills.includes(skill.id);
                      return (
                        <button key={skill.id} type="button" className={`hw-skill-card${isConfigured ? " hw-skill-card-active" : ""}`} onClick={() => openSkillConfig(skill.id)}>
                          <div className="hw-skill-card-top">
                            <span className="hw-skill-card-title">{skill.title}</span>
                            <span className={`hw-skill-state${isConfigured ? " hw-skill-state-ready" : ""}`}>{isConfigured ? "Configured" : "Configure"}</span>
                          </div>
                          <p className="hw-skill-card-body">{skill.description}</p>
                        </button>
                      );
                    })}
                  </div>

                  <div className="hw-skill-summary">
                    <span className="hw-field-label">Currently active skills</span>
                    <div className="hw-skill-tags">
                      {configuredSkills.length > 0 ? configuredSkills.map((skill) => <span key={skill} className="hw-skill-tag">{skill}</span>) : <span className="hw-inline-note">None for now.</span>}
                    </div>
                  </div>

                  <p className="hw-inline-note">If you don't want to activate any yet, continue with Skip for now.</p>
                </div>
              )}

              {activeQuestion.type === "superpowers" && (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "40px 24px", gap: "24px", textAlign: "center" }}>
                  <div style={{
                    width: "72px", height: "72px", borderRadius: "50%",
                    background: "radial-gradient(circle at 35% 35%, rgba(99,102,241,0.35), rgba(37,99,235,0.12))",
                    border: "1.5px solid rgba(99,102,241,0.35)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: "2rem", boxShadow: "0 0 32px rgba(99,102,241,0.18)",
                  }}>⚡</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                    <p style={{ margin: 0, fontSize: "1.15rem", fontWeight: 700, letterSpacing: "-0.01em", color: "var(--text, #e2e8f0)", lineHeight: 1.3 }}>
                      Give your agent superpowers with skills
                    </p>
                    <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--muted)", lineHeight: 1.6, maxWidth: "340px" }}>
                      Expand what your assistant knows and can do — across any domain.
                    </p>
                  </div>
                  <span style={{
                    display: "inline-block",
                    padding: "5px 16px",
                    borderRadius: "999px",
                    background: "rgba(99,102,241,0.1)",
                    border: "1px solid rgba(99,102,241,0.28)",
                    color: "rgba(165,180,252,0.9)",
                    fontSize: "0.72rem",
                    fontWeight: 700,
                    letterSpacing: "0.12em",
                    textTransform: "uppercase",
                  }}>Coming soon</span>
                </div>
              )}

              {activeQuestion.type === "path" && (
                <div className="hw-workspace-wrap">
                  <div className="hw-workspace-panel">
                    <p className="hw-inline-note">Think of it as AzulClaw's desk. Everything it reads, creates or organises will live inside that folder.</p>

                    <div className="hw-workspace-actions">
                      <button type="button" className="hw-btn-ghost" onClick={() => void handlePickWorkspace()} disabled={isPickingWorkspace}>
                        {isPickingWorkspace ? "Opening browser..." : "Choose folder..."}
                      </button>
                      <span className="hw-inline-note">You can also paste or adjust the path manually.</span>
                    </div>

                    <label className="hw-field-label" htmlFor="hw-workspace-root">Workspace folder</label>
                    <input id="hw-workspace-root" className="hw-input-line hw-input-mono" type="text" value={workspaceRoot} placeholder={activeQuestion.placeholder} onChange={(event) => setWorkspaceRoot(event.target.value)} autoFocus />

                    {previewMemoryDbPath(workspaceRoot) && (
                      <div className="hw-workspace-db-preview">
                        <span className="hw-field-label">Memory database (auto)</span>
                        <p className="hw-inline-note hw-mono">{previewMemoryDbPath(workspaceRoot)}</p>
                      </div>
                    )}

                    {workspacePickerError && <p className="hw-inline-note hw-inline-note-warning">{workspacePickerError}</p>}

                    <div className="hw-workspace-confirm">
                      <p className="hw-field-label">Sensitive actions</p>
                      <div className="hw-choice-row">
                        <button type="button" className={`hw-choice${confirmSensitiveActions ? " hw-choice-active" : ""}`} onClick={() => setConfirmSensitiveActions(true)}>
                          Ask before changing important things
                        </button>
                        <button type="button" className={`hw-choice${!confirmSensitiveActions ? " hw-choice-active" : ""}`} onClick={() => setConfirmSensitiveActions(false)}>
                          Let it act without prior confirmation
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {activeQuestion.type !== "textarea" && (
              <p className="hw-hint">
                <span>{nextHint}</span>
                <kbd className="hw-kbd">Enter</kbd>
              </p>
            )}
          </div>
        ) : (
          <div key="final" className={`hw-content hw-celebrate ${contentAnim}`}>
            <img src={mascotImage} alt="AzulClaw" className="hw-celebrate-img" />
            <div className="hw-question" style={{ textAlign: "center" }}>
              <p className="hw-label">READY</p>
              <h1 className="hw-title">{draftProfile.name} is ready to get started</h1>
              <p className="hw-helper">This is the starting point. Everything can be adjusted later from Settings.</p>
            </div>

            <div className="hw-summary-grid">
              <div className="hw-summary-item"><span className="hw-summary-label">Name</span><span className="hw-summary-value">{draftProfile.name}</span></div>

              <div className="hw-summary-item"><span className="hw-summary-label">Style</span><span className="hw-summary-value">{draftProfile.tone} · {draftProfile.style}</span></div>
              <div className="hw-summary-item"><span className="hw-summary-label">Azure</span><span className="hw-summary-value">{azureConfig.connected ? (azureConfig.authMethod === "api_key" ? "API key connected" : "Microsoft login connected") : "Not connected"}</span></div>
              <div className="hw-summary-item"><span className="hw-summary-label">Key Vault</span><span className="hw-summary-value hw-mono">{azureConfig.keyVaultUrl || "Not selected"}</span></div>
              <div className="hw-summary-item"><span className="hw-summary-label">Workspace</span><span className="hw-summary-value hw-mono">{draftProfile.workspace_root}</span></div>
              <div className="hw-summary-item" style={{ gridColumn: "1 / -1" }}><span className="hw-summary-label">Memory database</span><span className="hw-summary-value hw-mono">{previewMemoryDbPath(draftProfile.workspace_root) || "(set workspace folder)"}</span></div>
              <div className="hw-summary-item" style={{ gridColumn: "1 / -1" }}><span className="hw-summary-label">Capabilities</span><span className="hw-summary-value">{draftProfile.skills.length > 0 ? draftProfile.skills.join(", ") : "None for now"}</span></div>
            </div>
          </div>
        )}
      </main>

      <footer className="hw-footer">
        <button type="button" className="hw-btn-ghost" onClick={handleBack} disabled={currentStep === 0 || Boolean(activeSkill)}>Back</button>
        <span className="hw-step-label">{isFinalStep ? "Summary" : `${stepNumber} / ${wizardQuestions.length}`}</span>

        {isFinalStep ? (
          <div style={{ display: "flex", gap: "10px" }}>
            {!onboardingRequired && <button type="button" className="hw-btn-ghost" onClick={() => void handleSave(false)}>{isSaving ? "Saving..." : "Save draft"}</button>}
            <button type="button" className="hw-btn-primary" onClick={() => void handleSave(true)}>
              {isSaving ? "Saving..." : onboardingRequired ? "Enter the desktop ->" : "Apply changes ->"}
            </button>
          </div>
        ) : (
          <button type="button" className="hw-btn-primary" onClick={handleNext} disabled={Boolean(activeSkill)}>{nextButtonLabel}</button>
        )}
      </footer>

      {showAzureSkipWarning && (
        <div className="hw-modal-backdrop" onClick={() => setShowAzureSkipWarning(false)}>
          <div className="hw-modal-card hw-skip-card" onClick={(event) => event.stopPropagation()}>
            <div className="hw-skip-icon" aria-hidden="true">!</div>
            <div className="hw-modal-head">
              <div>
                <p className="hw-field-label">Azure is required</p>
                <h3 className="hw-modal-title">AzulClaw will not work without Azure</h3>
              </div>
            </div>
            <p className="hw-inline-note">
              If you skip this step, the assistant cannot call Azure OpenAI or use your Azure resources. You can continue only to finish the profile and connect Azure later from settings.
            </p>
            <div className="hw-modal-actions">
              <button type="button" className="hw-btn-ghost" onClick={() => setShowAzureSkipWarning(false)}>Go back and connect</button>
              <button type="button" className="hw-btn-primary" onClick={confirmAzureSkip}>Skip anyway</button>
            </div>
          </div>
        </div>
      )}

      {showApiKeyModeWarning && (
        <div className="hw-modal-backdrop" onClick={() => setShowApiKeyModeWarning(false)}>
          <div className="hw-modal-card hw-skip-card" onClick={(event) => event.stopPropagation()}>
            <div className="hw-skip-icon" aria-hidden="true">!</div>
            <div className="hw-modal-head">
              <div>
                <p className="hw-field-label">Advanced mode</p>
                <h3 className="hw-modal-title">Use API key mode only as a fallback</h3>
              </div>
            </div>
            <p className="hw-inline-note">
              Microsoft login is the recommended path. Use API key mode only when your tenant blocks Microsoft sign-in or when you need a manual Azure OpenAI connection.
            </p>
            <p className="hw-inline-note hw-inline-note-warning">
              This mode stores an Azure OpenAI API key locally on this machine.
            </p>
            <div className="hw-modal-actions">
              <button type="button" className="hw-btn-ghost" onClick={() => setShowApiKeyModeWarning(false)}>Keep recommended login</button>
              <button type="button" className="hw-btn-primary" onClick={enableApiKeyMode}>Use API key anyway</button>
            </div>
          </div>
        </div>
      )}

      {showApiKeyConnectWarning && (
        <div className="hw-modal-backdrop" onClick={() => setShowApiKeyConnectWarning(false)}>
          <div className="hw-modal-card hw-skip-card" onClick={(event) => event.stopPropagation()}>
            <div className="hw-skip-icon" aria-hidden="true">!</div>
            <div className="hw-modal-head">
              <div>
                <p className="hw-field-label">Before you connect</p>
                <h3 className="hw-modal-title">This stores a local Azure OpenAI key</h3>
              </div>
            </div>
            <p className="hw-inline-note">
              AzulClaw will connect directly to Azure OpenAI with the endpoint, deployment names, and API key you entered.
            </p>
            <p className="hw-inline-note hw-inline-note-warning">
              This is a fallback path. If Microsoft login is available in your tenant, that remains the recommended configuration.
            </p>
            <div className="hw-modal-actions">
              <button type="button" className="hw-btn-ghost" onClick={() => setShowApiKeyConnectWarning(false)}>Go back</button>
              <button
                type="button"
                className="hw-btn-primary"
                onClick={() => {
                  setShowApiKeyConnectWarning(false);
                  void handleConnectAzure(true);
                }}
              >
                Connect with API key
              </button>
            </div>
          </div>
        </div>
      )}

      {activeSkill && (
        <div className="hw-modal-backdrop" onClick={closeSkillConfig}>
          <section className="hw-modal-card" role="dialog" aria-modal="true" aria-labelledby="hw-skill-modal-title" onClick={(event) => event.stopPropagation()}>
            <div className="hw-modal-head">
              <div>
                <p className="hw-label">CONFIGURE SKILL</p>
                <h3 id="hw-skill-modal-title" className="hw-modal-title">{activeSkill.title}</h3>
              </div>
              <button type="button" className="hw-btn-ghost" onClick={closeSkillConfig}>Close</button>
            </div>

            <p className="hw-inline-note">{activeSkill.description}</p>

            <div className="hw-modal-fields">
              {activeSkill.fields.map((field, index) => (
                <label key={field.id} className="hw-modal-field">
                  <span className="hw-field-label">{field.label}</span>
                  <input className="hw-modal-input" type={field.type} value={skillDraft[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => handleSkillFieldChange(field.id, event.target.value)} autoFocus={index === 0} />
                  <span className="hw-inline-note">{field.helper}</span>
                </label>
              ))}
            </div>

            {skillModalError && <p className="hw-inline-note hw-inline-note-warning">{skillModalError}</p>}

            <div className="hw-modal-actions">
              {configuredSkills.includes(activeSkill.id) && <button type="button" className="hw-btn-ghost" onClick={() => deactivateSkill(activeSkill.id)}>Deactivate skill</button>}
              <button type="button" className="hw-btn-primary" onClick={saveSkillConfig}>{configuredSkills.includes(activeSkill.id) ? "Save configuration" : "Activate skill"}</button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
