# Setup and Development

Last reviewed: 2026-04-15

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
Copy-Item azul_backend\azul_brain\.env.example azul_backend\azul_brain\.env.local
```

### Desktop

```powershell
cd azul_desktop
npm install
```

## Minimal configuration

Edit `azul_backend/azul_brain/.env.local`.

Most important variables:

```env
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_DEPLOYMENT=
AZURE_OPENAI_FAST_DEPLOYMENT=
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=
PORT=3978
```

Optional but useful:

```env
AZUL_WORKSPACE_ROOT=
AZUL_MEMORY_DB_PATH=
SERVICE_BUS_CONNECTION_STRING=
MicrosoftAppId=
MicrosoftAppPassword=
```

## Running locally

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

### Native desktop shell

```powershell
cd azul_desktop
npm run tauri:dev
```

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

Stop the other backend instance or set a different `PORT`.

### Desktop loads but uses fallback data

The frontend could not reach the backend. Confirm the backend is running and the API base points to `http://localhost:3978`.

### Memory is not being embedded

Check `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` and `AZUL_EMBEDDING_DIM`.

### Channel relay returns auth errors

Verify `MicrosoftAppId`, `MicrosoftAppPassword`, and the Bot Framework auth configuration in Azure Function settings.

## Related documents

- [Architecture Overview](01_architecture.md)
- [Memory System](15_memory_system.md)
- [Azure Bot Deployment Guide](13_azure_bot_deployment_guide.md)
