# Azul Backend

`azul_backend/` contains the local Python runtime that powers AzulClaw.

## Responsibilities

- Host the local HTTP API consumed by the desktop shell.
- Orchestrate model selection, chat turns, and streaming responses.
- Persist memory and runtime state on disk.
- Run heartbeats and scheduled jobs.
- Handle Bot Framework traffic for local or relayed channels.
- Connect to the MCP sandbox for workspace file operations.

## Layout

```text
azul_backend/
|- azul_brain/       Main runtime
|- azul_hands_mcp/   Sandboxed filesystem tool server
|- workspace_layout.py
`- README.md
```

## Key modules

- `azul_brain/main_launcher.py`
- `azul_brain/conversation.py`
- `azul_brain/api/`
- `azul_brain/runtime/`
- `azul_brain/channels/`
- `azul_brain/memory/`
- `azul_hands_mcp/`

## Run locally

From the repository root:

```powershell
python -m azul_backend.azul_brain.main_launcher
```

The backend defaults to `http://localhost:3978`.

## Configuration

Primary config lives in `azul_backend/azul_brain/.env.local`.

See [Setup and Development](../docs/02_setup_and_development.md) and [Memory System](../docs/15_memory_system.md).
