# Heartbeats System

Last reviewed: 2026-04-20

## Purpose

Heartbeats are AzulClaw's unified automation model. They replace the idea of separate "system pulse" and "scheduled jobs" with one scheduler and one persistence model.

## Main concepts

### System heartbeat

The built-in heartbeat:

- uses the fixed id `system-heartbeat`
- cannot be deleted
- is created automatically if missing
- checks `HEARTBEAT.md` in the workspace

If nothing actionable exists, the runtime can skip expensive work instead of forcing a full slow-lane turn.

### User heartbeats

User-defined heartbeats share the same scheduler and persistence layer.

Supported concepts include:

- prompt
- enabled state
- schedule kind (`every`, `at`, or `cron`)
- cron expression for cron-backed recurring jobs
- next run timestamp
- run now action
- proactive delivery to a desktop chat conversation

## Storage

Heartbeat state is stored in the local runtime files under `memory/`, primarily `runtime_jobs.json`.

## Backend implementation

Main files:

- `azul_backend/azul_brain/runtime/store.py`
- `azul_backend/azul_brain/runtime/scheduler.py`
- `azul_backend/azul_brain/runtime/heartbeat_intent.py`

## Chat creation flow

Users can create custom heartbeats from chat with natural-language requests such as:

```text
Every 30 minutes, review my Inbox.
```

The backend uses a hybrid flow:

1. The fast runtime is used as a semantic router through Agent Framework messages.
2. The router returns one structured route: `create_heartbeat`, `confirm_pending`, `cancel_pending`, or `none`.
3. Native structured outputs are requested with a Pydantic model; the backend does not parse JSON text from the model.
4. For `create_heartbeat`, the router must include a structured heartbeat draft with a standard 5-field Linux cron expression.
5. If the schedule is ambiguous or the route cannot provide a valid cron expression, AzulClaw asks the user to clarify the frequency instead of guessing.
6. The backend validates the draft before creating anything.
7. If sensitive-action confirmation is enabled in Hatching, the draft is stored in `memory/runtime_pending_actions.json` until the user confirms it.
8. On confirmation, the backend creates the cron job through `RuntimeStore.upsert_job`.

Cron expressions are evaluated in the machine's local timezone and persisted as UTC `next_run_at` timestamps.

## Proactive delivery

Scheduled jobs do not only return an internal API result. When a heartbeat produces text, the scheduler stores that output as an assistant message in desktop chat so the user can actually receive it.

Delivery defaults:

- `delivery_kind`: `desktop_chat`
- `delivery_user_id`: `desktop-user`
- `delivery_conversation_id`: persisted after the first delivery

The scheduler prefers the desktop user's active chat conversation. The desktop marks a conversation active whenever it is opened or used for a streamed chat turn. If there is no active conversation, the first delivery for a job creates a named conversation such as `Heartbeat: Work reminder`; future fallback deliveries reuse that conversation. `Run now` returns the delivery metadata so the Heartbeats UI can show where the output was sent.

Heartbeat generation is isolated from normal desktop chat history by running custom jobs as `cron:<job_id>`. User-created heartbeats use a no-tools Agent Framework runtime with proactive-message instructions, so simple reminders do not inspect the workspace, read `HEARTBEAT.md`, or ask where to send the message. This keeps recurring reminders from reacting to unrelated active-chat context while still delivering the finished message to the user's active conversation.

System heartbeat outputs `HEARTBEAT_OK` and `HEARTBEAT_SKIP` are not delivered to chat, because they are operational no-op results.

## Frontend implementation

The desktop shell for this area is:

- `azul_desktop/src/features/heartbeats/HeartbeatsShell.tsx`
