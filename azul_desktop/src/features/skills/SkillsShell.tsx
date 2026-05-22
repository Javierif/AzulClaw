import { SectionTopbarPortal } from "../../components/SectionTopbarPortal";

const skills = [
  {
    name: "Email",
    risk: "moderate",
    riskClass: "status-waiting",
    description: "Read and draft emails from a connected account.",
    configured: false,
  },
  {
    name: "Telegram",
    risk: "moderate",
    riskClass: "status-waiting",
    description: "Send notifications and operate through a bot.",
    configured: false,
  },
  {
    name: "Workspace",
    risk: "safe",
    riskClass: "status-done",
    description: "Read, write and organise files within the sandbox.",
    configured: true,
  },
  {
    name: "Terminal",
    risk: "sensitive",
    riskClass: "status-failed",
    description: "Execute controlled local actions — requires confirmation.",
    configured: false,
  },
];

export function SkillsShell({
  headerPortalTarget = null,
}: {
  headerPortalTarget?: HTMLElement | null;
}) {
  const headerContent = (
    <div className="section-topbar">
      <div className="section-topbar-copy">
        <p className="eyebrow">Skills</p>
        <h2 className="section-topbar-title">Agent capabilities</h2>
      </div>
      <div className="section-topbar-actions">
        <button type="button" className="primary-button">Add skill</button>
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
          {skills.map((skill) => (
            <article key={skill.name} className={`subcard skill-card${skill.configured ? " skill-card-configured" : ""}`}>
              <div className="skill-card-top">
                <span className={`status-tag ${skill.riskClass}`}>{skill.risk}</span>
                {skill.configured ? <span className="skill-badge-active">Active</span> : null}
              </div>
              <h3 className="skill-card-name">{skill.name}</h3>
              <p className="skill-card-desc">{skill.description}</p>
              <div className="skill-card-action">
                <button type="button" className="skill-action-btn">
                  {skill.configured ? "Reconfigure" : "Configure"} →
                </button>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
