# AzulClaw

<p align="center">
  <img width="600" height="400" alt="AzulClaw" src="https://github.com/user-attachments/assets/c73da31c-f0e1-416e-9da7-ee5e30650857" />
</p>

<p align="center">
  <a href="https://discord.gg/gggT7Bx858">
    <img alt="Join the AzulClaw Discord community" src="https://img.shields.io/badge/Discord-Join%20the%20community-5865F2?logo=discord&logoColor=white" />
  </a>
</p>

<p align="center">
  Build AzulClaw with us, share feedback, and follow product progress in the community server.
</p>

AzulClaw is a local-first AI companion that combines a secure desktop workspace, a Python orchestration layer, and Azure-backed reasoning. The product is designed around one constraint: the assistant must be useful without being allowed to roam freely across the user's machine.

## What AzulClaw is

- A desktop shell built with Tauri, React, and TypeScript.
- A local Python runtime that handles chat, memory, scheduling, process tracking, and Bot Framework activities.
- A sandboxed file tool layer exposed through MCP so filesystem access stays isolated and auditable.
- An optional Azure relay for public channels such as Telegram or Alexa without exposing the local runtime directly.

## Architecture at a glance

```text
Desktop UI (Tauri + React)
        |
        v
Local HTTP API (aiohttp)
        |
        +--> Conversation orchestrator
        +--> Runtime scheduler and heartbeats
        +--> SQLite memory
        +--> Bot Framework adapter
        |
        v
MCP sandbox (filesystem tools inside workspace boundary)
```

For public channels, the production path is:

```text
Channel -> Azure Bot Service -> Azure Function -> Azure Service Bus -> Local AzulClaw
```

## Repository layout

```text
AzulClaw/
|- azul_backend/     Python runtime, memory, channels, MCP integration
|- azul_desktop/     Desktop shell and frontend views
|- azure/            Azure relay resources and deployment artifacts
|- docs/             Canonical product and technical documentation
|- memory/           Local runtime state generated during development
|- scripts/          Utility scripts
|- README.md
`- requirements.txt
```

## Quick start

### 1. Install backend dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure the backend

```powershell
Copy-Item azul_backend\azul_brain\.env.example azul_backend\azul_brain\.env.local
```

Fill in the Azure values you want to use. For local desktop iteration, only the backend config is required.

### 3. Start the backend

```powershell
python -m azul_backend.azul_brain.main_launcher
```

The local API listens on `http://localhost:3978`.

### 4. Start the desktop shell

```powershell
cd azul_desktop
npm install
npm run dev
```

For the native Tauri shell:

```powershell
npm run tauri:dev
```

## Core capabilities

- Fast and slow model lanes with automatic triage.
- Streaming desktop chat over NDJSON.
- Local persistent memory in SQLite with vector, keyword, and hybrid retrieval.
- Hatching flow for profile and workspace setup.
- Workspace browsing restricted to a dedicated sandbox root.
- Heartbeats and scheduled jobs stored locally.
- Optional Azure relay for Bot Framework channels.

## Documentation

Start with [Documentation Hub](docs/README.md).

Recommended reading order:

1. [Architecture Overview](docs/01_architecture.md)
2. [Setup and Development](docs/02_setup_and_development.md)
3. [Security Model](docs/03_security_model.md)
4. [Component Reference](docs/04_component_reference.md)
5. [Memory System](docs/15_memory_system.md)

## Notes for contributors

- Keep documentation in English.
- Treat `docs/` as the canonical source for product and architecture decisions.
- Do not commit `.env.local`, generated workspace data, or credentials.
- The MCP sandbox is a security boundary, not a convenience wrapper.
