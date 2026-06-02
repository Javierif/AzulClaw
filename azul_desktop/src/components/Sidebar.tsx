import mascotIcon from "../../../img/azulclaw_ico.png";
import hatchlingIcon from "../../../img/hatching_azulclaw_ico.png";

import type { AppView, SetupProfile } from "../lib/contracts";

const navItems: { label: string; view: AppView; icon: "chat" | "marketplace" | "registry" | "heartbeats" | "context" | "settings" }[] = [
  { label: "Chat", view: "chat", icon: "chat" },
  { label: "Marketplace", view: "skills", icon: "marketplace" },
  { label: "Registry", view: "registry", icon: "registry" },
  { label: "Heartbeats", view: "heartbeats", icon: "heartbeats" },
  { label: "Context", view: "context", icon: "context" },
  { label: "Settings", view: "settings", icon: "settings" },
];

function SidebarIcon({ icon }: { icon: (typeof navItems)[number]["icon"] }) {
  const common = {
    width: 17,
    height: 17,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (icon) {
    case "marketplace":
      return (
        <svg {...common}>
          <path d="M4 10.5h16" />
          <path d="M6 10.5V19h12v-8.5" />
          <path d="M8 19v-5h8v5" />
          <path d="M5 10.5 7.2 5h9.6L19 10.5" />
        </svg>
      );
    case "heartbeats":
      return (
        <svg {...common}>
          <path d="M3 12h4l2-5 4 10 2-5h6" />
        </svg>
      );
    case "registry":
      return (
        <svg {...common}>
          <rect x="4" y="5" width="16" height="5" rx="2" />
          <rect x="4" y="14" width="16" height="5" rx="2" />
          <path d="M8 7.5h.01" />
          <path d="M8 16.5h.01" />
        </svg>
      );
    case "context":
      return (
        <svg {...common}>
          <path d="M7 3h7l4 4v14H7z" />
          <path d="M14 3v5h5" />
          <path d="M10 13h6" />
          <path d="M10 17h4" />
        </svg>
      );
    case "settings":
      return (
        <svg {...common}>
          <path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z" />
          <path d="M19 12a7.6 7.6 0 0 0-.1-1.1l2-1.5-2-3.4-2.4 1a7.8 7.8 0 0 0-1.9-1.1L14.3 3h-4.6l-.4 2.9A7.8 7.8 0 0 0 7.4 7l-2.4-1-2 3.4 2 1.5A7.6 7.6 0 0 0 5 12c0 .4 0 .8.1 1.1l-2 1.5 2 3.4 2.4-1c.6.5 1.2.8 1.9 1.1l.4 2.9h4.6l.4-2.9c.7-.3 1.3-.6 1.9-1.1l2.4 1 2-3.4-2-1.5c.1-.3.1-.7.1-1.1z" />
        </svg>
      );
    case "chat":
    default:
      return (
        <svg {...common}>
          <path d="M5 5h14v10H8l-4 4V5z" />
        </svg>
      );
  }
}

interface SidebarProps {
  activeView: AppView;
  profile: SetupProfile;
  registryAdminVisible?: boolean;
  onNavigate: (view: AppView) => void;
}

export function Sidebar({ activeView, onNavigate, profile, registryAdminVisible = false }: SidebarProps) {
  const avatarSrc = profile.is_hatched ? mascotIcon : hatchlingIcon;
  const items = registryAdminVisible ? navItems : navItems.filter((item) => item.view !== "registry");

  return (
    <aside className="sidebar">
      <div className="brand-card">
        <img className="brand-avatar-image" src={avatarSrc} alt="" aria-hidden="true" />
        <div>
          <p className="eyebrow">{profile.is_hatched ? profile.archetype : "Setup"}</p>
          <h2 style={{ fontSize: "1.2rem" }}>{profile.name}</h2>
        </div>
      </div>

      <nav className="nav-list" aria-label="Primary navigation">
        {items.map((item) => (
          <button
            key={item.view}
            className={`nav-item${activeView === item.view ? " nav-item-active" : ""}`}
            type="button"
            onClick={() => onNavigate(item.view)}
          >
            <span className="nav-icon"><SidebarIcon icon={item.icon} /></span>
            <span>{item.label}</span>
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
