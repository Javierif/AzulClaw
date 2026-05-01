# Setup and Development

Last reviewed: 2026-04-23

## Prerequisites

| Requirement | Recommended version | Notes |
|---|---|---|
| Python | 3.11+ | Required for the backend |
| Node.js | 20+ | Recommended for the desktop shell |
| npm | current LTS | Used by `azul_desktop` |
| Rust toolchain | current stable | Required only for `tauri:dev` and native builds |
| Git | current | Repository checkout |

## Initial setup

### Backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Desktop

```powershell
cd azul_desktop
npm install
```

## Minimal configuration

Recommended desktop configuration uses Azure Key Vault for runtime settings and
Microsoft Entra ID instead of a local API key.

The signed-in user or app identity needs Azure RBAC access to the Azure OpenAI
resource, typically `Cognitive Services OpenAI User`.

The Hatching Azure wizard can discover Key Vault resources in the selected
subscription and persist the selected vault URL in the local profile. That keeps
the pointer out of `.env.local` while still letting the backend hydrate runtime
settings on the next startup.

For headless or manual setups, keep only the Key Vault pointer in the local user
or machine environment:

```powershell
setx AZUL_KEY_VAULT_URL "https://your-vault.vault.azure.net"
```

Store runtime settings as Key Vault secrets. Secret names use the environment
variable name with underscores replaced by hyphens:

```text
AZURE_OPENAI_ENDPOINT -> AZURE-OPENAI-ENDPOINT
AZURE_OPENAI_DEPLOYMENT -> AZURE-OPENAI-DEPLOYMENT
AZURE_OPENAI_FAST_DEPLOYMENT -> AZURE-OPENAI-FAST-DEPLOYMENT
AZURE_OPENAI_EMBEDDING_DEPLOYMENT -> AZURE-OPENAI-EMBEDDING-DEPLOYMENT
AZUL_AZURE_OPENAI_AUTH_MODE -> AZUL-AZURE-OPENAI-AUTH-MODE
PORT -> PORT
```

To migrate an existing local env file:

```powershell
python scripts\migrate_env_to_keyvault.py --vault your-vault --delete-env-file
```

The backend loads known runtime keys automatically. Add unusual keys with
`AZUL_KEY_VAULT_ENV_KEYS` as a comma-separated list.

`.env.local` is still ignored by Git for legacy checkouts, but it should not be
used for active secrets or normal local configuration.

Optional Entra settings can also live in Key Vault:

```text
AZURE_TENANT_ID
AZUL_ENABLE_INTERACTIVE_BROWSER_AUTH
AZUL_ENTRA_BROWSER_CLIENT_ID
```

## Running locally

### Native desktop shell

```powershell
cd azul_desktop
npm run tauri:dev
```

The native shell starts the backend automatically on `http://localhost:3978` if nothing is already listening there. Backend stdout and stderr are written under `memory/runtime-logs/`.

### Backend only

From the repository root:

```powershell
python -m azul_backend.azul_brain.main_launcher
```

Available local endpoints include:

- `GET /api/health`
- `POST /api/desktop/chat`
- `POST /api/desktop/chat/stream`
- `GET /api/desktop/memory`
- `GET /api/desktop/workspace`
- `GET /api/desktop/runtime`
- `GET /api/desktop/jobs`
- `POST /api/messages`

### Desktop in web mode

```powershell
cd azul_desktop
npm run dev
```

Web mode does not start the backend automatically; run the backend command above in a separate terminal.

## Packaging for Windows

From the repository root:

```powershell
npm run package:desktop:win
```

The packaging flow:

1. Installs build-only Python requirements.
2. Uses PyInstaller to create internal executables for `azul-backend` and `azul-hands-mcp`.
3. Copies those executables into `azul_desktop/resources/backend/`.
4. Runs `npm run tauri:build` to create the NSIS installer.

Output:

```text
azul_desktop/src-tauri/target/release/bundle/nsis/AzulClaw_0.1.0_x64-setup.exe
```

The installer creates a start menu entry and a desktop shortcut. The installed
desktop app starts the packaged backend automatically. Runtime state is stored in
the user's app data directory, while workspace and memory data remain inside the
configured AzulClaw workspace.

### Installed app configuration

The packaged app does not include `azul_backend/azul_brain/.env.local`.
For installed desktop builds, point the backend at Key Vault before launching
AzulClaw.

Recommended variable:

```powershell
setx AZUL_KEY_VAULT_URL "https://your-vault.vault.azure.net"
```

Optional Entra settings:

```powershell
setx AZURE_TENANT_ID "<your-tenant-id>"
setx AZUL_ENABLE_INTERACTIVE_BROWSER_AUTH "true"
setx AZUL_ENTRA_BROWSER_CLIENT_ID "<desktop-app-registration-client-id>"
```

Close AzulClaw completely after changing environment variables. If the desktop
shortcut still launches with old values, sign out and back in to refresh the
Explorer environment.

Without interactive browser auth, `DefaultAzureCredential` still supports other
developer and desktop sign-in sources such as Azure CLI, Visual Studio Code,
Azure PowerShell, and the Windows shared token cache.

### Installed app diagnostics

Settings now exposes a backend diagnostics panel for packaged builds. It shows:

- backend reachability
- enabled model count
- Azure OpenAI authentication state
- scheduler status
- runtime directory
- log directory
- recent tails of `desktop-backend.out.log`, `desktop-backend.err.log`, and `azul-hands-mcp.err.log`

When desktop auth mode is `entra`, the app requests Azure OpenAI authentication
on startup so the user is prompted before the first chat turn if sign-in is
required. Settings also exposes a manual `Authenticate now` action to retry the
flow.

The desktop backend status endpoint is:

- `GET /api/desktop/backend/status`

## Workspace and memory defaults

If `AZUL_WORKSPACE_ROOT` is not set, AzulClaw creates a default workspace under the user's documents area.

At first startup the backend also seeds:

- `Inbox/`
- `Projects/`
- `Generated/`
- `WORKSPACE.md`
- `.azul/azul_memory.db`

## Common issues

### Port `3978` is already in use

The native desktop shell reuses an existing backend on this port. For manual backend runs, stop the other backend instance or set a different `PORT`.

### Desktop loads but uses fallback data

The frontend could not reach the backend. Confirm the backend is running and the API base points to `http://localhost:3978`.

### Chat replies with `No enabled model profiles found.`

This usually means the backend is running, but no usable provider configuration
was available when it loaded runtime settings. Check the Settings diagnostics
panel, then confirm the installed app has the required Azure environment
variables. In Entra mode, also verify that the signed-in user has Azure RBAC
access to the Azure OpenAI resource.

### Memory is not being embedded

Check `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` and `AZUL_EMBEDDING_DIM`.

### Channel relay returns auth errors

Verify `MicrosoftAppId`, `MicrosoftAppPassword`, and the Bot Framework auth configuration in Azure Function settings.

## Related documents

- [Architecture Overview](01_architecture.md)
- [Memory System](15_memory_system.md)
- [Azure Bot Deployment Guide](13_azure_bot_deployment_guide.md)
