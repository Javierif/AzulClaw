export type AppView =
  | "chat"
  | "skills"
  | "registry"
  | "context"
  | "heartbeats"
  | "runtime"
  | "settings";

export interface WorkspaceEntry {
  name: string;
  kind: "file" | "folder";
  path: string;
}

export interface ProcessSummary {
  id: string;
  title: string;
  status: "running" | "waiting" | "done" | "failed";
  skill: string;
  kind: string;
  lane: string;
  startedAt: string;
  updatedAt?: string;
  detail?: string;
  modelLabel?: string;
}

export interface MemoryRecord {
  id: string;
  title: string;
  content?: string;
  kind: "preference" | "episodic" | "semantic" | "session";
  source: string;
  pinned?: boolean;
  created_at?: string;
}

export interface MemorySettings {
  memory_db_path: string;
  memory_db_path_override: string;
  default_memory_db_path: string;
  vector_memory_enabled: boolean;
  reload_ok?: boolean;
  reload_error?: string;
}

export interface AttachmentPreview {
  kind: "image" | "document" | "text";
  mime_type?: string;
  thumbnail_data_uri?: string;
  snippet?: string;
  width?: number;
  height?: number;
  page_count?: number;
  pages_with_text?: number;
  avg_chars_per_page?: number;
}

export interface AttachmentSummary {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  kind: "image" | "document" | "text";
  extraction_status: "pending" | "ready" | "low_text_quality" | "unsupported" | "failed";
  page_count: number;
  preview?: AttachmentPreview;
  message_id?: string;
  conversation_id?: string;
  created_at?: string;
}

export interface ChatExchange {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
  attachments?: AttachmentSummary[];
  approval_action_id?: string;
  approval_status?: string;
  approval_status_label?: string;
  workflow_events?: SkillWorkflowEvent[];
}

export interface ChatRuntimeSkipDetail {
  model_id: string;
  model_label: string;
  lane: string;
  reason: string;
  reason_label: string;
  detail: string;
}

export interface ChatRuntimeFailedAttempt {
  model_id: string;
  label: string;
  error: string;
}

export interface ChatDebugTrace {
  lane_label: string;
  reason_label: string;
  triage_reason: string;
  turn_status?: string;
  model_label: string;
  process_id: string;
  attempt_count?: number;
  skipped_models?: ChatRuntimeSkipDetail[];
  failed_attempts?: ChatRuntimeFailedAttempt[];
  started_at?: string;
  completed_at?: string;
  elapsed_ms?: number;
  summary?: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
  has_unread: boolean;
  last_message_id?: string;
  last_message_at?: string;
  last_message_role?: "user" | "assistant" | "";
  last_message_preview?: string;
  snippet?: string;
}

export interface ThinkingStep {
  id: string;
  label: string;
  status: "pending" | "active" | "done";
}

export interface ThinkingPhase {
  id: string;
  label: string;
  status: "pending" | "active" | "done";
  steps: ThinkingStep[];
}

export interface ThinkingProgress {
  event_type?: "progress-init" | "progress-update" | "progress-idle" | "progress-done";
  title: string;
  summary: string;
  badge: string;
  lane?: string;
  lane_label?: string;
  triage_reason?: string;
  reason_label?: string;
  current_step_label?: string;
  started_at?: string;
  last_updated_at?: string;
  active_count: number;
  phases: ThinkingPhase[];
}

export interface ChatRuntimeMeta {
  lane: string;
  model_id: string;
  model_label: string;
  process_id: string;
  attempt_count?: number;
  skipped_models?: ChatRuntimeSkipDetail[];
  failed_attempts?: ChatRuntimeFailedAttempt[];
  triage_reason?: string;
  turn_status?: string;
  workflow_events?: SkillWorkflowEvent[];
}

export interface SkillWorkflowEvent {
  type: "delta" | "status" | "request_info" | "completed" | "failed" | string;
  run_id: string;
  skill_id: string;
  data: Record<string, unknown>;
}

export interface ChatStreamEvent {
  type:
    | "start"
    | "commentary"
    | "progress-init"
    | "progress-update"
    | "progress-idle"
    | "progress-done"
    | "delta"
    | "done"
    | "error";
  text?: string;
  reply?: string;
  history?: ChatExchange[];
  runtime?: ChatRuntimeMeta;
  message?: string;
  progress?: ThinkingProgress;
  conversation_id?: string;
  /** Present on ``done`` when the server has a conversation row title (sidebar + top bar). */
  conversation_title?: string;
}

