# App

This folder contains the top-level desktop shell.

- `DesktopApp.tsx` wires navigation, bootstrapping, top-bar state, and view rendering.
- Primary product navigation now exposes `Context` as the shared operational surface for processes, memory, and workspace.
- Global application flow starts here: load the setup profile, decide whether setup is required, then mount the main product views.

Related docs: [Desktop Architecture and Repository Structure](../../../docs/10_desktop_architecture_and_repo_structure.md).
