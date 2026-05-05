# Azul Desktop

`azul_desktop/` is the desktop shell for AzulClaw. It provides the visible product surface while the Python backend remains the source of truth for cognition, memory, and runtime behavior.

## Stack

- Tauri 2
- React 19
- TypeScript
- Vite

## Main views

- Chat
- Hatching
- Heartbeats
- Memory
- Workspace
- Processes
- Settings

## Commands

```powershell
npm install
npm run dev
npm run tauri:dev
npm run build
npm run tauri:build
```

During development, the frontend expects the backend on `http://localhost:3978`.
`npm run tauri:dev` starts the backend automatically if the port is free. `npm run dev`
is web-only and still expects you to start the backend separately.

In packaged builds, the desktop shell starts bundled `azul-backend` and
`azul-hands-mcp` executables from Tauri resources instead of using the repo's
Python environment.

## Packaging

From the repository root:

```powershell
npm run package:desktop:win
```

This builds the backend and MCP server into internal PyInstaller executables,
adds them as Tauri resources, and creates the NSIS installer under
`src-tauri/target/release/bundle/nsis/`.

Packaged desktop builds do not bundle `.env.local`. Provider credentials must be
supplied through Windows environment variables or a future in-app credential flow.

## Diagnostics

Settings includes a backend diagnostics panel that shows:

- whether the local backend is reachable
- how many model profiles are enabled
- runtime and log directories
- recent backend and MCP launcher logs

This is the first place to check when packaged chat appears to start but runtime
configuration or credentials are missing.

## Structure

```text
azul_desktop/
|- src/app/          Application shell
|- src/components/   Shared UI components
|- src/features/     Product features by domain
|- src/lib/          API client, contracts, and helpers
|- src/styles/       Global styling and theme tokens
`- src-tauri/        Native wrapper
```

See [Desktop Architecture and Repository Structure](../docs/10_desktop_architecture_and_repo_structure.md).
