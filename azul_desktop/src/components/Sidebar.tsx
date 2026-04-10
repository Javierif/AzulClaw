import type { AppView } from "../lib/contracts";

const navItems: { label: string; view: AppView }[] = [
  { label: "Chat", view: "chat" },
  { label: "Hatching", view: "hatching" },
  { label: "Skills", view: "skills" },
  { label: "Processes", view: "processes" },
  { label: "Memory", view: "memory" },
  { label: "Workspace", view: "workspace" },
  { label: "Settings", view: "settings" },
];

interface SidebarProps {
  activeView: AppView;
  onNavigate: (view: AppView) => void;
}

export function Sidebar({ activeView, onNavigate }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand-card">
        <div className="brand-avatar">AC</div>
        <div>
          <p className="eyebrow">Companion</p>
          <h2>AzulClaw</h2>
        </div>
      </div>

      <nav className="nav-list" aria-label="Primary navigation">
        {navItems.map((item) => (
          <button
            key={item.view}
            className={`nav-item${activeView === item.view ? " nav-item-active" : ""}`}
            type="button"
            onClick={() => onNavigate(item.view)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <section className="workspace-card">
        <p className="eyebrow">Sandbox</p>
        <h3>AzulWorkspace</h3>
        <p>C:\Users\javie\Desktop\AzulWorkspace</p>
      </section>
    </aside>
  );
}
