import { isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { useEffect, useMemo, useState } from "react";

import { SectionTopbarPortal } from "../../components/SectionTopbarPortal";
import {
  approveRegistryVersion,
  inspectRegistryBundle,
  loadRegistryOverview,
  loadRegistrySkillVersions,
  loadRegistrySkills,
  loadSkillMarketplaceSettings,
  publishRegistryBundle,
  revokeRegistryVersion,
} from "../../lib/api";
import type {
  RegistryBundlePreview,
  RegistryOverview,
  RegistrySkillItem,
  RegistryVersionRecord,
  RegistrySkillVersionResponse,
  SkillMarketplaceSettings,
} from "../../lib/contracts";

type RegistryStatusFilter = "all" | "approved" | "draft" | "revoked" | "live" | "pending";
type RegistrySortMode = "activity" | "name" | "publisher";

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function formatDate(value?: string) {
  if (!value) return "Not set";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatBytes(value?: number) {
  const size = typeof value === "number" ? value : 0;
  if (!size) return "0 B";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function registryStatusTone(status: string) {
  if (status === "approved") return "status-done";
  if (status === "draft") return "status-waiting";
  if (status === "revoked") return "status-idle";
  return "status-idle";
}

function manifestString(value: unknown) {
  if (typeof value !== "string") return "";
  return value.trim();
}

function manifestStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => manifestString(item))
    .filter(Boolean);
}

function compareText(left: string, right: string) {
  return left.localeCompare(right, undefined, { sensitivity: "base" });
}

export function RegistryAdminShell({
  headerPortalTarget = null,
  onOpenMarketplaceSettings,
}: {
  headerPortalTarget?: HTMLElement | null;
  onOpenMarketplaceSettings?: () => void;
}) {
  const [settings, setSettings] = useState<SkillMarketplaceSettings>({ registry_url: "" });
  const [overview, setOverview] = useState<RegistryOverview | null>(null);
  const [skills, setSkills] = useState<RegistrySkillItem[]>([]);
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [selectedVersions, setSelectedVersions] = useState<RegistrySkillVersionResponse | null>(null);
  const [bundlePreview, setBundlePreview] = useState<RegistryBundlePreview | null>(null);
  const [bundlePath, setBundlePath] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<RegistryStatusFilter>("all");
  const [sortMode, setSortMode] = useState<RegistrySortMode>("activity");
  const [copiedValue, setCopiedValue] = useState("");

  const liveSkillCount = useMemo(
    () => skills.filter((skill) => Boolean(skill.approved_version)).length,
    [skills],
  );
  const pendingApprovalCount = useMemo(
    () => skills.filter((skill) => (skill.draft_count || 0) > 0).length,
    [skills],
  );
  const revokedSkillCount = useMemo(
    () => skills.filter((skill) => (skill.revoked_count || 0) > 0).length,
    [skills],
  );

  const headerContent = (
    <div className="section-topbar">
      <div className="section-topbar-copy">
        <p className="eyebrow">Registry</p>
        <h2 className="section-topbar-title">Registry Admin</h2>
      </div>
      <div className="section-topbar-actions filter-row">
        <span className="status-pill">live {liveSkillCount}</span>
        <span className="status-pill">drafts {overview?.totals.draft_versions ?? 0}</span>
        <span className="status-pill">approved {overview?.totals.approved_skills ?? 0}</span>
      </div>
    </div>
  );

  const adminAvailable = Boolean(
    settings.registry_url && settings.registry_auth_mode === "function_key" && settings.registry_admin_key_configured,
  );

  const registryHost = useMemo(() => {
    if (!settings.registry_url) return "";
    try {
      return new URL(settings.registry_url).host;
    } catch {
      return settings.registry_url;
    }
  }, [settings.registry_url]);

  async function refresh(selectedId = selectedSkillId) {
    setLoading(true);
    setError("");
    try {
      const nextSettings = await loadSkillMarketplaceSettings();
      setSettings(nextSettings);
      if (
        !nextSettings.registry_url ||
        nextSettings.registry_auth_mode !== "function_key" ||
        !nextSettings.registry_admin_key_configured
      ) {
        setOverview(null);
        setSkills([]);
        setSelectedVersions(null);
        setLoading(false);
        return;
      }
      const [overviewData, skillsData] = await Promise.all([loadRegistryOverview(), loadRegistrySkills()]);
      setOverview(overviewData);
      setSkills(skillsData.items);
      const resolvedId = selectedId || skillsData.items[0]?.id || "";
      setSelectedSkillId(resolvedId);
      if (resolvedId) {
        setSelectedVersions(await loadRegistrySkillVersions(resolvedId));
      } else {
        setSelectedVersions(null);
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not load Registry Admin.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filteredSkills = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const filtered = skills.filter((skill) => {
      if (statusFilter === "approved" && !skill.approved_version) return false;
      if (statusFilter === "draft" && !(skill.draft_count && skill.draft_count > 0)) return false;
      if (statusFilter === "revoked" && !(skill.revoked_count && skill.revoked_count > 0)) return false;
      if (statusFilter === "live" && !skill.approved_version) return false;
      if (statusFilter === "pending" && !(skill.draft_count && skill.draft_count > 0)) return false;
      if (!normalizedQuery) return true;
      return [
        skill.id,
        skill.name,
        skill.publisher,
        skill.kind,
        skill.latest_version,
        skill.approved_version,
      ]
        .join(" ")
        .toLowerCase()
        .includes(normalizedQuery);
    });
    const ranked = [...filtered];
    if (sortMode === "name") {
      ranked.sort((left, right) => compareText(left.name, right.name));
    } else if (sortMode === "publisher") {
      ranked.sort((left, right) => {
        const publisherDelta = compareText(left.publisher, right.publisher);
        return publisherDelta || compareText(left.name, right.name);
      });
    } else {
      ranked.sort((left, right) => {
        const leftScore =
          (left.approved_version ? 100 : 0) +
          ((left.draft_count || 0) > 0 ? 10 : 0) +
          (left.version_count || 0);
        const rightScore =
          (right.approved_version ? 100 : 0) +
          ((right.draft_count || 0) > 0 ? 10 : 0) +
          (right.version_count || 0);
        if (leftScore !== rightScore) return rightScore - leftScore;
        return compareText(left.name, right.name);
      });
    }
    return ranked;
  }, [query, skills, sortMode, statusFilter]);

  const selectedSkill = useMemo(
    () => filteredSkills.find((item) => item.id === selectedSkillId) ?? skills.find((item) => item.id === selectedSkillId) ?? null,
    [filteredSkills, selectedSkillId, skills],
  );

  const latestVersion = selectedVersions?.versions?.[0] ?? null;
  const recentVersions = overview?.recent_versions ?? [];

  function artifactDownloadUrl(filename?: string) {
    if (!filename || !settings.registry_url) return "";
    return `${settings.registry_url.replace(/\/$/, "")}/api/artifacts/${encodeURIComponent(filename)}`;
  }

  async function openBundlePicker() {
    setError("");
    setSuccess("");
    if (!isTauri()) {
      setError("Bundle picker is only available inside the Tauri desktop app.");
      return;
    }
    const selected = await open({
      multiple: false,
      filters: [{ name: "Azul skill bundle", extensions: ["azulskill"] }],
      title: "Select .azulskill bundle",
    });
    if (typeof selected !== "string" || !selected.trim()) return;
    setBundlePath(selected);
    setBusyAction("inspect");
    try {
      setBundlePreview(await inspectRegistryBundle(selected));
    } catch (nextError) {
      setBundlePreview(null);
      setError(nextError instanceof Error ? nextError.message : "Could not inspect bundle.");
    } finally {
      setBusyAction("");
    }
  }

  async function handlePublish() {
    if (!bundlePath) return;
    setBusyAction("publish");
    setError("");
    setSuccess("");
    try {
      const published = await publishRegistryBundle(bundlePath);
      setSuccess(`Published ${published.id}@${published.version} as draft.`);
      setBundlePreview(null);
      setBundlePath("");
      await refresh(published.id);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Publish failed.");
    } finally {
      setBusyAction("");
    }
  }

  async function handleApprove(skillId: string, version: string) {
    setBusyAction(`approve:${skillId}:${version}`);
    setError("");
    setSuccess("");
    try {
      await approveRegistryVersion(skillId, version);
      setSuccess(`Approved ${skillId}@${version}.`);
      await refresh(skillId);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Approve failed.");
    } finally {
      setBusyAction("");
    }
  }

  async function handleRevoke(skillId: string, version: string) {
    setBusyAction(`revoke:${skillId}:${version}`);
    setError("");
    setSuccess("");
    try {
      await revokeRegistryVersion(skillId, version);
      setSuccess(`Revoked ${skillId}@${version}.`);
      await refresh(skillId);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Revoke failed.");
    } finally {
      setBusyAction("");
    }
  }

  function copyText(value: string) {
    void navigator.clipboard.writeText(value);
    setCopiedValue(value);
    window.setTimeout(() => {
      setCopiedValue((current) => (current === value ? "" : current));
    }, 1200);
  }

  function renderBundlePreview(preview: RegistryBundlePreview) {
    const manifest = asRecord(preview.manifest);
    const capabilities = Array.isArray(manifest.capabilities) ? manifest.capabilities : [];
    const configSchema = asRecord(manifest.config_schema);
    const properties = typeof configSchema.properties === "object" && configSchema.properties
      ? Object.keys(configSchema.properties as Record<string, unknown>)
      : [];

    return (
      <div className="registry-admin-bundle-preview">
        <div className="registry-admin-bundle-grid">
          <div>
            <span>Bundle</span>
            <code>{preview.filename}</code>
          </div>
          <div>
            <span>Skill</span>
            <strong>{manifestString(manifest.name) || manifestString(manifest.id) || "Unknown"}</strong>
          </div>
          <div>
            <span>Version</span>
            <code>{manifestString(manifest.version) || "Unknown"}</code>
          </div>
          <div>
            <span>Kind</span>
            <strong>{manifestString(manifest.kind) || "Unknown"}</strong>
          </div>
          <div>
            <span>Publisher</span>
            <strong>{manifestString(manifest.publisher) || "Unknown"}</strong>
          </div>
          <div>
            <span>Bundle size</span>
            <strong>{formatBytes(preview.size_bytes)}</strong>
          </div>
        </div>
        <div className="registry-admin-inline-meta">
          <span className="status-pill">{preview.files} files</span>
          <span className="status-pill">{capabilities.length} capabilities</span>
          <span className="status-pill">{properties.length} config fields</span>
        </div>
        {manifestString(manifest.description) ? (
          <p className="registry-admin-version-desc">{manifestString(manifest.description)}</p>
        ) : null}
      </div>
    );
  }

  function renderRecentVersion(version: RegistryVersionRecord) {
    return (
      <button
        key={`${version.id}-${version.version}-${version.published_at || ""}`}
        type="button"
        className="registry-admin-recent-card"
        onClick={() => {
          setSelectedSkillId(version.id);
          void loadRegistrySkillVersions(version.id).then(setSelectedVersions).catch((nextError) => {
            setError(nextError instanceof Error ? nextError.message : "Could not load versions.");
          });
        }}
      >
        <div className="registry-admin-recent-head">
          <strong>{version.name}</strong>
          <span className={`status-tag ${registryStatusTone(version.status)}`}>{version.status}</span>
        </div>
        <div className="registry-admin-inline-meta">
          <span>{version.version}</span>
          <span>{version.kind}</span>
          <span>{formatDate(version.published_at)}</span>
        </div>
      </button>
    );
  }

  function buildOperationalChecklist(version: RegistryVersionRecord): string[] {
    const manifest = asRecord(version.manifest_snapshot);
    const activation = asRecord(manifest.activation);
    const deployment = asRecord(manifest.deployment);
    const notes: string[] = [];
    if (activation.restart_required === true) {
      notes.push("Requires a local runtime restart after install or update.");
    }
    if (activation.requires_azure_relay === true) {
      const relayPath = manifestString(activation.relay_function_path);
      notes.push(relayPath ? `Requires Azure relay deployment at ${relayPath}.` : "Requires Azure relay deployment.");
    }
    const resources = [
      manifestString(deployment.runtime_path),
      manifestString(deployment.infra_path),
      manifestString(deployment.docs_path),
    ].filter(Boolean);
    if (resources.length) {
      notes.push(`Declared resources: ${resources.join(" | ")}`);
    }
    if (version.kind === "remote_agent") {
      notes.push("Requires a reachable remote HTTPS endpoint before end users can use it.");
    }
    if (version.kind === "channel_connector") {
      notes.push("Requires external channel plumbing and validation before rolling out broadly.");
    }
    return notes;
  }

  function buildVersionBadges(version: RegistryVersionRecord, skill: RegistrySkillItem | null) {
    const manifest = asRecord(version.manifest_snapshot);
    const activation = asRecord(manifest.activation);
    const badges: string[] = [];
    if (skill?.approved_version === version.version) {
      badges.push("Live catalog version");
    }
    if (version.status === "draft") {
      badges.push("Pending approval");
    }
    if (activation.requires_azure_relay === true) {
      badges.push("Needs Azure relay");
    }
    if (version.kind === "remote_agent") {
      badges.push("Needs remote endpoint");
    }
    if (activation.restart_required === true) {
      badges.push("Needs restart");
    }
    return badges;
  }

  return (
    <section className="single-panel-layout">
      <SectionTopbarPortal
        target={headerPortalTarget}
        fallback={<div className="section-page-header-fallback">{headerContent}</div>}
      >
        {headerContent}
      </SectionTopbarPortal>

      <div className="registry-admin-shell">
        {!adminAvailable ? (
          <section className="registry-admin-empty">
            <p className="eyebrow">Registry Admin</p>
            <h3>Admin access is not configured</h3>
            <p>
              Add a Skill Registry URL and an admin key in Settings to publish bundles, approve versions, and control
              the company catalog.
            </p>
            {onOpenMarketplaceSettings ? (
              <button type="button" className="primary-button" onClick={onOpenMarketplaceSettings}>
                Open Marketplace settings
              </button>
            ) : null}
          </section>
        ) : (
          <>
            <div className="marketplace-registry-banner marketplace-registry-banner-ready">
              <div className="marketplace-registry-copy">
                <span className="marketplace-registry-dot" />
                <div>
                  <strong>Registry connected: {registryHost || overview?.registry || "configured"}</strong>
                  <p>
                    Publish drafts, review version history, and decide which approved skills appear in the company
                    marketplace.
                  </p>
                </div>
              </div>
              <div className="registry-admin-inline-actions">
                {onOpenMarketplaceSettings ? (
                  <button type="button" className="ghost-button" onClick={onOpenMarketplaceSettings}>
                    Registry settings
                  </button>
                ) : null}
                <button type="button" className="ghost-button" onClick={() => void refresh()}>
                  Refresh registry
                </button>
              </div>
            </div>

            <div className="registry-admin-overview">
              <article className="registry-admin-metric">
                <span>Skills</span>
                <strong>{overview?.totals.skills ?? 0}</strong>
              </article>
              <article className="registry-admin-metric">
                <span>Versions</span>
                <strong>{overview?.totals.versions ?? 0}</strong>
              </article>
              <article className="registry-admin-metric">
                <span>Drafts</span>
                <strong>{overview?.totals.draft_versions ?? 0}</strong>
              </article>
              <article className="registry-admin-metric">
                <span>Approved</span>
                <strong>{overview?.totals.approved_skills ?? 0}</strong>
              </article>
              <article className="registry-admin-metric">
                <span>Live in catalog</span>
                <strong>{liveSkillCount}</strong>
              </article>
              <article className="registry-admin-metric">
                <span>Need approval</span>
                <strong>{pendingApprovalCount}</strong>
              </article>
              <article className="registry-admin-metric">
                <span>With revoked</span>
                <strong>{revokedSkillCount}</strong>
              </article>
              <article className="registry-admin-metric">
                <span>Backend</span>
                <strong>{overview?.storage_backend ?? "unknown"}</strong>
              </article>
            </div>

            <section className="registry-admin-publish">
              <div className="registry-admin-section-head">
                <div>
                  <p className="runtime-kv-section-title">Publish draft</p>
                  <p className="marketplace-subtitle">
                    Select a packaged <code>.azulskill</code>, preview its manifest, then publish it to the company
                    registry as draft.
                  </p>
                </div>
                <div className="registry-admin-publish-actions">
                  <button type="button" className="ghost-button" onClick={() => void openBundlePicker()} disabled={busyAction === "inspect"}>
                    {busyAction === "inspect" ? "Inspecting..." : "Select bundle"}
                  </button>
                  <button
                    type="button"
                    className="primary-button"
                    onClick={() => void handlePublish()}
                    disabled={!bundlePreview || busyAction === "publish"}
                  >
                    {busyAction === "publish" ? "Publishing..." : "Publish draft"}
                  </button>
                </div>
              </div>
              {bundlePreview ? renderBundlePreview(bundlePreview) : null}
            </section>

            {success ? <p className="hw-inline-note">{success}</p> : null}
            {error ? <p className="hw-inline-note hw-inline-note-warning">{error}</p> : null}

            {loading ? (
              <p className="muted-text">Loading registry administration...</p>
            ) : (
              <>
                {recentVersions.length ? (
                  <section className="registry-admin-recent">
                    <div className="registry-admin-section-head">
                      <p className="runtime-kv-section-title">Recent registry activity</p>
                    </div>
                    <div className="registry-admin-recent-list">
                      {recentVersions.map(renderRecentVersion)}
                    </div>
                  </section>
                ) : null}

                <div className="registry-admin-filters">
                  <input
                    className="skill-search-input"
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search registered skills"
                  />
                  <select
                    className="skill-filter-select"
                    value={statusFilter}
                    onChange={(event) => setStatusFilter(event.target.value as RegistryStatusFilter)}
                  >
                    <option value="all">All states</option>
                    <option value="live">Live in catalog</option>
                    <option value="approved">Approved</option>
                    <option value="draft">With drafts</option>
                    <option value="pending">Pending approval</option>
                    <option value="revoked">With revoked</option>
                  </select>
                  <select
                    className="skill-filter-select"
                    value={sortMode}
                    onChange={(event) => setSortMode(event.target.value as RegistrySortMode)}
                  >
                    <option value="activity">Sort by activity</option>
                    <option value="name">Sort by name</option>
                    <option value="publisher">Sort by publisher</option>
                  </select>
                </div>

                <div className="registry-admin-grid">
                  <section className="registry-admin-list">
                    <div className="registry-admin-section-head">
                      <p className="runtime-kv-section-title">Registered skills</p>
                      <span className="status-pill">{filteredSkills.length} visible</span>
                    </div>
                    <div className="registry-admin-skill-list">
                      {filteredSkills.map((skill) => (
                        <button
                          key={skill.id}
                          type="button"
                          className={`registry-admin-skill-item${selectedSkillId === skill.id ? " registry-admin-skill-item-active" : ""}`}
                          onClick={() => {
                            setSelectedSkillId(skill.id);
                            void loadRegistrySkillVersions(skill.id).then(setSelectedVersions).catch((nextError) => {
                              setError(nextError instanceof Error ? nextError.message : "Could not load versions.");
                            });
                          }}
                        >
                          <div>
                            <strong>{skill.name}</strong>
                            <span>{skill.publisher}</span>
                            <div className="registry-admin-inline-meta">
                              <span>{skill.kind}</span>
                              <span>{skill.version_count} versions</span>
                            </div>
                            <div className="registry-admin-chip-row">
                              {skill.approved_version ? <span className="skill-detail-chip">Live</span> : null}
                              {(skill.draft_count || 0) > 0 ? <span className="skill-detail-chip">Drafts</span> : null}
                              {(skill.revoked_count || 0) > 0 ? <span className="skill-detail-chip">Revoked history</span> : null}
                            </div>
                          </div>
                          <div className="registry-admin-skill-meta">
                            <span>{skill.latest_version || "No versions"}</span>
                            <span>{skill.approved_version ? `approved ${skill.approved_version}` : "no approved version"}</span>
                            {(skill.draft_count || 0) > 0 ? <span>{skill.draft_count} drafts</span> : null}
                          </div>
                        </button>
                      ))}
                      {filteredSkills.length === 0 ? <p className="muted-text">No skills match the current filter.</p> : null}
                    </div>
                  </section>

                  <section className="registry-admin-detail">
                    <div className="registry-admin-section-head">
                      <div>
                        <p className="runtime-kv-section-title">Skill detail</p>
                        <h3 className="registry-admin-detail-title">{selectedSkill?.name ?? "Select a skill"}</h3>
                      </div>
                      {selectedSkill ? (
                        <div className="registry-admin-inline-actions">
                          <span className="status-pill">{selectedSkill.kind}</span>
                          {selectedSkill.approved_version ? (
                            <span className="status-pill">live {selectedSkill.approved_version}</span>
                          ) : (
                            <span className="status-pill">not published</span>
                          )}
                          <button
                            type="button"
                            className="marketplace-resource-button"
                            onClick={() => copyText(selectedSkill.id)}
                          >
                            {copiedValue === selectedSkill.id ? "Copied" : "Copy id"}
                          </button>
                        </div>
                      ) : null}
                    </div>

                    {latestVersion ? (
                      <div className="registry-admin-summary-card">
                        <div className="registry-admin-summary-grid">
                          <div>
                            <span>Latest version</span>
                            <strong>{latestVersion.version}</strong>
                          </div>
                          <div>
                            <span>Current state</span>
                            <span className={`status-tag ${registryStatusTone(latestVersion.status)}`}>{latestVersion.status}</span>
                          </div>
                          <div>
                            <span>Published by</span>
                            <strong>{latestVersion.published_by || "Unknown"}</strong>
                          </div>
                          <div>
                            <span>Artifact</span>
                            <code>{latestVersion.artifact?.filename || "Unknown"}</code>
                          </div>
                          <div>
                            <span>Draft versions</span>
                            <strong>{selectedSkill?.draft_count ?? 0}</strong>
                          </div>
                          <div>
                            <span>Revoked versions</span>
                            <strong>{selectedSkill?.revoked_count ?? 0}</strong>
                          </div>
                        </div>
                        <div className="registry-admin-inline-actions">
                          {latestVersion.artifact?.filename ? (
                            <button
                              type="button"
                              className="marketplace-resource-button"
                              onClick={() => copyText(latestVersion.artifact?.filename || "")}
                            >
                              {copiedValue === latestVersion.artifact?.filename ? "Copied" : "Copy artifact"}
                            </button>
                          ) : null}
                          {artifactDownloadUrl(latestVersion.artifact?.filename) ? (
                            <button
                              type="button"
                              className="marketplace-resource-button"
                              onClick={() => copyText(artifactDownloadUrl(latestVersion.artifact?.filename))}
                            >
                              {copiedValue === artifactDownloadUrl(latestVersion.artifact?.filename) ? "Copied" : "Copy download URL"}
                            </button>
                          ) : null}
                        </div>
                        {latestVersion.description ? <p className="registry-admin-version-desc">{latestVersion.description}</p> : null}
                        {latestVersion.capabilities?.length ? (
                          <div className="registry-admin-chip-row">
                            {latestVersion.capabilities.map((capability) => (
                              <span key={capability.id} className="skill-detail-chip">{capability.description}</span>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ) : null}

                    {selectedVersions ? (
                      <div className="registry-admin-version-list">
                        {selectedVersions.versions.map((version) => {
                          const manifest = asRecord(version.manifest_snapshot);
                          const runtime = asRecord(manifest.runtime);
                          const deployment = asRecord(manifest.deployment);
                          const isLiveVersion =
                            Boolean(selectedSkill?.approved_version) && selectedSkill?.approved_version === version.version;
                          const versionCoordinate = `${selectedVersions.skill.id}@${version.version}`;
                          const operationalChecklist = buildOperationalChecklist(version);
                          const versionBadges = buildVersionBadges(version, selectedSkill);
                          const manifestTags = manifestStringArray(manifest.tags);
                          return (
                            <article key={`${version.id}-${version.version}`} className="registry-admin-version-card">
                              <div className="registry-admin-version-head">
                                <div>
                                  <strong>{version.version}</strong>
                                  <div className="registry-admin-inline-actions">
                                    <span className={`status-tag ${registryStatusTone(version.status)}`}>{version.status}</span>
                                    {isLiveVersion ? <span className="status-pill">Live catalog version</span> : null}
                                  </div>
                                </div>
                                <div className="registry-admin-version-actions">
                                  <button
                                    type="button"
                                    className="ghost-button"
                                    onClick={() => void handleApprove(selectedVersions.skill.id, version.version)}
                                    disabled={busyAction === `approve:${selectedVersions.skill.id}:${version.version}` || version.status === "approved"}
                                  >
                                    {busyAction === `approve:${selectedVersions.skill.id}:${version.version}` ? "Approving..." : "Approve"}
                                  </button>
                                  <button
                                    type="button"
                                    className="ghost-button"
                                    onClick={() => void handleRevoke(selectedVersions.skill.id, version.version)}
                                    disabled={busyAction === `revoke:${selectedVersions.skill.id}:${version.version}` || version.status === "revoked"}
                                  >
                                    {busyAction === `revoke:${selectedVersions.skill.id}:${version.version}` ? "Revoking..." : "Revoke"}
                                  </button>
                                </div>
                              </div>
                              {versionBadges.length ? (
                                <div className="registry-admin-chip-row">
                                  {versionBadges.map((badge) => (
                                    <span key={`${version.id}-${badge}`} className="skill-detail-chip">{badge}</span>
                                  ))}
                                </div>
                              ) : null}
                              <div className="registry-admin-version-grid">
                                <div>
                                  <span>Artifact</span>
                                  <code>{version.artifact?.filename || "Unknown"}</code>
                                </div>
                                <div>
                                  <span>SHA-256</span>
                                  <code>{version.artifact?.sha256 || "Unknown"}</code>
                                </div>
                                <div>
                                  <span>Published</span>
                                  <strong>{formatDate(version.published_at)}</strong>
                                </div>
                                <div>
                                  <span>Approved</span>
                                  <strong>{formatDate(version.approved_at)}</strong>
                                </div>
                                <div>
                                  <span>Runtime kind</span>
                                  <strong>{version.runtime_kind || manifestString(runtime.kind) || "Unknown"}</strong>
                                </div>
                                <div>
                                  <span>Artifact size</span>
                                  <strong>{formatBytes(version.artifact?.size_bytes)}</strong>
                                </div>
                              </div>
                              <div className="registry-admin-inline-actions">
                                <button
                                  type="button"
                                  className="marketplace-resource-button"
                                  onClick={() => copyText(versionCoordinate)}
                                >
                                  {copiedValue === versionCoordinate ? "Copied" : "Copy skill@version"}
                                </button>
                                {version.artifact?.sha256 ? (
                                  <button
                                    type="button"
                                    className="marketplace-resource-button"
                                    onClick={() => copyText(version.artifact?.sha256 || "")}
                                  >
                                    {copiedValue === version.artifact?.sha256 ? "Copied" : "Copy SHA-256"}
                                  </button>
                                ) : null}
                                {artifactDownloadUrl(version.artifact?.filename) ? (
                                  <button
                                    type="button"
                                    className="marketplace-resource-button"
                                    onClick={() => copyText(artifactDownloadUrl(version.artifact?.filename))}
                                  >
                                    {copiedValue === artifactDownloadUrl(version.artifact?.filename) ? "Copied" : "Copy artifact URL"}
                                  </button>
                                ) : null}
                              </div>
                              {version.description ? <p className="registry-admin-version-desc">{version.description}</p> : null}
                              {operationalChecklist.length ? (
                                <div className="registry-admin-checklist">
                                  {operationalChecklist.map((note) => (
                                    <p key={note}>{note}</p>
                                  ))}
                                </div>
                              ) : null}
                              {manifestTags.length ? (
                                <div className="registry-admin-chip-row">
                                  {manifestTags.map((tag) => (
                                    <span key={tag} className="skill-detail-chip">{tag}</span>
                                  ))}
                                </div>
                              ) : null}
                              {(manifestString(deployment.runtime_path) || manifestString(deployment.infra_path) || manifestString(deployment.docs_path)) ? (
                                <div className="marketplace-resource-list">
                                  {manifestString(deployment.runtime_path) ? (
                                    <div className="marketplace-resource-row">
                                      <div className="marketplace-resource-copy">
                                        <span className="marketplace-resource-label">Runtime</span>
                                        <span className="marketplace-resource-path">{manifestString(deployment.runtime_path)}</span>
                                      </div>
                                      <button
                                        type="button"
                                        className="marketplace-resource-button"
                                        onClick={() => copyText(manifestString(deployment.runtime_path))}
                                      >
                                        {copiedValue === manifestString(deployment.runtime_path) ? "Copied" : "Copy"}
                                      </button>
                                    </div>
                                  ) : null}
                                  {manifestString(deployment.infra_path) ? (
                                    <div className="marketplace-resource-row">
                                      <div className="marketplace-resource-copy">
                                        <span className="marketplace-resource-label">Terraform</span>
                                        <span className="marketplace-resource-path">{manifestString(deployment.infra_path)}</span>
                                      </div>
                                      <button
                                        type="button"
                                        className="marketplace-resource-button"
                                        onClick={() => copyText(manifestString(deployment.infra_path))}
                                      >
                                        {copiedValue === manifestString(deployment.infra_path) ? "Copied" : "Copy"}
                                      </button>
                                    </div>
                                  ) : null}
                                  {manifestString(deployment.docs_path) ? (
                                    <div className="marketplace-resource-row">
                                      <div className="marketplace-resource-copy">
                                        <span className="marketplace-resource-label">Docs</span>
                                        <span className="marketplace-resource-path">{manifestString(deployment.docs_path)}</span>
                                      </div>
                                      <button
                                        type="button"
                                        className="marketplace-resource-button"
                                        onClick={() => copyText(manifestString(deployment.docs_path))}
                                      >
                                        {copiedValue === manifestString(deployment.docs_path) ? "Copied" : "Copy"}
                                      </button>
                                    </div>
                                  ) : null}
                                </div>
                              ) : null}
                              <div className="registry-admin-inline-meta">
                                <span>id {version.id}</span>
                                {version.publish_source ? <span>{version.publish_source}</span> : null}
                                {version.published_by ? <span>by {version.published_by}</span> : null}
                              </div>
                            </article>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="muted-text">Select a published skill to inspect its versions.</p>
                    )}
                  </section>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}
