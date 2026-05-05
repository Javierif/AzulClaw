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
import { ensureBackendAuth, loadBackendStatus, loadHatching } from "../lib/api";
import { profileCanRenewAzureLogin, renewAzureLoginFromProfile } from "../lib/azure-session";
import { DEFAULT_CONVERSATION_TITLE, normalizeConversationTitle } from "../lib/conversation-copy";
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
  onLocalDataWiped: (p: HatchingProfile) => void,
  onTitleChange: (title: string) => void,
  onRegisterNewChat: (fn: () => void) => void,
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
      return <SettingsShell onLocalDataWiped={onLocalDataWiped} />;
    case "chat":
    default:
      return (
        <ChatShell
          onThinkingChange={onThinkingChange}
          onTypingChange={onTypingChange}
          onAnswerStart={onAnswerStart}
          onTitleChange={onTitleChange}
          onRegisterNewChat={onRegisterNewChat}
        />
      );
  }
}

export function DesktopApp() {
  const [activeView, setActiveView] = useState<AppView>("chat");
  const [profile, setProfile] = useState<HatchingProfile>(defaultHatchingProfile);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [authPromptVisible, setAuthPromptVisible] = useState(false);
  const [authPromptBusy, setAuthPromptBusy] = useState(false);
  const [authPromptError, setAuthPromptError] = useState("");
  const [authPromptDismissWarning, setAuthPromptDismissWarning] = useState(false);
  const [topbarLabel, setTopbarLabel] = useState<{ text: string; mode: "thinking" | "typing" } | null>(null);
  const [conversationTitle, setConversationTitle] = useState(DEFAULT_CONVERSATION_TITLE);
  const newChatFnRef = useRef<() => void>(() => {});
  const thinkingIntervalRef = useRef<number | null>(null);
  const typingClearRef = useRef<number | null>(null);
  const sentenceIndexRef = useRef(0);
  const isThinkingRef = useRef(false);

  useEffect(() => {
    let isMounted = true;

    loadHatching().then(async (data) => {
      if (isMounted) {
        setProfile(data);
        setIsBootstrapping(false);
        if (!data.is_hatched) {
          setActiveView("hatching");
        }
      }
      try {
        const auth = await ensureBackendAuth();
        if (
          isMounted &&
          auth.mode === "entra" &&
          auth.requires_frontend_login &&
          profileCanRenewAzureLogin(data)
        ) {
          setAuthPromptVisible(true);
        }
      } catch {
        try {
          const status = await loadBackendStatus();
          if (
            isMounted &&
            status.auth.mode === "entra" &&
            status.auth.requires_frontend_login &&
            profileCanRenewAzureLogin(data)
          ) {
            setAuthPromptVisible(true);
          }
        } catch {
          /* backend unavailable; regular offline handling remains in views */
        }
      }
    });

    return () => {
      isMounted = false;
    };
  }, []);

  async function handleRenewAzureLogin() {
    setAuthPromptBusy(true);
    setAuthPromptError("");
    setAuthPromptDismissWarning(false);
    try {
      await renewAzureLoginFromProfile(profile);
      setAuthPromptVisible(false);
    } catch (error) {
      setAuthPromptError(error instanceof Error ? error.message : String(error));
    } finally {
      setAuthPromptBusy(false);
    }
  }

  function handleDismissAzureLogin() {
    if (!authPromptDismissWarning) {
      setAuthPromptDismissWarning(true);
      setAuthPromptError("");
      return;
    }
    setAuthPromptVisible(false);
    setAuthPromptDismissWarning(false);
  }

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
            <div className="topbar-chat-area">
              <div className="topbar-session-block">
                <p className="topbar-context-eyebrow">Active session</p>
                <h2
                  className="topbar-context-title"
                  title={normalizeConversationTitle(conversationTitle)}
                >
                  {normalizeConversationTitle(conversationTitle)}
                </h2>
              </div>
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
        {renderView(
          activeView,
          profile,
          setProfile,
          onThinkingChange,
          onTypingChange,
          onAnswerStart,
          (data) => {
            const { restart_required: _rr, ...rest } = data;
            setProfile(rest);
          },
          setConversationTitle,
          (fn) => { newChatFnRef.current = fn; },
        )}
        {authPromptVisible && (
          <div className="hw-modal-backdrop">
            <section
              className="hw-modal-card"
              role="dialog"
              aria-modal="true"
              style={{ maxWidth: "520px" }}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="hw-modal-head">
                <div>
                  <p className="hw-label">MICROSOFT LOGIN</p>
                  <h3 className="hw-modal-title">Azure needs a fresh sign-in</h3>
                </div>
              </div>
              <p className="hw-inline-note">
                AzulClaw kept your Azure resource, model and Key Vault settings. It only needs a new Microsoft token for this session.
              </p>
              {authPromptDismissWarning && (
                <p className="hw-inline-note hw-inline-note-warning" style={{ marginTop: "10px" }}>
                  Without Microsoft sign-in, AzulClaw cannot call Azure OpenAI or use your Azure-backed resources in this session.
                </p>
              )}
              {authPromptError && (
                <p className="hw-inline-note hw-inline-note-warning" style={{ marginTop: "10px" }}>{authPromptError}</p>
              )}
              <div className="hw-modal-actions" style={{ marginTop: "16px" }}>
                <button type="button" className="hw-btn-ghost" onClick={handleDismissAzureLogin} disabled={authPromptBusy}>
                  {authPromptDismissWarning ? "Continue without sign-in" : "Not now"}
                </button>
                <button type="button" className="hw-btn-primary" onClick={() => void handleRenewAzureLogin()} disabled={authPromptBusy}>
                  {authPromptBusy ? "Signing in..." : "Sign in with Microsoft"}
                </button>
              </div>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
