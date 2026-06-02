# Remote Agent Skill Template

Use this template when AzulClaw should call a remote corporate agent or API.

The business logic lives in the remote service. The skill bundle declares the
endpoint, authentication expectations, configuration schema, and prompt
contribution.

By default the template expects an API key configured from the AzulClaw
Marketplace UI and sent to the remote endpoint through the `x-api-key` header.
