# azul_desktop

Desktop application shell for AzulClaw.

Built with **Tauri, React, TypeScript and Vite** to provide a lightweight native shell with a modern UI and a direct link to the local Python backend.

## Key features

### 1. Hatching
- Initial wizard to define the agent's identity, tone, autonomy, workspace, and base capabilities.
- Dashboard to edit that configuration without repeating the full onboarding.

### 2. Chat and operational control
- **Smart composer:** multiline field with `Enter` to send and `Shift+Enter` for new line.
- **Quick actions:** access to File, Memory, and Workspace from the composer itself.
- **Live context:** side panel showing active lane, triage reason, model, and process tied to the current turn.
- **Dual cognitive streaming:** the main chat uses `POST /api/desktop/chat/stream`. The first visible bubble comes from the `fast` brain; the `slow` route can show a summarised progress card; the final reply arrives via `delta`.
- **Real send state:** the send button uses a persistent loader.

### 3. Secure integration
- The UI does not decide critical cognitive logic.
- The Python backend handles triage, memory, runtime, and streaming.
- The agent workspace continues to act as a visible, understandable sandbox for the user.

## Stack

- **Core:** Tauri 2.x
- **Frontend:** React 19 + TypeScript + Vite
- **Styles:** plain CSS with variables, custom layout, and lightweight animations

## Development guide

To iterate the UI in web mode:

```bash
npm install
npm run dev
```

Notes:
- The dev frontend runs at `http://localhost:1420`.
- Vite proxies `/api` to the local backend at `http://localhost:3978`.
- The main chat flow depends on the incremental endpoint `/api/desktop/chat/stream`.

To open the native desktop app:

```bash
npm run tauri:dev
```

To build the desktop bundle:

```bash
npm run tauri:build
```

## Structure

```text
azul_desktop/
|-- src/
|   |-- app/          # Main shell
|   |-- components/   # Shared components
|   |-- features/     # Product modules
|   |-- lib/          # Contracts, mocks, and HTTP client
|   `-- styles/       # Global styles
|-- src-tauri/        # Tauri native layer
`-- package.json
```
