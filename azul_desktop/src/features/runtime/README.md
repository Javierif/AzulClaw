# Runtime

The runtime feature presents editable local runtime configuration.

- `RuntimeShell.tsx` reads and writes aggregated runtime state.
- Typical concerns include default lane selection, enabled model profiles, and scheduler overview.
- Changes are persisted by the backend in the local `memory/` state folder.
