import { useState } from "react";

import { sendDesktopMessage } from "../../lib/api";
import { chatMessages } from "../../lib/mock-data";

export function ChatShell() {
  const [messages, setMessages] = useState(chatMessages);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);

  async function handleSend() {
    const trimmed = draft.trim()
    if (!trimmed || isSending) {
      return;
    }

    setIsSending(true);
    const nextUserMessage = {
      id: `user-${Date.now()}`,
      role: "user" as const,
      content: trimmed,
    };
    setMessages((current) => [...current, nextUserMessage]);
    setDraft("");

    const response = await sendDesktopMessage(trimmed);
    setMessages((current) => [
      ...current,
      {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: response.reply,
      },
    ]);
    setIsSending(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  return (
    <section className="chat-layout">
      <div className="chat-panel card">
        <div className="chat-header">
          <div>
            <p className="eyebrow">Main Session</p>
            <h2>Conversación principal</h2>
          </div>
          <div className="action-row">
            <button type="button" className="ghost-button">
              Add skill
            </button>
            <button type="button" className="ghost-button">
              New task
            </button>
          </div>
        </div>

        <div className="message-list">
          {messages.map((message) => (
            <article
              key={message.id}
              className={`message-bubble message-${message.role}`}
            >
              <span className="message-role">
                {message.role === "user" ? "You" : "AzulClaw"}
              </span>
              <p>{message.content}</p>
            </article>
          ))}
        </div>

        <div className="composer">
          <div className="composer-wrapper">
            <label className="composer-field">
              <span className="sr-only">Message AzulClaw</span>
              <textarea
                placeholder="Escribe un mensaje... (Enter para enviar, Shift+Enter para nueva línea)"
                rows={1}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={handleKeyDown}
              />
            </label>
            <div className="composer-bottom">
              <div className="composer-actions">
                <button type="button" className="ghost-button-mini" title="Adjuntar un archivo local" aria-label="Adjuntar archivo">
                  📎 Archivo
                </button>
                <button type="button" className="ghost-button-mini" title="Buscar en las preferencias del agente" aria-label="Añadir Memoria">
                  🧠 Memoria
                </button>
                <button type="button" className="ghost-button-mini" title="Buscar un documento del Workspace" aria-label="Añadir del Workspace">
                  🗂️ Workspace
                </button>
              </div>
              <button 
                type="button" 
                className="composer-send-btn" 
                onClick={() => void handleSend()} 
                disabled={isSending || !draft.trim()}
              >
                {isSending ? "..." : "Enviar ➔"}
              </button>
            </div>
          </div>
          <div className="composer-footer">
            <span className="hint-text">AzulClaw puede cometer errores. Considera verificar la información importante.</span>
          </div>
        </div>
      </div>

      <aside className="context-panel card">
        <div>
          <p className="eyebrow">Live Context</p>
          <h2>Actividad de sesion</h2>
        </div>

        <section className="context-section">
          <h3>Estado actual</h3>
          <ul className="context-list">
            {[
              "Leyendo archivos del sandbox",
              "Resumiendo notas recientes",
              "Esperando futuras aprobaciones sensibles",
            ].map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <section className="context-section">
          <h3>Memoria reciente</h3>
          <p>
            Preferencia fijada: respuestas directas, foco en pasos concretos y
            visibilidad de procesos.
          </p>
        </section>

        <section className="context-section">
          <h3>Workspace</h3>
          <p>
            Ruta activa: <code>Desktop\AzulWorkspace</code>
          </p>
        </section>
      </aside>
    </section>
  );
}
