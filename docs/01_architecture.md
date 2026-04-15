# Architecture Overview

Last reviewed: 2026-04-15

## Purpose

This document explains the current AzulClaw system as a product and as a codebase. It is the best starting point for anyone new to the repository.

## System summary

AzulClaw is built around three cooperating layers:

1. `azul_desktop`
   The user-facing desktop shell.
2. `azul_backend.azul_brain`
   The local orchestration runtime that owns cognition, memory, streaming, and scheduling.
3. `azul_backend.azul_hands_mcp`
   The filesystem tool server that enforces workspace boundaries.

The guiding principle is simple: the assistant can be powerful, but unrestricted local access is never acceptable.

## Runtime topology

```text
Desktop UI
   |
   v
Local HTTP API (`main_launcher.py`)
   |
   +--> Conversation orchestrator
   +--> Runtime store and scheduler
   +--> SQLite memory
   +--> Bot Framework adapter
   |
   v
MCP client
   |
   v
MCP server (`azul_hands_mcp`)
   |
   v
Workspace sandbox
```

## Main execution flows

### Desktop chat

1. The desktop app calls `POST /api/desktop/chat/stream`.
2. The backend triages the request into `fast`, `slow`, or effective `auto`.
3. The orchestrator emits commentary, progress, and response deltas over NDJSON.
4. The final answer, runtime metadata, and conversation history are returned in the `done` event.

### Workspace access

1. The orchestrator decides a file tool is needed.
2. The request is forwarded to the MCP client.
3. The MCP server validates the requested path.
4. The tool executes only if the target remains inside the configured workspace root.

### Channel delivery

1. Azure Bot Service receives a public channel activity.
2. Azure Function validates and relays it through Service Bus.
3. The local worker consumes the activity and routes it into the same local orchestration stack.

## Design principles

### Local-first

Workspace data and memory live on the user's machine by default.

### Security by boundary

Filesystem access is isolated behind the MCP sandbox instead of being mixed into the reasoning layer.

### One runtime, multiple surfaces

Desktop UI, scheduled jobs, and Bot Framework turns all converge on the same core orchestration logic.

### Explicit state

Memory, runtime settings, jobs, and process history are persisted locally so the product can recover predictably across restarts.

## Repository map

```text
azul_backend/       Local runtime and MCP server
azul_desktop/       Desktop shell
azure/              Cloud relay for channels
docs/               Canonical documentation
memory/             Generated local runtime state
scripts/            Utility scripts
```

## Related documents

- [Setup and Development](02_setup_and_development.md)
- [Security Model](03_security_model.md)
- [Component Reference](04_component_reference.md)
