# Workspace

The workspace feature lets the user inspect AzulClaw's sandbox root.

- `WorkspaceShell.tsx` reads the listing exposed by `/api/desktop/workspace`.
- The desktop app should present the sandbox clearly without implying unrestricted system access.
- The backend remains responsible for all validation and file access rules.

Related docs: [Security Model](../../../../docs/03_security_model.md).
