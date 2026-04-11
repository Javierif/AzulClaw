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
            <p>Edit name, tone and base role.</p>
          </section>
          <section className="subcard">
            <p className="eyebrow">Models</p>
            <h3>Local + Cloud</h3>
            <p>Configure providers and fallback strategy.</p>
          </section>
          <section className="subcard">
            <p className="eyebrow">Security</p>
            <h3>Human approvals</h3>
            <p>Manage sensitive permissions and the sandbox.</p>
          </section>
        </div>
      </div>
    </section>
  );
}
