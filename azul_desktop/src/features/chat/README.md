# Chat

The chat feature is the main interaction surface for AzulClaw.

- `ChatShell.tsx` handles conversation streaming, commentary, progress updates, and session state.
- The view consumes `/api/desktop/chat/stream` and falls back to the non-streaming endpoint when needed.
- Runtime metadata such as lane, model, and process id are exposed alongside the assistant reply.

Related docs: [Cognitive Design](../../../../docs/06_cognitive_design.md) and [Memory System](../../../../docs/15_memory_system.md).
