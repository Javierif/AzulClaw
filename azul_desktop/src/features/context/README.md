# Context

This feature groups operational visibility for AzulClaw into a single surface.

- `ContextShell.tsx` owns the tabbed shell and loads processes, memory, and workspace data in parallel.
- `Overview` summarizes current activity, learned memory, and sandbox scope.
- `Processes`, `Memory`, and `Workspace` are now internal tabs rather than top-level desktop views.

Related docs: [Desktop Interface Design](../../../../docs/08_desktop_interface_design.md).
