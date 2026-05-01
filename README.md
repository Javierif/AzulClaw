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

Recommended local configuration uses Azure Key Vault. The Hatching Azure wizard
can discover Key Vault resources in your subscription and save the selected
vault URL in the local profile, so `.env.local` does not need to hold secrets or
the vault pointer.

For headless or manual setups, keep only the vault pointer in your user/machine
environment:

```powershell
setx AZUL_KEY_VAULT_URL "https://your-vault.vault.azure.net"
```

Secret names use the environment variable name with underscores replaced by
hyphens, for example `AZURE_OPENAI_ENDPOINT` is stored as
`AZURE-OPENAI-ENDPOINT`.

AzulClaw supports Microsoft Entra ID for Azure OpenAI and treats it as the
preferred desktop path. Assign the signed-in user an Azure role such as
`Cognitive Services OpenAI User` on the Azure OpenAI resource.

To migrate an existing local env file:

```powershell
python scripts\migrate_env_to_keyvault.py --vault your-vault --delete-env-file
```

### 3. Start the desktop shell

```powershell
cd azul_desktop
npm install
npm run tauri:dev
```

The native Tauri shell starts the backend automatically on `http://localhost:3978` if nothing is already listening there.

For frontend-only web iteration, start the backend in one terminal:

```powershell
python -m azul_backend.azul_brain.main_launcher
```

Then start Vite in another terminal:

```powershell
cd azul_desktop
npm run dev
```

## Windows desktop package

To build the one-click Windows installer, run from the repository root:

```powershell
npm run package:desktop:win
```

The script packages the Python backend and MCP server into internal executables,
bundles them into the Tauri app, and writes the installer to:

```text
azul_desktop/src-tauri/target/release/bundle/nsis/
```

Install that `.exe` and launch AzulClaw from the desktop shortcut or start menu.
The app starts its local backend automatically.

## Installed app configuration

The packaged Windows app does not bundle `.env.local` or any Azure secrets.
For an installed desktop app, point the backend at Key Vault before launching
AzulClaw from the desktop/start menu.

Recommended configuration:

```powershell
setx AZUL_KEY_VAULT_URL "https://your-vault.vault.azure.net"
```

After changing them, fully close AzulClaw and launch it again. If Windows
Explorer already had an old environment snapshot, sign out and back in.

Optional Entra settings:

```powershell
setx AZURE_TENANT_ID "<your-tenant-id>"
setx AZUL_ENABLE_INTERACTIVE_BROWSER_AUTH "true"
setx AZUL_ENTRA_BROWSER_CLIENT_ID "<desktop-app-registration-client-id>"
```

If interactive browser auth is not configured, the backend still supports other
`DefaultAzureCredential` sources such as Azure CLI, Visual Studio Code, and the
Windows shared token cache.

Settings now includes a backend diagnostics section that shows:

- whether the local backend is reachable
- how many model profiles are enabled
- the Microsoft Entra sign-in state for Azure OpenAI
- the runtime and log directories
- recent backend and MCP launcher logs

When `AZUL_AZURE_OPENAI_AUTH_MODE=entra`, the desktop app triggers Azure OpenAI
authentication as part of startup instead of waiting for the first chat request.

If chat replies with `No enabled model profiles found.`, the backend is usually
running but missing provider configuration. In Entra mode, also verify that the
current user can obtain a token and has Azure RBAC access to the Azure OpenAI
resource.

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
