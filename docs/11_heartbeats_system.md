# Heartbeats System

Last reviewed: 2026-04-20

## Purpose

Heartbeats are AzulClaw's unified automation model. They cover both the protected system pulse and user-created recurring tasks.

The important product rule is:

- system heartbeats maintain the workspace-aware system pulse
- user heartbeats produce proactive desktop messages or lightweight scheduled actions

Those two paths share scheduling and persistence, but they do not execute with the same cognitive context.

## Concepts

### System heartbeat

The built-in system heartbeat:

- uses the fixed id `system-heartbeat`
- is created or repaired automatically on backend startup
- cannot be deleted
- uses `schedule_kind: every`
- reads `HEARTBEAT.md` from the configured workspace
- skips execution when `HEARTBEAT.md` is empty or contains only comments

The system heartbeat is the only heartbeat that should inspect `HEARTBEAT.md` by default. If the file has no active checklist lines, the runtime records the run and returns `HEARTBEAT_SKIP`.

System no-op outputs such as `HEARTBEAT_OK` and `HEARTBEAT_SKIP` are not delivered to chat.

### User heartbeats

User heartbeats are created by the user through chat or the Heartbeats view. They support:

- `prompt`: the scheduled task text
- `enabled`: whether the job can run
- `schedule_kind`: `cron`, `every`, or `at`
- `cron_expression`: standard 5-field Linux cron for cron-backed jobs
- `next_run_at`: the next UTC execution timestamp
- `last_run_at`: the latest completed execution timestamp
- `delivery_kind`: currently `desktop_chat` or `none`
- `delivery_user_id`: defaults to `desktop-user`
- `delivery_conversation_id`: persisted fallback conversation after first delivery

For chat-created recurring tasks, the preferred schedule format is cron. Cron lets the system represent schedules such as every minute, hourly, or every Monday at 09:00 without relying on LLM math for seconds.

Cron expressions are evaluated in the machine's local timezone and persisted as UTC `next_run_at` timestamps.

## Storage

Runtime state is stored under `memory/`.

Important files:

- `memory/runtime_jobs.json`: scheduled jobs and delivery metadata
- `memory/runtime_settings.json`: model/runtime configuration
- `memory/runtime_pending_actions.json`: pending heartbeat creation confirmations
- `memory/runtime_process_history.json`: recent process visibility

The durable desktop chat database is resolved through the hatching profile and is usually stored under `<workspace>/.azul/azul_memory.db`.

## Backend

Main implementation files:

- `azul_backend/azul_brain/runtime/store.py`
- `azul_backend/azul_brain/runtime/scheduler.py`
- `azul_backend/azul_brain/runtime/heartbeat_intent.py`
- `azul_backend/azul_brain/runtime/agent_runtime.py`
- `azul_backend/azul_brain/cortex/kernel_setup.py`
- `azul_backend/azul_brain/memory/safe_memory.py`
- `azul_backend/azul_brain/api/routes.py`
- `azul_backend/azul_brain/api/services.py`

### Store responsibilities

`RuntimeStore` owns persistence and validation:

- loads legacy and current job payloads
- repairs the protected system heartbeat
- blocks deletion of system jobs
- validates `at`, `every`, and `cron` schedules
- calculates `next_run_at`
- persists delivery metadata

Cron support depends on `croniter`.

### Scheduler responsibilities

`RuntimeScheduler` owns execution:

- checks due jobs every scheduler tick
- prevents duplicate concurrent runs for the same job id
- supports manual `Run now`
- marks `last_run_at` and recomputes `next_run_at`
- returns delivery metadata in run results
- delivers visible outputs to desktop chat

System and user jobs execute differently.

System job path:

1. Load active lines from `HEARTBEAT.md`.
2. Skip if nothing actionable exists.
3. Inject the active checklist into the system heartbeat prompt.
4. Execute through the normal orchestrator with source `heartbeat`.

User job path:

1. Build a proactive-message prompt from the stored job prompt.
2. Execute with the Agent Framework runtime as `cron:<job_id>`.
3. Disable tools for that execution.
4. Use heartbeat-specific instructions that tell the model to return only the desktop chat message.
5. Deliver the returned text to desktop chat.

This is intentional. User reminders such as "send me a greeting every minute" must not read the workspace, inspect `HEARTBEAT.md`, ask where to send the message, or react to unrelated active-chat history.

### Semantic creation flow

`HeartbeatIntentService` intercepts chat turns before normal conversation handling.

It uses the fast runtime as a semantic router with native structured outputs. The route model is `HeartbeatRouteModel`, and the possible routes are:

- `create_heartbeat`
- `confirm_pending`
- `cancel_pending`
- `none`

For `create_heartbeat`, the structured draft includes:

- `name`
- `prompt`
- `cron_expression`
- `lane`

The router must return a standard 5-field Linux cron expression. If it cannot understand the frequency, or if the draft is incomplete, AzulClaw asks the user to clarify the schedule instead of falling back to regex or guessing.

There is no local regex fallback for natural-language scheduling. The human is the fallback.

### Confirmation

