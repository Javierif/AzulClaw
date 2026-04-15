# Security Model

Last reviewed: 2026-04-15

## Security posture

AzulClaw is a local AI product. That changes the threat model: a failure is not just a bad answer, it can become unwanted access to a user's machine or files. The architecture therefore separates reasoning from execution.

## Core boundary

```text
Reasoning runtime (`azul_brain`)
   |
   | JSON-RPC over stdio
   v
Filesystem tool runtime (`azul_hands_mcp`)
   |
   v
Approved workspace root only
```

The reasoning layer should not assume it can touch the filesystem directly. Sensitive local operations must go through the MCP server and the path validator.

## Main controls

### Workspace confinement

- All file paths are resolved relative to a configured workspace root unless explicitly absolute.
- Resolved targets must stay inside that root after canonicalization.
- This blocks classic path traversal such as `../../..`.

### Process separation

- The desktop UI is not trusted to enforce security rules.
- The backend decides whether a tool can be called.
- The MCP server performs the final path validation before disk access.

### Local data by default

- Memory is stored in local SQLite files.
- The default workspace scaffold stays on the user's machine.
- Public channels are optional and use an Azure relay instead of a direct local webhook.

### Defense in depth for channels

- Bot Framework auth can be enforced in the Azure Function relay.
- Telegram allowlists can be applied both in Azure and again locally.
- The local runtime does not require public inbound exposure when the relay architecture is used.

## Threat catalog

| Threat | Example | Primary mitigation |
|---|---|---|
| Path traversal | Reading `../../Windows/System32` | Canonical path validation in `path_validator.py` |
| Prompt injection leading to unsafe tool use | User content tries to escape the sandbox | Tool isolation and backend-controlled execution |
| Public webhook exposure | Exposing the local runtime to the internet | Azure relay plus outbound Service Bus worker |
| Unauthorized Telegram access | Messages from unknown users or chats | Allowlist checks in Function and local runtime |
| Local destructive actions | Deleting memory or resetting state unintentionally | Explicit confirmation flows in desktop settings |

## Operational rules

- Never bypass the MCP boundary for workspace file operations.
- Do not commit `.env.local`, local settings, or credentials.
- Treat `memory/` as runtime state, not source code.
- Prefer additive permissions and explicit approval flows over hidden power.

## Related documents

- [Architecture Overview](01_architecture.md)
- [Channels and Transport](14_channels_and_transport.md)
