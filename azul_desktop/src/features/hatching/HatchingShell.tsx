import { isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { useEffect, useMemo, useState } from "react";

import adultMascot from "../../../../img/azulclaw.png";
import babyMascot from "../../../../img/hatching_azulclaw.png";

import { saveHatching } from "../../lib/api";
import type { HatchingProfile } from "../../lib/contracts";
import { defaultHatchingProfile } from "../../lib/mock-data";

interface HatchingShellProps {
  profile?: HatchingProfile;
  onboardingRequired?: boolean;
  onProfileSaved?: (profile: HatchingProfile) => void;
}

type StepType = "text" | "textarea" | "skills" | "path";
type NavDir = "forward" | "back";
type SkillFieldType = "text" | "password" | "url";

interface WizardQuestion {
  id: string;
  title: string;
  helper: string;
  placeholder: string;
  type: StepType;
  emoji: string;
}

interface SkillField {
  id: string;
  label: string;
  helper: string;
  placeholder: string;
  type: SkillFieldType;
}

interface SkillDefinition {
  id: string;
  title: string;
  description: string;
  fields: SkillField[];
}

interface WizardState {
  answers: string[];
  configuredSkills: string[];
  skillConfigs: Record<string, Record<string, string>>;
  workspaceRoot: string;
  confirmSensitiveActions: boolean;
}

const wizardQuestions: WizardQuestion[] = [
  { id: "NOMBRE", title: "Como quieres que se llame tu AzulClaw?", helper: "Ese sera su nombre dentro de la app. Puedes cambiarlo cuando quieras.", placeholder: "AzulClaw, Atlas, Clawy...", type: "text", emoji: "Paw" },
  { id: "ROL", title: "Que quieres que sea para ti?", helper: "Puedes responder con total libertad. No existe respuesta incorrecta.", placeholder: "Quiero que seas mi asistente tecnico para organizar tareas, codigo y decisiones.", type: "textarea", emoji: "Role" },
  { id: "MISION", title: "Cual debe ser su mision principal?", helper: "Esto define en que debe poner el foco cuando trabaje contigo.", placeholder: "Ayudarme a avanzar con foco, contexto y orden.", type: "textarea", emoji: "Mission" },
  { id: "CARACTER", title: "Como quieres que te hable y actue?", helper: "Describe su tono, su estilo y cuanta autonomia quieres darle.", placeholder: "Directo, claro, tecnico y con iniciativa, pero que confirme las acciones delicadas.", type: "textarea", emoji: "Tone" },
  { id: "CAPACIDADES", title: "Que skills quieres activar primero?", helper: "Haz click en una skill para configurarla. Solo se activara cuando la dejes completamente lista.", placeholder: "", type: "skills", emoji: "Skills" },
  { id: "WORKSPACE", title: "Que carpeta sera su workspace?", helper: "El workspace debe ser una carpeta. Sera el escritorio de AzulClaw: su zona segura para leer, escribir y organizar archivos.", placeholder: "C:\\Users\\usuario\\Desktop\\AzulWorkspace", type: "path", emoji: "Desk" },
];

const SKILL_CATALOG: SkillDefinition[] = [
  {
    id: "Correo",
    title: "Correo",
    description: "Leer y preparar correos desde una cuenta conectada.",
    fields: [
      { id: "provider", label: "Proveedor", helper: "Ejemplo: Gmail u Outlook.", placeholder: "Gmail", type: "text" },
      { id: "email", label: "Correo", helper: "Direccion principal que usara AzulClaw.", placeholder: "tu@dominio.com", type: "text" },
      { id: "token", label: "Token o app password", helper: "Credencial necesaria para autenticar el acceso.", placeholder: "Pega aqui la credencial", type: "password" },
    ],
  },
  {
    id: "Telegram",
    title: "Telegram",
    description: "Enviar avisos y operar a traves de un bot.",
    fields: [
      { id: "botToken", label: "Bot token", helper: "Token generado por BotFather.", placeholder: "123456:ABC...", type: "password" },
      { id: "chatId", label: "Chat ID", helper: "Chat o usuario autorizado para hablar con AzulClaw.", placeholder: "987654321", type: "text" },
    ],
  },
  {
    id: "Slack",
    title: "Slack",
    description: "Publicar mensajes y atender eventos en canales de trabajo.",
    fields: [
      { id: "workspaceUrl", label: "Workspace URL", helper: "URL base del workspace.", placeholder: "https://mi-equipo.slack.com", type: "url" },
      { id: "botToken", label: "Bot token", helper: "Token del bot con permisos.", placeholder: "xoxb-...", type: "password" },
      { id: "defaultChannel", label: "Canal por defecto", helper: "Canal principal donde podra escribir.", placeholder: "#azulclaw", type: "text" },
    ],
  },
  {
    id: "Alexa",
    title: "Alexa",
    description: "Conectar acciones por voz y rutinas del hogar.",
    fields: [
      { id: "skillId", label: "Skill ID", helper: "Identificador de la skill o integracion.", placeholder: "amzn1.ask.skill...", type: "text" },
      { id: "clientSecret", label: "Client secret", helper: "Credencial usada para validar peticiones.", placeholder: "Pega aqui el secret", type: "password" },
    ],
  },
  {
    id: "Memoria",
    title: "Memoria",
    description: "Guardar contexto relevante y recuperarlo cuando haga falta.",
    fields: [
      { id: "retentionPolicy", label: "Politica de retencion", helper: "Cuanto y como recordar.", placeholder: "Preferencias y contexto tecnico", type: "text" },
      { id: "memoryScope", label: "Ambito inicial", helper: "Que tipo de informacion puede memorizar.", placeholder: "Proyectos, decisiones y preferencias", type: "text" },
    ],
  },
];

const SKILL_IDS = new Set(SKILL_CATALOG.map((skill) => skill.id));

function buildTextAnswers(profile: HatchingProfile) {
  return [profile.name, profile.role, profile.mission, [profile.tone, profile.style, profile.autonomy].filter(Boolean).join(", ")];
}

function buildWizardState(profile: HatchingProfile, onboardingRequired: boolean): WizardState {
  return {
    answers: buildTextAnswers(profile),
    configuredSkills: onboardingRequired ? [] : profile.skills.filter((skill) => SKILL_IDS.has(skill)),
    skillConfigs: onboardingRequired ? {} : profile.skill_configs,
    workspaceRoot: profile.workspace_root,
    confirmSensitiveActions: profile.confirm_sensitive_actions,
  };
}

function deriveArchetype(value: string, fallback: string) {
  const v = value.toLowerCase();
  if (v.includes("guardian") || v.includes("segur")) return "Guardian";
  if (v.includes("explor") || v.includes("investiga")) return "Explorer";
  if (v.includes("tecnic") || v.includes("codigo") || v.includes("program")) return "Engineer";
  return fallback || "Companion";
}

function deriveTone(value: string, fallback: string) {
  const v = value.toLowerCase();
  if (v.includes("serio") || v.includes("formal")) return "Serio";
  if (v.includes("calido") || v.includes("cercano")) return "Calido";
  if (v.includes("directo") || v.includes("claro")) return "Directo";
  return fallback || "Directo";
}

function deriveStyle(value: string, fallback: string) {
  const v = value.toLowerCase();
  if (v.includes("breve") || v.includes("conciso")) return "Breve";
  if (v.includes("tecnico") || v.includes("profundo")) return "Tecnico";
  if (v.includes("explica") || v.includes("detalle")) return "Explicativo";
  return fallback || "Explicativo";
}

function deriveAutonomy(value: string, fallback: string) {
  const v = value.toLowerCase();
  if (v.includes("confirma") || v.includes("pregunta") || v.includes("prudente")) return "Confirmador";
  if (v.includes("autonomo") || v.includes("iniciativa")) return "Autonomo alto";
  return fallback || "Autonomo moderado";
}

function buildProfileFromWizard(base: HatchingProfile, state: WizardState): HatchingProfile {
  const [name, role, mission, temper] = state.answers;
  return {
    ...base,
    name: name.trim() || base.name,
    role: role.trim() || base.role,
    mission: mission.trim() || base.mission,
    tone: deriveTone(temper, base.tone),
    style: deriveStyle(temper, base.style),
    autonomy: deriveAutonomy(temper, base.autonomy),
    archetype: deriveArchetype(`${role} ${mission}`, base.archetype),
    workspace_root: state.workspaceRoot.trim() || base.workspace_root,
    confirm_sensitive_actions: state.confirmSensitiveActions,
    skills: state.configuredSkills,
    skill_configs: state.skillConfigs,
  };
}

function getStepEmoji(label: string) {
  return label;
}

export function HatchingShell({
  profile: incomingProfile,
  onboardingRequired = false,
  onProfileSaved,
}: HatchingShellProps) {
  const initial = incomingProfile ?? defaultHatchingProfile;
  const initialState = buildWizardState(initial, onboardingRequired);

  const [profile, setProfile] = useState<HatchingProfile>(initial);
  const [answers, setAnswers] = useState<string[]>(initialState.answers);
  const [configuredSkills, setConfiguredSkills] = useState<string[]>(initialState.configuredSkills);
  const [skillConfigs, setSkillConfigs] = useState<Record<string, Record<string, string>>>(initialState.skillConfigs);
  const [workspaceRoot, setWorkspaceRoot] = useState(initialState.workspaceRoot);
  const [confirmSensitiveActions, setConfirmSensitiveActions] = useState(initialState.confirmSensitiveActions);
  const [currentStep, setCurrentStep] = useState(0);
  const [isSaving, setIsSaving] = useState(false);
  const [isExiting, setIsExiting] = useState(false);
  const [navDir, setNavDir] = useState<NavDir>("forward");
  const [isPickingWorkspace, setIsPickingWorkspace] = useState(false);
  const [workspacePickerError, setWorkspacePickerError] = useState("");
  const [activeSkillId, setActiveSkillId] = useState<string | null>(null);
  const [skillDraft, setSkillDraft] = useState<Record<string, string>>({});
  const [skillModalError, setSkillModalError] = useState("");

  useEffect(() => {
    if (!incomingProfile) return;
    const nextState = buildWizardState(incomingProfile, onboardingRequired);
    setProfile(incomingProfile);
    setAnswers(nextState.answers);
    setConfiguredSkills(nextState.configuredSkills);
    setSkillConfigs(nextState.skillConfigs);
    setWorkspaceRoot(nextState.workspaceRoot);
    setConfirmSensitiveActions(nextState.confirmSensitiveActions);
  }, [incomingProfile, onboardingRequired]);

  const draftProfile = useMemo(
    () => buildProfileFromWizard(profile, { answers, configuredSkills, skillConfigs, workspaceRoot, confirmSensitiveActions }),
    [answers, configuredSkills, confirmSensitiveActions, profile, skillConfigs, workspaceRoot],
  );

  const isFinalStep = currentStep === wizardQuestions.length;
  const activeQuestion = wizardQuestions[currentStep];
  const stepNumber = Math.min(currentStep + 1, wizardQuestions.length);
  const mascotImage = onboardingRequired && !draftProfile.is_hatched ? babyMascot : adultMascot;
  const activeSkill = activeSkillId ? SKILL_CATALOG.find((skill) => skill.id === activeSkillId) ?? null : null;

  function navigate(toStep: number, dir: NavDir) {
    if (isExiting || activeSkill) return;
    setNavDir(dir);
    setIsExiting(true);
    setTimeout(() => {
      setCurrentStep(toStep);
      setIsExiting(false);
    }, 260);
  }

  function handleNext() {
    navigate(Math.min(currentStep + 1, wizardQuestions.length), "forward");
  }

  function handleBack() {
    navigate(Math.max(currentStep - 1, 0), "back");
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Enter" || event.shiftKey || isExiting || isFinalStep || activeSkill) return;
      const target = event.target as HTMLElement | null;
      if (!target || target.tagName === "TEXTAREA" || target.tagName === "BUTTON") return;
      event.preventDefault();
      handleNext();
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeSkill, isExiting, isFinalStep, currentStep]);

  function handleAnswerChange(value: string) {
    setAnswers((current) => current.map((item, index) => (index === currentStep ? value : item)));
  }

  function openSkillConfig(skillId: string) {
    setActiveSkillId(skillId);
    setSkillDraft(skillConfigs[skillId] ?? {});
    setSkillModalError("");
  }

  function closeSkillConfig() {
    setActiveSkillId(null);
    setSkillDraft({});
    setSkillModalError("");
  }

  function handleSkillFieldChange(fieldId: string, value: string) {
    setSkillDraft((current) => ({ ...current, [fieldId]: value }));
  }

  function saveSkillConfig() {
    if (!activeSkill) return;
    const missingField = activeSkill.fields.find((field) => !skillDraft[field.id]?.trim());
    if (missingField) {
      setSkillModalError(`Falta completar "${missingField.label}".`);
      return;
    }

    setSkillConfigs((current) => ({ ...current, [activeSkill.id]: skillDraft }));
    setConfiguredSkills((current) => (current.includes(activeSkill.id) ? current : [...current, activeSkill.id]));
    closeSkillConfig();
  }

  function deactivateSkill(skillId: string) {
    setConfiguredSkills((current) => current.filter((item) => item !== skillId));
    setSkillConfigs((current) => {
      const next = { ...current };
      delete next[skillId];
      return next;
    });
    closeSkillConfig();
  }

  async function handlePickWorkspace() {
    setWorkspacePickerError("");
    if (!isTauri()) {
      setWorkspacePickerError("El selector nativo solo esta disponible dentro de la app de escritorio de Tauri.");
      return;
    }

    setIsPickingWorkspace(true);
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        defaultPath: workspaceRoot || undefined,
        title: "Selecciona la carpeta workspace de AzulClaw",
      });
      if (typeof selected === "string" && selected.trim()) setWorkspaceRoot(selected);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      console.error("Workspace picker failed", error);
      setWorkspacePickerError(`No se pudo abrir el selector nativo. ${detail}`);
    } finally {
      setIsPickingWorkspace(false);
    }
  }

  async function handleSave(markAsHatched: boolean) {
    setIsSaving(true);
    const saved = await saveHatching({ ...draftProfile, is_hatched: markAsHatched || profile.is_hatched });
    const nextState = buildWizardState(saved, false);
    setProfile(saved);
    setAnswers(nextState.answers);
    setConfiguredSkills(nextState.configuredSkills);
    setSkillConfigs(nextState.skillConfigs);
    setWorkspaceRoot(nextState.workspaceRoot);
    setConfirmSensitiveActions(nextState.confirmSensitiveActions);
    onProfileSaved?.(saved);
    setIsSaving(false);
  }

  const contentAnim = isExiting
    ? navDir === "forward" ? "hw-exit-fwd" : "hw-exit-back"
    : navDir === "forward" ? "hw-enter-fwd" : "hw-enter-back";

  const shellClass = onboardingRequired ? "hw-fullscreen" : "hw-contained card";
  const nextButtonLabel = activeQuestion?.type === "skills" && configuredSkills.length === 0 ? "Skip for now ->" : "Siguiente ->";
  const nextHint = activeQuestion?.type === "skills" && configuredSkills.length === 0 ? "Pulsa Enter para saltarlo por ahora" : "Pulsa Enter para continuar";

  if (!onboardingRequired) {
    return (
      <section className="single-panel-layout">
        <div className="card panel-stack" style={{ padding: "28px" }}>
          <div className="panel-heading" style={{ borderBottom: "1px solid var(--line)", paddingBottom: "20px", marginBottom: "10px" }}>
            <div>
              <p className="eyebrow">Identity Menu</p>
              <h2 style={{ fontSize: "1.8rem", margin: "4px 0" }}>Perfil de Hatching</h2>
              <p className="hint-text" style={{ margin: 0 }}>Ajusta la personalidad central y el entorno de tu agente en cualquier momento.</p>
            </div>
            <button type="button" className="primary-button" onClick={() => void handleSave(true)} disabled={isSaving}>
              {isSaving ? "Guardando..." : "Aplicar cambios"}
            </button>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
            <label className="hw-modal-field">
              <span className="hw-field-label">Nombre del Agente</span>
              <input 
                className="hw-modal-input" 
                type="text" 
                value={answers[0] ?? ""} 
                onChange={(e) => setAnswers(c => { const n = [...c]; n[0] = e.target.value; return n; })} 
              />
            </label>

            <div className="hw-choice-row">
              <label className="hw-modal-field">
                <span className="hw-field-label">Rol principal</span>
                <textarea 
                  className="hw-textarea" 
                  style={{ minHeight: "100px" }} 
                  value={answers[1] ?? ""} 
                  onChange={(e) => setAnswers(c => { const n = [...c]; n[1] = e.target.value; return n; })} 
                />
              </label>
              <label className="hw-modal-field">
                <span className="hw-field-label">Misión u Objetivo</span>
                <textarea 
                  className="hw-textarea" 
                  style={{ minHeight: "100px" }} 
                  value={answers[2] ?? ""} 
                  onChange={(e) => setAnswers(c => { const n = [...c]; n[2] = e.target.value; return n; })} 
                />
              </label>
            </div>

            <label className="hw-modal-field">
              <span className="hw-field-label">Comportamiento y Tono</span>
              <input 
                className="hw-modal-input" 
                type="text" 
                value={answers[3] ?? ""} 
                onChange={(e) => setAnswers(c => { const n = [...c]; n[3] = e.target.value; return n; })} 
              />
            </label>

            <div className="hw-workspace-panel" style={{ marginTop: "10px" }}>
              <p className="hw-field-label" style={{ marginBottom: "6px" }}>Workspace Seguro</p>
              <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
                <input 
                  className="hw-input-line hw-input-mono" 
                  type="text" 
                  value={workspaceRoot} 
                  onChange={(e) => setWorkspaceRoot(e.target.value)} 
                  style={{ flex: 1 }}
                />
                <button type="button" className="hw-btn-ghost" onClick={() => void handlePickWorkspace()}>
                  📂 Elegir
                </button>
              </div>
              {workspacePickerError && <p className="hw-inline-note hw-inline-note-warning">{workspacePickerError}</p>}
              
              <div className="hw-workspace-confirm" style={{ marginTop: "16px" }}>
                <p className="hw-field-label">Confirmación de acciones sensibles</p>
                <div className="hw-choice-row">
                  <button type="button" className={`hw-choice${confirmSensitiveActions ? " hw-choice-active" : ""}`} onClick={() => setConfirmSensitiveActions(true)}>
                    Preguntar antes de actuar
                  </button>
                  <button type="button" className={`hw-choice${!confirmSensitiveActions ? " hw-choice-active" : ""}`} onClick={() => setConfirmSensitiveActions(false)}>
                    Total autonomía
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    );
  }

  return (
    <div className={shellClass}>
      <header className="hw-header">
        <div className="hw-brand">
          <img className="hw-mascot" src={mascotImage} alt="AzulClaw" />
          <div>
            <p className="hw-eyebrow">Hatching</p>
            <h2 className="hw-brand-title">{onboardingRequired ? "Crea tu AzulClaw" : "Reconfigura tu AzulClaw"}</h2>
          </div>
        </div>
        <div className="hw-dots" aria-label={`Paso ${stepNumber} de ${wizardQuestions.length}`}>
          {wizardQuestions.map((_, index) => (
            <span key={index} className={`hw-dot${index < currentStep ? " hw-dot-done" : index === currentStep ? " hw-dot-active" : ""}`} />
          ))}
        </div>
      </header>

      <div className="hw-progress" role="progressbar">
        <span style={{ width: `${(currentStep / wizardQuestions.length) * 100}%` }} />
      </div>

      <main className="hw-main">
        {!isFinalStep ? (
          <div key={currentStep} className={`hw-content ${contentAnim}`}>
            <div className="hw-question">
              <span className="hw-emoji">{getStepEmoji(activeQuestion.emoji)}</span>
              <p className="hw-label">{activeQuestion.id}</p>
              <h1 className="hw-title">{activeQuestion.title}</h1>
              <p className="hw-helper">{activeQuestion.helper}</p>
            </div>

            <div className="hw-answer-wrap">
              {activeQuestion.type === "text" && (
                <input id={`hw-answer-${currentStep}`} className="hw-input-line" type="text" value={answers[currentStep] ?? ""} placeholder={activeQuestion.placeholder} onChange={(event) => handleAnswerChange(event.target.value)} autoFocus />
              )}

              {activeQuestion.type === "textarea" && (
                <textarea id={`hw-answer-${currentStep}`} className="hw-textarea" value={answers[currentStep] ?? ""} placeholder={activeQuestion.placeholder} onChange={(event) => handleAnswerChange(event.target.value)} autoFocus />
              )}

              {activeQuestion.type === "skills" && (
                <div className="hw-skills-wrap">
                  <div className="hw-skills-copy">
                    <p className="hw-inline-note">Aqui no eliges texto libre. Solo puedes activar skills del catalogo disponible.</p>
                    <p className="hw-inline-note">Al hacer click en una skill se abre su configuracion. Hasta que no completes ese popup, la skill no queda activa.</p>
                  </div>

                  <div className="hw-skills-grid">
                    {SKILL_CATALOG.map((skill) => {
                      const isConfigured = configuredSkills.includes(skill.id);
                      return (
                        <button key={skill.id} type="button" className={`hw-skill-card${isConfigured ? " hw-skill-card-active" : ""}`} onClick={() => openSkillConfig(skill.id)}>
                          <div className="hw-skill-card-top">
                            <span className="hw-skill-card-title">{skill.title}</span>
                            <span className={`hw-skill-state${isConfigured ? " hw-skill-state-ready" : ""}`}>{isConfigured ? "Configurada" : "Configurar"}</span>
                          </div>
                          <p className="hw-skill-card-body">{skill.description}</p>
                        </button>
                      );
                    })}
                  </div>

                  <div className="hw-skill-summary">
                    <span className="hw-field-label">Skills activas ahora mismo</span>
                    <div className="hw-skill-tags">
                      {configuredSkills.length > 0 ? configuredSkills.map((skill) => <span key={skill} className="hw-skill-tag">{skill}</span>) : <span className="hw-inline-note">Ninguna por ahora.</span>}
                    </div>
                  </div>

                  <p className="hw-inline-note">Si no quieres activar ninguna todavia, continua con Skip for now.</p>
                </div>
              )}

              {activeQuestion.type === "path" && (
                <div className="hw-workspace-wrap">
                  <div className="hw-workspace-panel">
                    <p className="hw-inline-note">Piensalo como el escritorio de AzulClaw. Todo lo que lea, cree u organice vivira dentro de esa carpeta.</p>

                    <div className="hw-workspace-actions">
                      <button type="button" className="hw-btn-ghost" onClick={() => void handlePickWorkspace()} disabled={isPickingWorkspace}>
                        {isPickingWorkspace ? "Abriendo explorador..." : "Elegir carpeta..."}
                      </button>
                      <span className="hw-inline-note">Si prefieres, tambien puedes pegar o ajustar la ruta manualmente.</span>
                    </div>

                    <label className="hw-field-label" htmlFor="hw-workspace-root">Carpeta workspace</label>
                    <input id="hw-workspace-root" className="hw-input-line hw-input-mono" type="text" value={workspaceRoot} placeholder={activeQuestion.placeholder} onChange={(event) => setWorkspaceRoot(event.target.value)} autoFocus />

                    {workspacePickerError && <p className="hw-inline-note hw-inline-note-warning">{workspacePickerError}</p>}

                    <div className="hw-workspace-confirm">
                      <p className="hw-field-label">Acciones sensibles</p>
                      <div className="hw-choice-row">
                        <button type="button" className={`hw-choice${confirmSensitiveActions ? " hw-choice-active" : ""}`} onClick={() => setConfirmSensitiveActions(true)}>
                          Preguntar antes de cambiar cosas importantes
                        </button>
                        <button type="button" className={`hw-choice${!confirmSensitiveActions ? " hw-choice-active" : ""}`} onClick={() => setConfirmSensitiveActions(false)}>
                          Dejarle actuar sin confirmacion previa
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {activeQuestion.type !== "textarea" && (
              <p className="hw-hint">
                <span>{nextHint}</span>
                <kbd className="hw-kbd">Enter</kbd>
              </p>
            )}
          </div>
        ) : (
          <div key="final" className={`hw-content hw-celebrate ${contentAnim}`}>
            <img src={mascotImage} alt="AzulClaw" className="hw-celebrate-img" />
            <div className="hw-question" style={{ textAlign: "center" }}>
              <p className="hw-label">LISTO</p>
              <h1 className="hw-title">{draftProfile.name} esta listo para empezar</h1>
              <p className="hw-helper">Este es el punto de partida. Todo se puede ajustar despues desde Settings.</p>
            </div>

            <div className="hw-summary-grid">
              <div className="hw-summary-item"><span className="hw-summary-label">Nombre</span><span className="hw-summary-value">{draftProfile.name}</span></div>
              <div className="hw-summary-item"><span className="hw-summary-label">Archetype</span><span className="hw-summary-value">{draftProfile.archetype}</span></div>
              <div className="hw-summary-item"><span className="hw-summary-label">Estilo</span><span className="hw-summary-value">{draftProfile.tone} · {draftProfile.style}</span></div>
              <div className="hw-summary-item"><span className="hw-summary-label">Workspace</span><span className="hw-summary-value hw-mono">{draftProfile.workspace_root}</span></div>
              <div className="hw-summary-item" style={{ gridColumn: "1 / -1" }}><span className="hw-summary-label">Capacidades</span><span className="hw-summary-value">{draftProfile.skills.length > 0 ? draftProfile.skills.join(", ") : "Ninguna por ahora"}</span></div>
            </div>
          </div>
        )}
      </main>

      <footer className="hw-footer">
        <button type="button" className="hw-btn-ghost" onClick={handleBack} disabled={currentStep === 0 || Boolean(activeSkill)}>Atras</button>
        <span className="hw-step-label">{isFinalStep ? "Resumen" : `${stepNumber} / ${wizardQuestions.length}`}</span>

        {isFinalStep ? (
          <div style={{ display: "flex", gap: "10px" }}>
            {!onboardingRequired && <button type="button" className="hw-btn-ghost" onClick={() => void handleSave(false)}>{isSaving ? "Guardando..." : "Guardar borrador"}</button>}
            <button type="button" className="hw-btn-primary" onClick={() => void handleSave(true)}>
              {isSaving ? "Guardando..." : onboardingRequired ? "Entrar al escritorio ->" : "Aplicar cambios ->"}
            </button>
          </div>
        ) : (
          <button type="button" className="hw-btn-primary" onClick={handleNext} disabled={Boolean(activeSkill)}>{nextButtonLabel}</button>
        )}
      </footer>

      {activeSkill && (
        <div className="hw-modal-backdrop" onClick={closeSkillConfig}>
          <section className="hw-modal-card" role="dialog" aria-modal="true" aria-labelledby="hw-skill-modal-title" onClick={(event) => event.stopPropagation()}>
            <div className="hw-modal-head">
              <div>
                <p className="hw-label">CONFIGURAR SKILL</p>
                <h3 id="hw-skill-modal-title" className="hw-modal-title">{activeSkill.title}</h3>
              </div>
              <button type="button" className="hw-btn-ghost" onClick={closeSkillConfig}>Cerrar</button>
            </div>

            <p className="hw-inline-note">{activeSkill.description}</p>

            <div className="hw-modal-fields">
              {activeSkill.fields.map((field, index) => (
                <label key={field.id} className="hw-modal-field">
                  <span className="hw-field-label">{field.label}</span>
                  <input className="hw-modal-input" type={field.type} value={skillDraft[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => handleSkillFieldChange(field.id, event.target.value)} autoFocus={index === 0} />
                  <span className="hw-inline-note">{field.helper}</span>
                </label>
              ))}
            </div>

            {skillModalError && <p className="hw-inline-note hw-inline-note-warning">{skillModalError}</p>}

            <div className="hw-modal-actions">
              {configuredSkills.includes(activeSkill.id) && <button type="button" className="hw-btn-ghost" onClick={() => deactivateSkill(activeSkill.id)}>Desactivar skill</button>}
              <button type="button" className="hw-btn-primary" onClick={saveSkillConfig}>{configuredSkills.includes(activeSkill.id) ? "Guardar configuracion" : "Activar skill"}</button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
