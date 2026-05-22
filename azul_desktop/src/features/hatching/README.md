# Hatching

This feature contains the first-run Hatching ritual where the user defines AzulClaw's initial profile.

- Collects identity, personality, workspace, and first-run preferences.
- Includes the full Azure onboarding flow used by both first run and later reconfiguration from Settings.
- Supports guided Microsoft sign-in with tenant, subscription, resource, deployment, and Key Vault discovery.
- Supports a manual fallback mode for direct Azure OpenAI API key configuration when secure desktop storage is available.
- Persists the selected Azure endpoint, deployments, auth mode, and optional Key Vault secret mappings into the local profile.
- Persists profile data through the desktop setup profile endpoint.
- Acts as a gate before the user can access the main desktop experience.
