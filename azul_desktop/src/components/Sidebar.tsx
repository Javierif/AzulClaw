import mascotIcon from "../../../img/azulclaw_ico.png";
import hatchlingIcon from "../../../img/hatching_azulclaw_ico.png";

import type { AppView, HatchingProfile } from "../lib/contracts";

const navItems: { label: string; view: AppView }[] = [
  { label: "Chat", view: "chat" },
  { label: "Hatching", view: "hatching" },
  { label: "Skills", view: "skills" },
  { label: "Processes", view: "processes" },
  { label: "Runtime", view: "runtime" },
  { label: "Memory", view: "memory" },
  { label: "Workspace", view: "workspace" },
  { label: "Settings", view: "settings" },
];

interface SidebarProps {
  activeView: AppView;
  profile: HatchingProfile;
  onNavigate: (view: AppView) => void;
}

export function Sidebar({ activeView, onNavigate, profile }: SidebarProps) {
  const avatarSrc = profile.is_hatched ? mascotIcon : hatchlingIcon;

  return (
    <aside className="sidebar">
      <div className="brand-card">
        <div>
          <p className="eyebrow">{profile.is_hatched ? profile.archetype : "Hatching"}</p>
          <h2 style={{ fontSize: "1.2rem" }}>{profile.name}</h2>
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
        <p>{profile.workspace_root}</p>
      </section>
    </aside>
  );
}
