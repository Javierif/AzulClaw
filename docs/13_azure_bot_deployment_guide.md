# Azure Bot Deployment Guide

Last reviewed: 2026-06-02

## Scope

This guide covers the Azure relay path for public channels:

```text
Channel -> Azure Bot Service -> Azure Function -> Azure Service Bus -> Local AzulClaw
```

The deployable first-party connector in this repository is Telegram. The relay
uses Bot Framework activities, so the same transport pattern can be reused for
other channels after their channel-specific connector and configuration exist.

## Prerequisites

- Azure subscription
- local checkout of this repository
- backend dependencies installed
- Azure Bot credentials
- Azure Service Bus connection string

## Required Azure resources

1. Resource group
2. Storage account for Functions
3. Function App
4. Service Bus namespace
5. Queue `bot-inbound`
6. Queue `bot-outbound`
7. Azure Bot resource

## Configure the Function

Deploy the contents of `skills/official/telegram/src/relay_function/`.

Terraform for this channel connector belongs under
`skills/official/telegram/infra/terraform/`.

Important settings:

```text
SERVICE_BUS_CONNECTION_STRING
SERVICE_BUS_INBOUND_QUEUE=bot-inbound
SERVICE_BUS_OUTBOUND_QUEUE=bot-outbound
SERVICE_BUS_USE_SESSIONS=auto
BOT_SYNC_REPLY_TIMEOUT_SECONDS=6.8
BOT_RELAY_REQUIRE_AUTH=true
MicrosoftAppId
MicrosoftAppPassword
TELEGRAM_ALLOWED_USER_IDS
TELEGRAM_ALLOWED_CHAT_IDS
```

## Configure the local runtime

For managed installed desktops, store the Service Bus and channel settings in
Key Vault and configure the local machine with `AZUL_KEY_VAULT_URL`.

For local development only, the same values can be set in
`azul_backend/azul_brain/.env.local`.

## Validate the deployment

1. Confirm `GET /health` returns `200`.
2. Confirm Bot Framework can reach `POST /api/messages`.
3. Confirm inbound activities land in `bot-inbound`.
4. Start the local backend and verify the Service Bus worker connects.
5. Send a channel message and confirm a reply returns end to end.
