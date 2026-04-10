export function SettingsShell() {
  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Settings</p>
            <h2>Soul and system</h2>
          </div>
        </div>

        <div className="three-column-grid">
          <section className="subcard">
            <p className="eyebrow">Identity</p>
            <h3>AzulClaw</h3>
            <p>Editar nombre, tono y rol base.</p>
          </section>
          <section className="subcard">
            <p className="eyebrow">Models</p>
            <h3>Local + Cloud</h3>
            <p>Configurar proveedores y estrategia de fallback.</p>
          </section>
          <section className="subcard">
            <p className="eyebrow">Security</p>
            <h3>Human approvals</h3>
            <p>Gestionar permisos sensibles y el sandbox.</p>
          </section>
        </div>
      </div>
    </section>
  );
}
