# Desktop Architecture and Repository Structure

Last reviewed: 2026-04-15

## Purpose

This document explains how the desktop frontend is organized and how it should evolve without becoming a tangle of feature-specific glue.

## High-level structure

```text
azul_desktop/
|- src/app/
|- src/components/
|- src/features/
|- src/lib/
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

### `src/lib/`

Contains API access, shared contracts, fallback data, and small utilities.

### `src/styles/`

Contains the global design system expressed in CSS.

### `src-tauri/`

Contains the native wrapper and build metadata.

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