export interface SetupProfile {
  name: string;
  role: string;
  /** Deprecated: kept for backward compatibility with persisted profiles; no longer edited in the UI. */
  mission: string;
  tone: string;
  style: string;
  autonomy: string;
  archetype: string;
  workspace_root: string;
  confirm_sensitive_actions: boolean;
  require_authenticator_for_sensitive_actions: boolean;
  is_hatched: boolean;
  completed_at: string;
  skills: string[];
  skill_configs: Record<string, Record<string, string>>;
  /** Effective SQLite path (server-computed; not stored in profile JSON). */
  memory_db_path?: string;
  /** Present on ``POST /api/desktop/data-wipe`` responses only. */
  restart_required?: boolean;
}

export interface DesktopShellPreferences {
  tray_icon_enabled: boolean;
  global_shortcut_enabled: boolean;
  close_to_tray_enabled: boolean;
  global_shortcut: string;
}

export type HatchingProfile = SetupProfile;

export interface RuntimeModelStatus {
  id: string;
  label: string;
  lane: "fast" | "slow";
  provider: "azure" | "openai";
  deployment: string;
  enabled: boolean;
  streaming_enabled: boolean;
  capabilities: string[];
  available: boolean;
  cooldown_until: string;
  last_error: string;
  description: string;
  probe_detail: string;
}

export interface RuntimeOverview {
  default_lane: "auto" | "fast" | "slow";
  models: RuntimeModelStatus[];
  scheduler_running: boolean;
  scheduler_last_error: string;
  jobs_total: number;
  jobs_running: number;
  processes_visible: number;
}

export interface BackendLogTail {
  name: string;
  path: string;
  exists: boolean;
  content: string;
}

export interface BackendAuthStatus {
  mode: "api_key" | "entra";
  startup_enabled: boolean;
  status: "idle" | "authenticating" | "authenticated" | "failed" | "disabled";
  detail: string;
  last_error: string;
  last_success_at: string;
  source?: "default" | "frontend" | string;
  requires_frontend_login?: boolean;
}

export interface AzureSubscriptionOption {
  id: string;
  display_name: string;
  state: string;
  tenant_id?: string;
}

export interface AzureOpenAIResourceOption {
  id: string;
  name: string;
  location: string;
  resource_group: string;
  subscription_id: string;
  kind: string;
  endpoint: string;
}

export interface AzureKeyVaultOption {
  id: string;
  name: string;
  location: string;
  resource_group: string;
  subscription_id: string;
  vault_uri: string;
}

export interface AzureKeyVaultSecretOption {
  id: string;
  name: string;
  enabled: boolean;
  content_type: string;
}

export interface AzureDeploymentOption {
  id: string;
  name: string;
  model_name: string;
  model_version: string;
  model_format: string;
  sku_name: string;
  provisioning_state: string;
  capabilities: string[];
}

export interface BackendStatus {
  status: "running" | "offline";
  api_base: string;
  runtime_dir: string;
  log_dir: string;
  models_total: number;
  models_enabled: number;
  scheduler_running: boolean;
  auth: BackendAuthStatus;
  logs: BackendLogTail[];
  error?: string;
}

export interface ScheduledJobSecurityPolicy {
  origin: "system" | "user";
  protected: boolean;
  execution_mode: "workspace_heartbeat" | "proactive_message";
  workspace_access: "heartbeat_md" | "none";
  tools_enabled: boolean;
  memory_context: "none";
  delivery_kind: "desktop_chat" | "none";
  suppress_noop_output: boolean;
  can_delete: boolean;
}

export interface ScheduledJob {
  id: string;
  name: string;
  prompt: string;
  lane: "auto" | "fast" | "slow";
  schedule_kind: "at" | "every" | "cron";
  run_at: string;
  interval_seconds: number;
  cron_expression: string;
  enabled: boolean;
  system: boolean;
  source: string;
  delivery_kind: "desktop_chat" | "none";
  delivery_user_id: string;
  delivery_conversation_id: string;
  created_at: string;
  updated_at: string;
  last_run_at: string;
  next_run_at: string;
  security_policy?: ScheduledJobSecurityPolicy;
  tags?: string[];
}

export interface JobRunResult {
  job_id: string;
  reason: "manual" | "scheduled";
  ok: boolean;
  response: string;
  next_run_at?: string;
  error?: string;
  delivery?: {
    kind: "desktop_chat" | "none";
    user_id?: string;
    conversation_id?: string;
    conversation_title?: string;
    error?: string;
  };
}

export type SkillKind =
  | "local_mcp"
  | "remote_agent"
  | "knowledge"
  | "workflow"
  | "channel_connector"
  | "unknown";

export type SkillRuntimeKind = "none" | "mcp" | "remote_agent" | "unknown";

