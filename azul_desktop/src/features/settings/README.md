# Settings

Settings contains local device and profile controls.

- `SettingsShell.tsx` surfaces actions such as data wipe and local reset flows.
- Sensitive actions require explicit confirmation because they affect on-disk memory and onboarding state.
- Keep destructive actions consistent with the backend API contract.
