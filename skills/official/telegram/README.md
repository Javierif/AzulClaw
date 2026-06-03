# Telegram Skill

First-party channel connector for Telegram.

The Azure Function relay code lives in `src/relay_function/`. Terraform for
deploying the relay belongs under `infra/terraform/`.

## Runtime integration

When this skill is installed, configured, and enabled, AzulClaw uses its
Marketplace configuration as the source of truth for the local relay runtime.
The enabled skill supplies:

- Bot Framework credentials: `MicrosoftAppId`, `MicrosoftAppPassword`, and
  optional `MicrosoftAppTenantId`.
- Service Bus transport: `SERVICE_BUS_CONNECTION_STRING`,
  `SERVICE_BUS_INBOUND_QUEUE`, `SERVICE_BUS_OUTBOUND_QUEUE`,
  `SERVICE_BUS_USE_SESSIONS`, and `BOT_SYNC_REPLY_TIMEOUT_SECONDS`.
- Telegram allowlists: `TELEGRAM_ALLOWED_USER_IDS` and
  `TELEGRAM_ALLOWED_CHAT_IDS`.

These values override the legacy environment variables and the backend reloads
the local Bot Framework adapter and Service Bus worker when the skill is saved
or enabled. The same values still need to be configured as Azure Function app
settings for the deployed relay.

The Telegram bot token belongs to the Azure Bot Service Telegram channel setup.
AzulClaw's local runtime and relay Function do not need that token once the
channel is already configured in Azure.

Inbound Telegram messages are mapped into AzulClaw's multi-conversation memory
model by Bot Framework channel metadata. Each Telegram chat gets a stable local
conversation such as `Telegram: Support (123456789)`, so separate chats no
longer share the same history merely because they came from the same channel.
If the Telegram chat title changes, AzulClaw keeps the same local conversation
and updates only the visible title.
