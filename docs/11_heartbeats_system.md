# Heartbeats System

Last reviewed: 2026-04-15

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
- schedule kind
- next run timestamp
- run now action

## Storage

Heartbeat state is stored in the local runtime files under `memory/`, primarily `runtime_jobs.json`.

## Backend implementation

Main files:

- `azul_backend/azul_brain/runtime/store.py`
- `azul_backend/azul_brain/runtime/scheduler.py`

## Frontend implementation

The desktop shell for this area is:

- `azul_desktop/src/features/heartbeats/HeartbeatsShell.tsx`
