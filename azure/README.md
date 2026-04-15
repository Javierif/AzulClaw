# Azure Cloud Resources for AzulClaw

This folder contains the Azure-side pieces used to expose AzulClaw safely to public channels such as Alexa through Azure Bot Service.

## What is here

- `functions/bot_relay`
  Public Azure Function that validates Bot Framework traffic and relays it to AzulClaw through Azure Service Bus.

## Current public routes

With the current code, the published Function exposes:

- `GET /health`
- `POST /api/messages`

Example:

- `https://<your-function-app>.azurewebsites.net/health`
- `https://<your-function-app>.azurewebsites.net/api/messages`

## Security and Service Bus notes

- `POST /api/messages` expects a valid Bot Framework `Authorization` header before any payload is enqueued.
- For local-only troubleshooting, auth can be disabled explicitly with `BOT_RELAY_REQUIRE_AUTH=false`.
- To restrict Telegram to your own account, set `TELEGRAM_ALLOWED_USER_IDS` in both the Azure Function and the local AzulClaw runtime.
- `TELEGRAM_ALLOWED_CHAT_IDS` is optional defense-in-depth if you also want to pin accepted Telegram conversation IDs.
- Synchronous request/reply requires sessions on `bot-outbound`; keep `SERVICE_BUS_USE_SESSIONS=true`.
- Keep `bot-inbound` without sessions because the local worker consumes it with a non-session receiver.

## Deployment guide

For the full end-to-end deployment process, read:

- [docs/13_azure_bot_deployment_guide.md](../docs/13_azure_bot_deployment_guide.md)
- [docs/14_channels_and_transport.md](../docs/14_channels_and_transport.md)

That guide covers:

- Azure Service Bus creation
- Azure Function App creation and deployment
- Application Settings
- Azure Bot Service configuration
- Alexa channel configuration
- Local AzulClaw runtime configuration
- Validation and troubleshooting
