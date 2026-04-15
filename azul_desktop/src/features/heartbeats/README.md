# Heartbeats

Heartbeats are the unified automation model for AzulClaw.

- `HeartbeatsShell.tsx` displays built-in and user-created jobs.
- The UI talks to `/api/desktop/jobs` and runtime status endpoints.
- The system heartbeat is a protected built-in job that keeps the assistant checking `HEARTBEAT.md` in the workspace.

Related docs: [Heartbeats System](../../../../docs/11_heartbeats_system.md).
