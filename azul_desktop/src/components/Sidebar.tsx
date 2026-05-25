import { useTranslation } from "react-i18next";

import mascotIcon from "../../../img/azulclaw_ico.png";
import hatchlingIcon from "../../../img/hatching_azulclaw_ico.png";

import type { AppView, SetupProfile } from "../lib/contracts";

interface SidebarProps {
  activeView: AppView;
  profile: SetupProfile;
  onNavigate: (view: AppView) => void;
}

export function Sidebar({ activeView, onNavigate, profile }: SidebarProps) {
  const { t } = useTranslation();
  const avatarSrc = profile.is_hatched ? mascotIcon : hatchlingIcon;

  const navItems: { labelKey: string; view: AppView }[] = [
    { labelKey: "nav.chat", view: "chat" },
    { labelKey: "nav.skills", view: "skills" },
    { labelKey: "nav.heartbeats", view: "heartbeats" },
    { labelKey: "nav.context", view: "context" },
    { labelKey: "nav.settings", view: "settings" },
  ];

  return (
    <aside className="sidebar">
      <div className="brand-card">
        <div>
          <p className="eyebrow">{profile.is_hatched ? profile.archetype : t("nav.setup")}</p>
          <h2 style={{ fontSize: "1.2rem" }}>{profile.name}</h2>
        </div>
      </div>

      <nav className="nav-list" aria-label={t("nav.primaryNavigation")}>
        {navItems.map((item) => (
          <button
            key={item.view}
            className={`nav-item${activeView === item.view ? " nav-item-active" : ""}`}
            type="button"
            onClick={() => onNavigate(item.view)}
          >
            {t(item.labelKey)}
          </button>
        ))}
      </nav>

      <section className="sidebar-account">
        <button type="button" className="disconnect-btn">
          {t("nav.disconnect")}
        </button>
      </section>
    </aside>
  );
}
