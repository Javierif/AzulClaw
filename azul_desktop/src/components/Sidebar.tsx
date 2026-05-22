import mascotIcon from "../../../img/azulclaw_ico.png";
import hatchlingIcon from "../../../img/hatching_azulclaw_ico.png";

import type { AppView, SetupProfile } from "../lib/contracts";

const navItems: { label: string; view: AppView }[] = [
  { label: "Chat", view: "chat" },
  { label: "Skills", view: "skills" },
  { label: "Heartbeats", view: "heartbeats" },
  { label: "Context", view: "context" },
  { label: "Settings", view: "settings" },
];

interface SidebarProps {
  activeView: AppView;
  profile: SetupProfile;
  onNavigate: (view: AppView) => void;
}

export function Sidebar({ activeView, onNavigate, profile }: SidebarProps) {
  const avatarSrc = profile.is_hatched ? mascotIcon : hatchlingIcon;

  return (
    <aside className="sidebar">
      <div className="brand-card">
        <div>
          <p className="eyebrow">{profile.is_hatched ? profile.archetype : "Setup"}</p>
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

      <section className="sidebar-account">
        <button type="button" className="disconnect-btn">
          Disconnect
        </button>
      </section>
    </aside>
  );
}
