const skills = [
  { name: "Email", risk: "moderate", description: "Read and draft emails." },
  { name: "Telegram", risk: "moderate", description: "Send messages and notifications." },
  { name: "Workspace", risk: "safe", description: "Manage sandbox files." },
  { name: "Terminal", risk: "sensitive", description: "Controlled local actions." },
];

export function SkillsShell() {
  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Skills</p>
            <h2>Agent capabilities</h2>
          </div>
          <button type="button" className="primary-button">
            Add skill
          </button>
        </div>

        <div className="skill-grid">
          {skills.map((skill) => (
            <article key={skill.name} className="subcard">
              <p className="eyebrow">{skill.risk}</p>
              <h3>{skill.name}</h3>
              <p>{skill.description}</p>
              <button type="button" className="ghost-button">
                Configure
              </button>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
