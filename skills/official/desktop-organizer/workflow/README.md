# Folder Organizer Workflow

This workflow is the reference marketplace skill flow for AzulClaw.

The skill owns the planning behavior for Folder Organizer. AzulClaw core owns
permissions, tool mediation, human approval lifecycle, checkpoint persistence,
and UI rendering.

Expected flow:

1. The marketplace router selects this workflow before the worker starts.
2. Run `preview_folder_organization` through the core MCP tool broker.
3. Build an `organization_plan` from the real preview payload.
4. Complete with the plan when no executable moves are available.
5. Emit a `HumanApprovalRequest` only when the prior `organization_plan` is executable.
6. Resume after `HumanApprovalResponse`.
7. Run `organize_target_folder` only from the approved `organization_plan`.
8. Summarize the concrete tool result.

Execution is gated by the plan phase. The approval payload must contain an
executable `organization_plan`; otherwise the workflow refuses to execute.
