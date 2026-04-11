import { useEffect, useState } from "react";

import adultMascot from "../../../img/azulclaw.png";
import babyMascot from "../../../img/hatching_azulclaw.png";

import { Sidebar } from "../components/Sidebar";
import { ChatShell } from "../features/chat/ChatShell";
import { HeartbeatsShell } from "../features/heartbeats/HeartbeatsShell";
import { HatchingShell } from "../features/hatching/HatchingShell";
import { MemoryShell } from "../features/memory/MemoryShell";
import { ProcessesShell } from "../features/processes/ProcessesShell";
import { SettingsShell } from "../features/settings/SettingsShell";
import { SkillsShell } from "../features/skills/SkillsShell";
import { WorkspaceShell } from "../features/workspace/WorkspaceShell";
import { loadHatching } from "../lib/api";
import type { AppView, HatchingProfile } from "../lib/contracts";
import { defaultHatchingProfile } from "../lib/mock-data";

function renderView(view: AppView, profile: HatchingProfile, setProfile: (p: HatchingProfile) => void) {
  switch (view) {
    case "hatching":
      return <HatchingShell profile={profile} onProfileSaved={setProfile} />;
    case "skills":
      return <SkillsShell />;
    case "processes":
      return <ProcessesShell />;
    case "heartbeats":
      return <HeartbeatsShell />;
    case "memory":
      return <MemoryShell />;
    case "workspace":
      return <WorkspaceShell />;
    case "settings":
      return <SettingsShell />;
    case "chat":
    default:
      return <ChatShell />;
  }
}

export function DesktopApp() {
  const [activeView, setActiveView] = useState<AppView>("chat");
  const [profile, setProfile] = useState<HatchingProfile>(defaultHatchingProfile);
  const [isBootstrapping, setIsBootstrapping] = useState(true);

  useEffect(() => {
    let isMounted = true;

    loadHatching().then((data) => {
      if (isMounted) {
        setProfile(data);
        setIsBootstrapping(false);
        if (!data.is_hatched) {
          setActiveView("hatching");
        }
      }
    });

    return () => {
      isMounted = false;
    };
  }, []);

  if (isBootstrapping) {
    return (
      <div className="onboarding-stage">
        <section className="onboarding-card">
          <img className="onboarding-mascot" src={babyMascot} alt="AzulClaw hatchling" />
          <p className="eyebrow">Wake up</p>
          <h1>Preparando el nido de AzulClaw</h1>
          <p>
            Cargando perfil, sandbox y estado del companion antes de abrir el
            escritorio.
          </p>
        </section>
      </div>
    );
  }

  if (!profile.is_hatched) {
    return (
      <HatchingShell
        profile={profile}
        onboardingRequired
        onProfileSaved={(saved) => {
          setProfile(saved);
          if (saved.is_hatched) {
            setActiveView("chat");
          }
        }}
      />
    );
  }

  return (
    <div className="desktop-frame">
      <Sidebar activeView={activeView} onNavigate={setActiveView} profile={profile} />
      <main className="desktop-main">
        <header className="desktop-topbar">
          <div className="topbar-identity">
            <img className="topbar-mascot" src={adultMascot} alt={profile.name} />
            <div>
              <p className="eyebrow">AzulClaw Desktop</p>
              <h1>{profile.name}, tu workspace viviente</h1>
            </div>
          </div>
          <div className="status-cluster">
            <span className="status-pill status-pill-live">Awake</span>
            <span className="status-pill">{profile.archetype}</span>
            <span className="status-pill">Local + Cloud</span>
          </div>
        </header>
        {renderView(activeView, profile, setProfile)}
      </main>
    </div>
  );
}
