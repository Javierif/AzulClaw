# Heartbeats

Heartbeats are the unified automation model for AzulClaw.

- `HeartbeatsShell.tsx` displays built-in and user-created jobs.
- The UI talks to `/api/desktop/jobs`, `/api/desktop/jobs/{id}/run`, and runtime status endpoints.
- The system heartbeat is a protected built-in job that checks `HEARTBEAT.md` in the workspace.
- User heartbeats are delivered as proactive desktop chat messages.
- `Run now` executes a job immediately, shows delivery status, and renders the latest manual-run output.
- Chat-created heartbeats are confirmed in `ChatShell.tsx` with an interactive heartbeat draft card.

Related docs: [Heartbeats System](../../../../docs/11_heartbeats_system.md).
