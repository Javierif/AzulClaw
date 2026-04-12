import { useCallback, useEffect, useRef, useState } from "react";

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

const THINKING_SENTENCES = [
  "Connecting the dots...",
  "Pulling the threads together.",
  "Reading between the lines.",
  "On it. Give me a moment.",
  "Thinking this through carefully.",
  "Running the cognitive layer.",
  "Parsing your request.",
  "Consulting the knowledge base.",
  "Firing up the slow brain.",
  "Cross-referencing context.",
  "Let me think about that.",
  "Assembling a response.",
  "Checking what I know.",
  "Working through it step by step.",
  "Almost there, stay with me.",
];

const TYPING_SENTENCES = [
  "Oh, you're typing... interesting.",
  "I see those fingers moving.",
  "Go on, I'm listening.",
  "Hmm, what's on your mind?",
  "Drafting something? I'm ready.",
  "I'm all ears.",
  "Take your time.",
  "Whenever you're ready.",
  "Something's coming my way...",
  "I can feel a question forming.",
];

function renderView(
  view: AppView,
  profile: HatchingProfile,
  setProfile: (p: HatchingProfile) => void,
  onThinkingChange: (thinking: boolean) => void,
  onTypingChange: (typing: boolean) => void,
  onAnswerStart: () => void,
) {
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
      return (
        <ChatShell
          onThinkingChange={onThinkingChange}
          onTypingChange={onTypingChange}
          onAnswerStart={onAnswerStart}
        />
      );
  }
}

export function DesktopApp() {
  const [activeView, setActiveView] = useState<AppView>("chat");
  const [profile, setProfile] = useState<HatchingProfile>(defaultHatchingProfile);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [topbarLabel, setTopbarLabel] = useState<{ text: string; mode: "thinking" | "typing" } | null>(null);
  const thinkingIntervalRef = useRef<number | null>(null);
  const typingClearRef = useRef<number | null>(null);
  const sentenceIndexRef = useRef(0);
  const isThinkingRef = useRef(false);

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

  const onThinkingChange = useCallback((thinking: boolean) => {
    isThinkingRef.current = thinking;
    if (thinking) {
      sentenceIndexRef.current = Math.floor(Math.random() * THINKING_SENTENCES.length);
      setTopbarLabel({ text: THINKING_SENTENCES[sentenceIndexRef.current], mode: "thinking" });
      thinkingIntervalRef.current = window.setInterval(() => {
        sentenceIndexRef.current = (sentenceIndexRef.current + 1) % THINKING_SENTENCES.length;
        setTopbarLabel({ text: THINKING_SENTENCES[sentenceIndexRef.current], mode: "thinking" });
      }, 2800);
    } else {
      if (thinkingIntervalRef.current !== null) {
        window.clearInterval(thinkingIntervalRef.current);
        thinkingIntervalRef.current = null;
      }
      setTopbarLabel(null);
    }
  }, []);

  const onAnswerStart = useCallback(() => {
    if (thinkingIntervalRef.current !== null) {
      window.clearInterval(thinkingIntervalRef.current);
      thinkingIntervalRef.current = null;
    }
    setTopbarLabel(null);
  }, []);

  const onTypingChange = useCallback((typing: boolean) => {
    if (isThinkingRef.current) return;
    if (typing) {
      // Pick a sentence only when transitioning from nothing
      setTopbarLabel((current) => {
        if (current?.mode === "typing") return current;
        const idx = Math.floor(Math.random() * TYPING_SENTENCES.length);
        return { text: TYPING_SENTENCES[idx], mode: "typing" };
      });
      // Reset the 3s idle clear timer on every keystroke
      if (typingClearRef.current !== null) window.clearTimeout(typingClearRef.current);
      typingClearRef.current = window.setTimeout(() => {
        setTopbarLabel(null);
        typingClearRef.current = null;
      }, 3000);
    } else {
      if (typingClearRef.current !== null) {
        window.clearTimeout(typingClearRef.current);
        typingClearRef.current = null;
      }
      setTopbarLabel(null);
    }
  }, []);

  if (isBootstrapping) {
    return (
      <div className="onboarding-stage">
        <section className="onboarding-card">
          <img className="onboarding-mascot" src={babyMascot} alt="AzulClaw hatchling" />
          <p className="eyebrow">Wake up</p>
          <h1>Preparing AzulClaw's nest</h1>
          <p>
            Loading profile, sandbox and companion state before opening the
            desktop.
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
            {topbarLabel && (
              <div className={`topbar-bubble topbar-bubble-${topbarLabel.mode}`}>
                {topbarLabel.text}
              </div>
            )}
          </div>
          {activeView === "chat" && (
            <div className="topbar-context">
              <p className="topbar-context-eyebrow">Active session</p>
              <h2 className="topbar-context-title">Main conversation</h2>
            </div>
          )}
          <div className="topbar-right">
            <div className="topbar-status-row">
              <span className="topbar-live-dot" />
              <span className="topbar-status-label">Slow Brain</span>
              <span className="topbar-status-divider">·</span>
              <span className="topbar-status-label">auto</span>
            </div>
            <span className="topbar-workspace-chip" title={profile.workspace_root}>
              {profile.workspace_root}
            </span>
          </div>
        </header>
        {renderView(activeView, profile, setProfile, onThinkingChange, onTypingChange, onAnswerStart)}
      </main>
    </div>
  );
}
