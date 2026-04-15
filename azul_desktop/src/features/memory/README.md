# Memory

The memory feature exposes what AzulClaw has learned and retained locally.

- `MemoryShell.tsx` lists learned memory records and supports deletion.
- Data comes from the local SQLite-backed memory layer through `/api/desktop/memory`.
- The view is intentionally scoped to user-visible durable memory rather than raw conversation history.

Related docs: [Memory System](../../../../docs/15_memory_system.md).
