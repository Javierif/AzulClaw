# Chat

The chat feature is the main interaction surface for AzulClaw.

- `ChatShell.tsx` handles conversation streaming, commentary, progress updates, session state, and active conversation switching.
- The view consumes `/api/desktop/chat/stream` and falls back to the non-streaming endpoint when needed.
- Runtime metadata such as lane, model, and process id are exposed alongside the assistant reply.
- The composer supports local attachments from the file picker, pasted files, and desktop clipboard paths in Tauri mode.
- Draft attachments are uploaded before send, rendered as attachment chips, and bound to the persisted user message after the turn is stored.
- The right-side context panel keeps lightweight runtime and memory context visible while chatting.
- Chat also renders interactive heartbeat confirmation cards for automation created through natural language.

Related docs: [Cognitive Design](../../../../docs/06_cognitive_design.md) and [Memory System](../../../../docs/15_memory_system.md).
