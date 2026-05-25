import { useTranslation } from "react-i18next";

import { SectionTopbarPortal } from "../../components/SectionTopbarPortal";

const SKILL_NAMES = ["Email", "Telegram", "Workspace", "Terminal"] as const;
const SKILL_RISK: Record<string, string> = {
  Email: "moderate",
  Telegram: "moderate",
  Workspace: "safe",
  Terminal: "sensitive",
};
const SKILL_RISK_CLASS: Record<string, string> = {
  moderate: "status-waiting",
  safe: "status-done",
  sensitive: "status-failed",
};
const SKILL_CONFIGURED: Record<string, boolean> = {
  Email: false,
  Telegram: false,
  Workspace: true,
  Terminal: false,
};

export function SkillsShell({
  headerPortalTarget = null,
}: {
  headerPortalTarget?: HTMLElement | null;
}) {
  const { t } = useTranslation();

  const headerContent = (
    <div className="section-topbar">
      <div className="section-topbar-copy">
        <p className="eyebrow">{t("skills.eyebrow")}</p>
        <h2 className="section-topbar-title">{t("skills.agentCapabilities")}</h2>
      </div>
      <div className="section-topbar-actions">
        <button type="button" className="primary-button">{t("skills.addSkill")}</button>
      </div>
    </div>
  );

  return (
    <section className="single-panel-layout">
      <SectionTopbarPortal
        target={headerPortalTarget}
        fallback={<div className="section-page-header-fallback">{headerContent}</div>}
      >
        {headerContent}
      </SectionTopbarPortal>
      <div className="card panel-stack">
        <div className="skill-grid">
          {SKILL_NAMES.map((name) => {
            const risk = SKILL_RISK[name];
            const riskClass = SKILL_RISK_CLASS[risk];
            const configured = SKILL_CONFIGURED[name];
            return (
              <article key={name} className={`subcard skill-card${configured ? " skill-card-configured" : ""}`}>
                <div className="skill-card-top">
                  <span className={`status-tag ${riskClass}`}>{t(`skills.risk.${risk}`)}</span>
                  {configured ? <span className="skill-badge-active">{t("common.active")}</span> : null}
                </div>
                <h3 className="skill-card-name">{name}</h3>
                <p className="skill-card-desc">{t(`skills.descriptions.${name}`)}</p>
                <div className="skill-card-action">
                  <button type="button" className="skill-action-btn">
                    {configured ? t("common.reconfigure") : t("common.configure")} →
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
