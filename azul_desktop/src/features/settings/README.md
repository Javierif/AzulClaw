# Settings

Settings contains local device and profile controls.

- `SettingsShell.tsx` is organized as a tabbed control surface: `Azure`, `Runtime`, `Memory`, `Identity`, `Security`, and `Data`.
- The `Azure` tab reuses the onboarding wizard so users can rediscover resources, switch auth mode, review deployments, and update Key Vault selections after first run.
- The `Runtime` tab exposes backend diagnostics such as reachability, scheduler state, Azure auth state, enabled model counts, runtime paths, and recent launcher logs.
- `SettingsShell.tsx` also surfaces actions such as data wipe and local reset flows.
- Sensitive actions require explicit confirmation because they affect on-disk memory and setup state.
- Keep destructive actions consistent with the backend API contract.
