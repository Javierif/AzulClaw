import { useState } from "react";

import { Sidebar } from "../components/Sidebar";
import { ChatShell } from "../features/chat/ChatShell";
import { HatchingShell } from "../features/hatching/HatchingShell";
import { MemoryShell } from "../features/memory/MemoryShell";
import { ProcessesShell } from "../features/processes/ProcessesShell";
import { SettingsShell } from "../features/settings/SettingsShell";
import { SkillsShell } from "../features/skills/SkillsShell";
import { WorkspaceShell } from "../features/workspace/WorkspaceShell";
import type { AppView } from "../lib/contracts";

function renderView(view: AppView) {
  switch (view) {
    case "hatching":
      return <HatchingShell />;
    case "skills":
      return <SkillsShell />;
    case "processes":
      return <ProcessesShell />;
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

  return (
    <div className="desktop-frame">
      <Sidebar activeView={activeView} onNavigate={setActiveView} />
      <main className="desktop-main">
        <header className="desktop-topbar">
          <div>
            <p className="eyebrow">AzulClaw Desktop</p>
            <h1>Workspace viviente para tu agente local</h1>
          </div>
          <div className="status-cluster">
            <span className="status-pill status-pill-live">Awake</span>
            <span className="status-pill">Local + Cloud</span>
          </div>
        </header>
        {renderView(activeView)}
      </main>
    </div>
  );
}
