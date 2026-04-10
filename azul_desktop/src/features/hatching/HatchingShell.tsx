const traits = [
  "Directo",
  "Explicativo",
  "Autonomo moderado",
  "Tecnico",
];

const skills = ["Email", "Telegram", "Workspace", "Memory"];

export function HatchingShell() {
  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">
        <div className="split-hero">
          <div className="hero-figure">
            <div className="hero-badge">AC</div>
            <p className="eyebrow">Hatching</p>
            <h2>Vamos a criar tu AzulClaw</h2>
            <p>
              Define personalidad, skills y reglas de seguridad antes de que el
              agente empiece a operar.
            </p>
          </div>

          <div className="hero-summary">
            <div className="summary-card">
              <p className="eyebrow">Personalidad</p>
              <ul className="chip-list">
                {traits.map((trait) => (
                  <li key={trait} className="chip">
                    {trait}
                  </li>
                ))}
              </ul>
            </div>

            <div className="summary-card">
              <p className="eyebrow">Skills iniciales</p>
              <ul className="chip-list">
                {skills.map((skill) => (
                  <li key={skill} className="chip">
                    {skill}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>

        <div className="three-column-grid">
          <section className="subcard">
            <p className="eyebrow">Identidad</p>
            <h3>AzulClaw Companion</h3>
            <p>Rol: companero tecnico local</p>
            <p>Mision: ayudarte sin perder seguridad ni contexto.</p>
          </section>

          <section className="subcard">
            <p className="eyebrow">Sandbox</p>
            <h3>AzulWorkspace</h3>
            <p>Ruta segura para que el agente gestione ficheros sin salir de su jaula.</p>
            <code>C:\Users\javie\Desktop\AzulWorkspace</code>
          </section>

          <section className="subcard">
            <p className="eyebrow">Politica</p>
            <h3>Control humano</h3>
            <p>Las acciones sensibles siguen requiriendo confirmacion explicita.</p>
          </section>
        </div>
      </div>
    </section>
  );
}
