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