export interface SkillSummary {
  id: string;
  name: string;
  version: string;
  publisher: string;
  description: string;
  kind: SkillKind;
  runtime_kind: SkillRuntimeKind;
  categories?: string[];
  tags?: string[];
  presentation?: {
    icon_text?: string;
    banner?: {
      variant?: "default" | "desktop" | "gemini" | "telegram" | "blueprint" | "agent" | "channel";
      title?: string;
      image?: string;
      accent?: string;
    };
  };
  config_schema?: {
    type?: string;
    required?: string[];
    properties?: Record<string, {
      type?: string;
      title?: string;
      format?: string;
      description?: string;
      default?: string | number;
      minimum?: number;
      maximum?: number;
    }>;
  };
  secrets?: Array<{
    name: string;
    field: string;
    title?: string;
    description?: string;
    required?: boolean;
    configured?: boolean;
  }>;
  activation?: {
    restart_required?: boolean;
    requires_azure_relay?: boolean;
    relay_function_path?: string;
    [key: string]: unknown;
  };
  deployment?: {
    skill_root_path?: string;
    readme_path?: string;
    docs_path?: string;
    infra_path?: string;
    runtime_path?: string;
    [key: string]: unknown;
  };
  permissions?: Record<string, unknown>;
  capabilities?: Array<{ id: string; description: string; prompt?: string }>;
  source?: {
    kind: "official" | "registry" | "package" | "unknown";
    path?: string;
    registry?: string;
    artifact?: Record<string, unknown>;
  };
  registry_status?: string;
  local_status?: "available" | "installed" | "configured" | "enabled" | string;
  external_deployment_required?: boolean;
  installed: boolean;
  enabled: boolean;
  configured: boolean;
  missing_required_fields: string[];
  config?: Record<string, unknown>;
  installed_at?: string;
  updated_at?: string;
}

export interface SkillListResponse {
  items: SkillSummary[];
}

export interface SkillMarketplaceSettings {
  schema_version?: string;
  registry_url: string;
  registry_auth_mode?: "none" | "function_key";
  registry_consumer_key_configured?: boolean;
  registry_admin_key_configured?: boolean;
  updated_at?: string;
}

export interface SkillRegistryProbeResult {
  status: "local_only" | "ok" | "error";
  registry_url: string;
  registry_auth_mode?: "none" | "function_key";
  registry_consumer_key_configured?: boolean;
  registry_admin_key_configured?: boolean;
  health_ok: boolean;
  catalog_ok: boolean;
  registry_name?: string;
  skill_count?: number;
  checked_at?: string;
  message?: string;
  error?: string;
}

export interface SkillRuntimeStatus {
  skill_id: string;
  skill_name: string;
  status: "connected" | "error";
  tool_count: number;
  message: string;
}

export interface RegistryVersionRecord {
  id: string;
  name: string;
  version: string;
  publisher: string;
  description: string;
  kind: SkillKind | string;
  runtime_kind: SkillRuntimeKind | string;
  status: "draft" | "approved" | "revoked";
  approved: boolean;
  artifact?: {
    filename?: string;
    sha256?: string;
    size_bytes?: number;
    files?: number;
    path?: string;
  };
  published_at?: string;
  published_by?: string;
  approved_at?: string;
  approved_by?: string;
  revoked_at?: string;
  revoked_by?: string;
  updated_at?: string;
  publish_source?: string;
  manifest_snapshot?: Record<string, unknown>;
  activation?: Record<string, unknown>;
  config_schema?: Record<string, unknown>;
  presentation?: Record<string, unknown>;
  capabilities?: Array<{ id: string; description: string; prompt?: string }>;
}

export interface RegistrySkillItem {
  id: string;
  name: string;
  publisher: string;
  kind: SkillKind | string;
  latest_version: string;
  approved_version: string;
  version_count: number;
  draft_count?: number;
  revoked_count?: number;
  versions?: RegistryVersionRecord[];
}

export interface RegistryOverview {
  schema_version?: string;
  registry: string;
  storage_backend?: "local" | "azure" | string;
  totals: {
    skills: number;
    versions: number;
    approved_skills: number;
    draft_versions: number;
    revoked_versions: number;
  };
  recent_versions?: RegistryVersionRecord[];
  items?: RegistrySkillItem[];
}

export interface RegistrySkillListResponse {
  schema_version?: string;
  registry: string;
  storage_backend?: "local" | "azure" | string;
  items: RegistrySkillItem[];
}

export interface RegistrySkillVersionResponse {
  schema_version?: string;
  registry: string;
  storage_backend?: "local" | "azure" | string;
  skill: {
    id: string;
    name: string;
    publisher: string;
    kind: SkillKind | string;
  };
  versions: RegistryVersionRecord[];
}

export interface RegistryBundlePreview {
  filename: string;
  bundle_path: string;
  sha256: string;
  size_bytes: number;
  files: number;
  manifest: Record<string, unknown>;
}
