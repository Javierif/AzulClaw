const skills = [
  { name: "Email", risk: "moderate", description: "Leer y redactar correo." },
  { name: "Telegram", risk: "moderate", description: "Enviar mensajes y avisos." },
  { name: "Workspace", risk: "safe", description: "Gestionar archivos del sandbox." },
  { name: "Terminal", risk: "sensitive", description: "Acciones locales controladas." },
];

export function SkillsShell() {
  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Skills</p>
            <h2>Capacidades del agente</h2>
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
