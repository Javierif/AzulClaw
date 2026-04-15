# Component Reference

Last reviewed: 2026-04-15

## Purpose

This document maps the main source areas to their responsibilities so new contributors can find the right entry point quickly.

## Backend

| Path | Responsibility |
|---|---|
| `azul_backend/azul_brain/main_launcher.py` | Bootstraps the local HTTP app, scheduler, MCP client, and optional Service Bus worker |
| `azul_backend/azul_brain/conversation.py` | Main conversation orchestration, lane selection, streaming, and memory injection |
| `azul_backend/azul_brain/api/routes.py` | Desktop and local Bot Framework HTTP routes |
| `azul_backend/azul_brain/api/services.py` | Aggregation helpers for workspace, memory, runtime, and onboarding data |
| `azul_backend/azul_brain/runtime/store.py` | Persistence for runtime settings, jobs, and process history |
| `azul_backend/azul_brain/runtime/scheduler.py` | Heartbeat and scheduled job execution loop |
| `azul_backend/azul_brain/channels/servicebus_worker.py` | Local worker for Azure Service Bus activities |
| `azul_backend/azul_brain/memory/` | Persistent memory stack and embedding integration |
| `azul_backend/azul_hands_mcp/mcp_server.py` | Filesystem tool host |
| `azul_backend/azul_hands_mcp/path_validator.py` | Workspace boundary enforcement |

## Desktop

| Path | Responsibility |
|---|---|
| `azul_desktop/src/app/DesktopApp.tsx` | Desktop shell composition and navigation |
| `azul_desktop/src/lib/api.ts` | Frontend API client |
| `azul_desktop/src/features/chat/ChatShell.tsx` | Streaming chat UX |
| `azul_desktop/src/features/hatching/HatchingShell.tsx` | First-run setup flow |
| `azul_desktop/src/features/heartbeats/HeartbeatsShell.tsx` | Scheduler and automation UI |
| `azul_desktop/src/features/memory/MemoryShell.tsx` | Memory inspection and deletion |
| `azul_desktop/src/features/workspace/WorkspaceShell.tsx` | Workspace browser |
| `azul_desktop/src/features/processes/ProcessesShell.tsx` | Process visibility |
| `azul_desktop/src/features/settings/SettingsShell.tsx` | Local reset and settings actions |

## Azure relay

| Path | Responsibility |
|---|---|
| `azure/functions/bot_relay/function_app.py` | Azure Function relay for Bot Framework traffic |
| `azure/functions/bot_relay/access_control.py` | Channel allowlist parsing and evaluation |
| `azure/functions/bot_relay/local.settings.example.json` | Local development settings example |

## Local data

| Path | Responsibility |
|---|---|
| `memory/runtime_settings.json` | Persisted runtime configuration |
| `memory/runtime_jobs.json` | Scheduled jobs |
| `memory/runtime_process_history.json` | Recent process execution history |
| `<workspace>/.azul/azul_memory.db` | Durable chat memory and learned facts |

## Ownership guidelines

- Backend behavior belongs in `azul_backend`.
- Product interaction design belongs in `azul_desktop`.
- Public channel ingress belongs in `azure`.
- Canonical explanation belongs in `docs`.
