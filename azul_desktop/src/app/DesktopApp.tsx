import { useCallback, useEffect, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import adultMascot from "../../../img/azulclaw.png";
import babyMascot from "../../../img/hatching_azulclaw.png";

import { Sidebar } from "../components/Sidebar";
import { ChatShell } from "../features/chat/ChatShell";
import { ContextShell } from "../features/context/ContextShell";
import { HeartbeatsShell } from "../features/heartbeats/HeartbeatsShell";
import { SetupWizardShell } from "../features/hatching/HatchingShell";
import { RegistryAdminShell } from "../features/registry/RegistryAdminShell";
import { SettingsShell, type SettingsTab } from "../features/settings/SettingsShell";
import { SkillsShell } from "../features/skills/SkillsShell";
import {
  ensureBackendAuth,
  listConversations,
  loadBackendStatus,
  loadSetupProfile,
  loadSkillMarketplaceSettings,
} from "../lib/api";
import { profileCanRenewAzureLogin, renewAzureLoginFromProfile } from "../lib/azure-session";
import { DEFAULT_CONVERSATION_TITLE, normalizeConversationTitle } from "../lib/conversation-copy";
import { notifyUnreadConversation } from "../lib/desktop-notifications";
import type { AppView, SetupProfile } from "../lib/contracts";
import { defaultSetupProfile } from "../lib/mock-data";

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

const THINKING_LABEL_MIN_DELAY_MS = 3_000;
const THINKING_LABEL_MAX_DELAY_MS = 15_000;

function renderView(
  view: AppView,
  profile: SetupProfile,
  setProfile: (p: SetupProfile) => void,
  onThinkingChange: (thinking: boolean) => void,
  onTypingChange: (typing: boolean) => void,
  onAnswerStart: () => void,
  onLocalDataWiped: (p: SetupProfile) => void,
  onTitleChange: (title: string) => void,
  onRegisterNewChat: (fn: () => void) => void,
  unreadByConversationId: Record<string, boolean>,
  externalConversationRequest: { conversationId: string; title: string; nonce: number } | null,
  headerPortalTarget: HTMLElement | null,
  focusRequestNonce: number,
  onOpenMarketplaceSettings: () => void,
  settingsInitialTab: SettingsTab,
  onMarketplaceSettingsChanged: (settings: { registry_admin_key_configured?: boolean }) => void,
) {
  switch (view) {
    case "skills":
      return <SkillsShell headerPortalTarget={headerPortalTarget} onOpenMarketplaceSettings={onOpenMarketplaceSettings} />;
    case "registry":
      return <RegistryAdminShell headerPortalTarget={headerPortalTarget} onOpenMarketplaceSettings={onOpenMarketplaceSettings} />;
    case "context":
      return <ContextShell headerPortalTarget={headerPortalTarget} />;
    case "heartbeats":
      return <HeartbeatsShell headerPortalTarget={headerPortalTarget} />;
    case "settings":
      return (
        <SettingsShell
          headerPortalTarget={headerPortalTarget}
          initialTab={settingsInitialTab}
          onLocalDataWiped={onLocalDataWiped}
          onMarketplaceSettingsChanged={onMarketplaceSettingsChanged}
        />
      );
    case "chat":
    default:
      return (
        <ChatShell
          onThinkingChange={onThinkingChange}
          onTypingChange={onTypingChange}
          onAnswerStart={onAnswerStart}
          onTitleChange={onTitleChange}
          onRegisterNewChat={onRegisterNewChat}
          unreadByConversationId={unreadByConversationId}
          externalConversationRequest={externalConversationRequest}
          focusRequestNonce={focusRequestNonce}
        />
      );
  }
}

export function DesktopApp() {
  const [activeView, setActiveView] = useState<AppView>("chat");
  const [settingsInitialTab, setSettingsInitialTab] = useState<SettingsTab>("azure");
  const [profile, setProfile] = useState<SetupProfile>(defaultSetupProfile);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [authPromptVisible, setAuthPromptVisible] = useState(false);
  const [authPromptBusy, setAuthPromptBusy] = useState(false);
  const [authPromptError, setAuthPromptError] = useState("");
  const [authPromptDismissWarning, setAuthPromptDismissWarning] = useState(false);
  const [registryAdminVisible, setRegistryAdminVisible] = useState(false);
  const [topbarLabel, setTopbarLabel] = useState<{ text: string; mode: "thinking" | "typing" } | null>(null);
  const [conversationTitle, setConversationTitle] = useState(DEFAULT_CONVERSATION_TITLE);
  const [sectionHeaderHost, setSectionHeaderHost] = useState<HTMLDivElement | null>(null);
  const [unreadByConversationId, setUnreadByConversationId] = useState<Record<string, boolean>>({});
  const [externalConversationRequest, setExternalConversationRequest] = useState<{
    conversationId: string;
    title: string;
    nonce: number;
  } | null>(null);
  const [chatFocusNonce, setChatFocusNonce] = useState(0);
  const newChatFnRef = useRef<() => void>(() => {});
  const thinkingIntervalRef = useRef<number | null>(null);
  const typingClearRef = useRef<number | null>(null);
  const sentenceIndexRef = useRef(0);
  const isThinkingRef = useRef(false);
  const notificationNonceRef = useRef(0);

  const clearThinkingLabelTimer = useCallback(() => {
    if (thinkingIntervalRef.current !== null) {
      window.clearTimeout(thinkingIntervalRef.current);
      thinkingIntervalRef.current = null;
    }
  }, []);

  const scheduleNextThinkingLabel = useCallback(() => {
    clearThinkingLabelTimer();
    const delay =
      THINKING_LABEL_MIN_DELAY_MS +
      Math.floor(Math.random() * (THINKING_LABEL_MAX_DELAY_MS - THINKING_LABEL_MIN_DELAY_MS + 1));
    thinkingIntervalRef.current = window.setTimeout(() => {
      if (!isThinkingRef.current) {
        thinkingIntervalRef.current = null;
        return;
      }
      sentenceIndexRef.current = (sentenceIndexRef.current + 1) % THINKING_SENTENCES.length;
      setTopbarLabel({ text: THINKING_SENTENCES[sentenceIndexRef.current], mode: "thinking" });
      scheduleNextThinkingLabel();
    }, delay);
  }, [clearThinkingLabelTimer]);

  useEffect(() => {
    let isMounted = true;

    loadSetupProfile().then(async (data) => {
      if (isMounted) {
        setProfile(data);
        setIsBootstrapping(false);
      }
      try {
        const marketplaceSettings = await loadSkillMarketplaceSettings();
        if (isMounted) {
          setRegistryAdminVisible(Boolean(marketplaceSettings.registry_admin_key_configured));
        }
      } catch {
        if (isMounted) {
          setRegistryAdminVisible(false);
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

  function handleNavigate(view: AppView) {
    if (view === "settings") {
      setSettingsInitialTab("azure");
    }
    setActiveView(view);
  }

  function openMarketplaceSettings() {
    setSettingsInitialTab("marketplace");
    setActiveView("settings");
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
      clearThinkingLabelTimer();
      sentenceIndexRef.current = Math.floor(Math.random() * THINKING_SENTENCES.length);
      setTopbarLabel({ text: THINKING_SENTENCES[sentenceIndexRef.current], mode: "thinking" });
      scheduleNextThinkingLabel();
    } else {
      clearThinkingLabelTimer();
      setTopbarLabel(null);
    }
  }, [clearThinkingLabelTimer, scheduleNextThinkingLabel]);

  const onAnswerStart = useCallback(() => {
    clearThinkingLabelTimer();
    setTopbarLabel(null);
  }, [clearThinkingLabelTimer]);

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

  useEffect(() => {
    let cancelled = false;
    let pollId: number | null = null;

    async function refreshConversationActivity() {
      try {
        const summaries = await listConversations("desktop-user");
        if (cancelled) {
          return;
        }
        setUnreadByConversationId(
          summaries.reduce<Record<string, boolean>>((acc, item) => {
            acc[item.id] = Boolean(item.has_unread);
            return acc;
          }, {}),
        );
        for (const item of summaries) {
          await notifyUnreadConversation(item, async () => {
            if (cancelled) {
              return;
            }
            notificationNonceRef.current += 1;
            setActiveView("chat");
            setExternalConversationRequest({
              conversationId: item.id,
              title: item.title,
              nonce: notificationNonceRef.current,
            });
          });
        }
      } catch {
        if (!cancelled) {
          setUnreadByConversationId({});
        }
      } finally {
        if (!cancelled) {
          pollId = window.setTimeout(() => void refreshConversationActivity(), 5_000);
        }
      }
    }

    void refreshConversationActivity();

    return () => {
      cancelled = true;
      clearThinkingLabelTimer();
      if (pollId !== null) {
        window.clearTimeout(pollId);
      }
    };
  }, [clearThinkingLabelTimer]);

  useEffect(() => {
    if (!isTauri()) {
      return;
    }

    let unlisten: (() => void) | undefined;
    void listen("azul://shell/activate-chat", () => {
      setActiveView("chat");
      setChatFocusNonce((current) => current + 1);
    }).then((dispose) => {
      unlisten = dispose;
    });

    return () => {
      unlisten?.();
    };
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
      <SetupWizardShell
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
      <Sidebar
        activeView={activeView}
        onNavigate={handleNavigate}
        profile={profile}
        registryAdminVisible={registryAdminVisible}
      />
      <main className="desktop-main">
        <header className={`desktop-topbar${activeView === "chat" ? "" : " desktop-topbar-section-mode"}`}>
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
          {activeView === "chat" ? (
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
          ) : (
            <div className="topbar-section-host" ref={setSectionHeaderHost} />
          )}
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
          unreadByConversationId,
          externalConversationRequest,
          sectionHeaderHost,
          chatFocusNonce,
          openMarketplaceSettings,
          settingsInitialTab,
          (settings) => setRegistryAdminVisible(Boolean(settings.registry_admin_key_configured)),
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
