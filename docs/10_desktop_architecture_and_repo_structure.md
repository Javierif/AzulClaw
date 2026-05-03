# Desktop Architecture and Repository Structure

Last reviewed: 2026-04-23

## Purpose

This document explains how the desktop frontend is organized and how it should evolve without becoming a tangle of feature-specific glue.

## High-level structure

```text
azul_desktop/
|- src/app/
|- src/components/
|- src/features/
|- src/lib/
|- resources/
|- src/styles/
`- src-tauri/
```

## Responsibilities by folder

### `src/app/`

Owns the top-level shell, navigation, boot flow, and view composition.

### `src/components/`

Contains shared UI pieces reused across features.

### `src/features/`

Contains product surfaces grouped by domain such as chat, memory, workspace, and heartbeats.

The settings surface now also includes desktop diagnostics for the local backend,
including reachability, enabled model profile counts, runtime paths, and recent
launcher/MCP logs.

### `src/lib/`

Contains API access, shared contracts, fallback data, and small utilities.

This folder also defines desktop-only contracts such as backend diagnostics
status payloads consumed by Settings.

### `resources/`

Contains packaged runtime assets that are bundled into desktop installers.
For Windows builds this includes the generated `azul-backend` and
`azul-hands-mcp` executables produced by the packaging scripts.

### `src/styles/`

Contains the global design system expressed in CSS.

### `src-tauri/`

Contains the native wrapper and build metadata.

Current responsibilities include:

- starting the local backend automatically when the native shell opens
- reusing an existing backend on `localhost:3978` when one is already running
- resolving bundled backend resources in installed builds
- wiring NSIS packaging, installer metadata, and desktop shortcut creation
- stopping the spawned backend child process when the desktop app exits

## Data flow

```text
Feature shell
   |
   v
`src/lib/api.ts`
   |
   v
Local backend endpoints
```

The frontend should not duplicate backend business rules. It should display state, collect intent, and submit API requests.

In installed desktop builds the native wrapper is also responsible for making
the backend process lifecycle mostly invisible to the user: one desktop icon,
one app launch, local backend started in the background.
