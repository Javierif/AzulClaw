# Component Reference

Last reviewed: 2026-04-23

## Purpose

This document maps the main source areas to their responsibilities so new contributors can find the right entry point quickly.

## Backend

| Path | Responsibility |
|---|---|
| `azul_backend/azul_brain/main_launcher.py` | Bootstraps the local HTTP app, scheduler, MCP client, and optional Service Bus worker |
| `azul_backend/azul_brain/conversation.py` | Main conversation orchestration, lane selection, streaming, and memory injection |
| `azul_backend/azul_brain/api/routes.py` | Desktop and local Bot Framework HTTP routes, including backend diagnostics |
| `azul_backend/azul_brain/api/services.py` | Aggregation helpers for workspace, memory, runtime, and onboarding data |
| `azul_backend/azul_brain/runtime/store.py` | Persistence for runtime settings, jobs, and process history |
| `azul_backend/azul_brain/runtime/scheduler.py` | Heartbeat execution loop, cron runs, manual runs, and proactive desktop delivery |
| `azul_backend/azul_brain/runtime/heartbeat_intent.py` | Semantic routing and confirmation flow for chat-created heartbeats |
| `azul_backend/azul_brain/channels/servicebus_worker.py` | Local worker for Azure Service Bus activities |
| `azul_backend/azul_brain/bootstrap.py` | Resolves how the backend launches AzulHands in repo and packaged modes |
| `azul_backend/azul_brain/mcp_client.py` | STDIO MCP client, Windows subprocess compatibility, and MCP launcher log capture |
| `azul_backend/azul_brain/memory/` | Persistent memory stack and embedding integration |
| `azul_backend/azul_hands_mcp/mcp_server.py` | Filesystem tool host |
| `azul_backend/azul_hands_mcp/path_validator.py` | Workspace boundary enforcement |

## Desktop

| Path | Responsibility |
|---|---|
| `azul_desktop/src/app/DesktopApp.tsx` | Desktop shell composition and navigation |
| `azul_desktop/src/lib/api.ts` | Frontend API client |
| `azul_desktop/src/features/chat/ChatShell.tsx` | Streaming chat UX and heartbeat confirmation card rendering |
| `azul_desktop/src/features/hatching/HatchingShell.tsx` | First-run setup flow |
| `azul_desktop/src/features/heartbeats/HeartbeatsShell.tsx` | Scheduler and automation UI, including manual run output and delivery status |
| `azul_desktop/src/features/memory/MemoryShell.tsx` | Memory inspection and deletion |
| `azul_desktop/src/features/workspace/WorkspaceShell.tsx` | Workspace browser |
| `azul_desktop/src/features/processes/ProcessesShell.tsx` | Process visibility |
| `azul_desktop/src/features/settings/SettingsShell.tsx` | Local reset, runtime summary, and packaged backend diagnostics |
| `azul_desktop/src-tauri/src/main.rs` | Native process launcher for repo and packaged backend modes |
| `azul_desktop/src-tauri/tauri.conf.json` | Tauri bundling config, backend resources, NSIS target, and desktop shortcut hook |
| `azul_desktop/src-tauri/nsis/desktop-shortcut.nsh` | NSIS hook that creates and removes the desktop shortcut |

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
| `memory/runtime_jobs.json` | Scheduled jobs, cron expressions, and delivery metadata |
| `memory/runtime_pending_actions.json` | Pending heartbeat creation confirmations |
| `memory/runtime_process_history.json` | Recent process execution history |
| `<workspace>/.azul/azul_memory.db` | Durable chat memory and learned facts |
| `%AppData%/com.azulclaw.desktop/runtime/` | Runtime state used by packaged desktop installs |
| `%AppData%/com.azulclaw.desktop/logs/` | Backend and MCP launcher logs used by packaged desktop installs |

## Ownership guidelines

- Backend behavior belongs in `azul_backend`.
- Product interaction design belongs in `azul_desktop`.
- Public channel ingress belongs in `azure`.
- Canonical explanation belongs in `docs`.