If hatching settings require confirmation for sensitive actions, the draft is stored in `memory/runtime_pending_actions.json`.

The backend still returns a text representation for compatibility:

```text
I can create this heartbeat:

Name: recordatorio_agua
Schedule: `0 * * * *`
Action: Remind user to drink water
Delivery: desktop chat

Reply 'yes, create it' to activate it or 'no' to cancel.
```

The desktop renders that response as an interactive card instead of showing the raw text.

The buttons send normal chat messages:

- Create heartbeat: `yes, create it`
- Cancel: `no`

The backend routes those messages through the same semantic router as `confirm_pending` or `cancel_pending`.

### Delivery

When a user heartbeat produces text, the scheduler stores it as an assistant message in desktop chat.

Delivery order:

1. Use the active desktop chat conversation for `delivery_user_id`.
2. If no active conversation exists, reuse `delivery_conversation_id` if still valid.
3. If no valid fallback exists, create a conversation named `Heartbeat: <job name>`.
4. Persist the fallback conversation id on the job.

The desktop marks a conversation active when:

- the user sends a streamed chat message in that conversation
- the user opens a conversation and loads its messages
- the desktop creates a new conversation

Successful user-heartbeat messages are delivered without a visible `Heartbeat: ...` prefix. They should feel like direct messages from AzulClaw. Failed executions are delivered with `Heartbeat failed: <job name>` so the error is clear.

## Desktop

Main UI files:

- `azul_desktop/src/features/chat/ChatShell.tsx`
- `azul_desktop/src/features/heartbeats/HeartbeatsShell.tsx`
- `azul_desktop/src/lib/api.ts`
- `azul_desktop/src/lib/contracts.ts`
- `azul_desktop/src/styles/global.css`

### Chat confirmation card

`ChatShell` parses the compatibility confirmation text returned by the backend and renders a `HeartbeatConfirmationCard`.

The card displays:

- heartbeat name
- cron schedule
- action
- delivery target
- Create heartbeat button
- Cancel button

This keeps the backend contract simple while avoiding a raw confirmation blob in the chat UI.

### Heartbeats view

`HeartbeatsShell` displays:

- system heartbeat controls
- custom heartbeats
- schedule tags
- enable/pause actions
- delete action for non-system jobs
- `Run now`
- latest manual-run output
- delivery status such as `Delivered to chat: <conversation>`

`Run now` is primarily a manual execution and debugging affordance. It returns the backend run result, including `response`, `next_run_at`, and delivery metadata.

## API

Relevant desktop endpoints:

- `GET /api/desktop/runtime`
- `PUT /api/desktop/runtime`
- `GET /api/desktop/jobs`
- `POST /api/desktop/jobs`
- `POST /api/desktop/jobs/{job_id}/run`
- `DELETE /api/desktop/jobs/{job_id}`
- `GET /api/desktop/conversations`
- `GET /api/desktop/conversations/{conv_id}/messages`
- `POST /api/desktop/chat/stream`

`POST /api/desktop/jobs/{job_id}/run` returns the manual run result:

```json
{
  "job_id": "heartbeat-...",
  "reason": "manual",
  "ok": true,
  "response": "Hola. Recuerda beber agua.",
  "next_run_at": "2026-04-20T18:00:00Z",
  "delivery": {
    "kind": "desktop_chat",
    "user_id": "desktop-user",
    "conversation_id": "...",
    "conversation_title": "New conversation"
  }
}
```

## Testing

Backend coverage lives in:

- `tests/test_runtime_heartbeats.py`
- `tests/test_heartbeat_intent.py`

Important behaviors covered:

- system heartbeat creation and repair
- system heartbeat deletion protection
- cron next-run calculation
- semantic routing with structured outputs
- pending confirmation creation
- confirmation/cancellation flow
- proactive desktop chat delivery
- active conversation preference
- user heartbeat execution without tools

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Frontend validation:

```powershell
npm run build --prefix azul_desktop
```

In the current sandbox, `vite`/`esbuild` may fail with `spawn EPERM`; run the same command outside the sandbox when that happens.

## Troubleshooting

### A reminder talks about HEARTBEAT.md or workspace files

The running backend is probably still using an older scheduler process, or the job is the protected system heartbeat rather than a user heartbeat.

Restart the backend. User heartbeats should execute with no tools and no workspace inspection.

### A heartbeat runs but no chat message appears

Check:

- the job has `delivery_kind: desktop_chat`
- the backend process has been restarted after code changes
- the desktop has an active conversation, or the job has a valid `delivery_conversation_id`
- `Run now` shows delivery metadata

If there is no active conversation, the scheduler should create or reuse a `Heartbeat: <job name>` conversation.

### The confirmation appears as raw text instead of a card

Check that the desktop bundle includes `HeartbeatConfirmationCard` from `ChatShell.tsx` and the `message-heartbeat-card` styles. Reload the desktop window after frontend changes.

### The card renders as a thin line

The card must not be clipped. `.message-heartbeat-card` uses `overflow: visible`.

### The model asks for a frequency

The semantic router could not produce a valid cron expression. The expected fallback is to ask the user for clarification. Regex fallback is intentionally not used.
