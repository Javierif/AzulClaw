# Azure Bot + Function + Service Bus Deployment Guide

This guide documents the full deployment flow required for anyone to publish their own AzulClaw instance in Azure and connect it to Azure Bot Service and Alexa using a secure Azure Function + Azure Service Bus architecture.

## Goal

The final flow described here is:

```text
Alexa -> Azure Bot Service -> Azure Function -> Service Bus -> local AzulClaw
local AzulClaw -> Service Bus -> Azure Function -> Azure Bot Service -> Alexa
```

The key design principle is that AzulClaw does not expose a local public endpoint to the internet. The only public entry point is the Azure Function.

## Required Azure resources

You need to create these resources:

1. A Resource Group.
2. A Storage Account for Azure Functions.
3. An Azure Function App.
4. An Azure Service Bus namespace.
5. Two Service Bus queues:
   - `bot-inbound`
   - `bot-outbound`
6. An Azure Bot.
7. An Alexa skill connected to the Alexa channel of the Azure Bot.

## Prerequisites

- An Azure subscription.
- An Amazon Developer account for Alexa.
- A working local checkout of AzulClaw.
- Python and the required dependencies installed for the local runtime.
- Access to configure Azure secrets:
  - `MicrosoftAppId`
  - `MicrosoftAppPassword`
  - `MicrosoftAppTenantId` when applicable
  - `SERVICE_BUS_CONNECTION_STRING`

## 1. Create Azure Service Bus

1. Create an Azure Service Bus namespace.
2. Prefer the `Standard` tier.
3. Create the `bot-inbound` queue.
4. Create the `bot-outbound` queue.
5. If you use `Standard`, enable sessions on `bot-outbound`.

Notes:

- The local worker uses `correlation_id` to match each request with its reply.
- The current deployment uses sessions only on `bot-outbound`.
- Keep `bot-inbound` without sessions because the local worker consumes it with a non-session receiver.
- If you use sessions, keep `SERVICE_BUS_USE_SESSIONS=true` or `auto`.

Official reference:
- Azure Service Bus sessions: https://learn.microsoft.com/en-us/azure/service-bus-messaging/message-sessions

## 2. Create the Azure Function App

### Recommended option: Flex Consumption

If you use Flex Consumption:

- The runtime is chosen when the Function App is created.
- Do not try to fix it later by setting `FUNCTIONS_WORKER_RUNTIME` in Application Settings.
- Create the app directly with the `Python` stack.

Official reference:
- Flex Consumption: https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-how-to

Recommended configuration when creating the app:

- Hosting: `Flex Consumption`
- Runtime stack: `Python`
- Runtime version: a Python version currently supported by Azure Functions
- Application Insights: enabled
- Storage account: dedicated or isolated from unrelated workloads

## 3. Deploy the Azure Function

The Function project to deploy is located at:

```text
azure/functions/bot_relay
```

Key files:

- `function_app.py`
- `host.json`
- `requirements.txt`
- `local.settings.example.json`

Important:

- Deploy from that folder, not from the repository root.
- If you publish through the Azure Functions extension in VS Code, confirm that the selected project is `azure/functions/bot_relay`.
- If you use Flex Consumption, wait until deployment fully completes before validating routes.

## 4. Configure Application Settings in Azure Function

Configure these variables in `Function App > Application Settings`:

```text
SERVICE_BUS_CONNECTION_STRING
SERVICE_BUS_INBOUND_QUEUE=bot-inbound
SERVICE_BUS_OUTBOUND_QUEUE=bot-outbound
SERVICE_BUS_USE_SESSIONS=auto
BOT_RELAY_REQUIRE_AUTH=true
BOT_SYNC_REPLY_TIMEOUT_SECONDS=6.8
MicrosoftAppId
MicrosoftAppPassword
MicrosoftAppTenantId
```

Notes:

- `BOT_SYNC_REPLY_TIMEOUT_SECONDS` defines how long the Function waits for a reply from the local worker before falling back.
- `6.8` seconds leaves room inside Alexa's skill timeout window.
- `BOT_RELAY_REQUIRE_AUTH=true` makes the relay reject requests without a valid Bot Framework bearer token.

Safe local example:

- Use `azure/functions/bot_relay/local.settings.example.json` as the template.
- Do not commit the real `local.settings.json`.

## 5. Validate that the Function is published

Minimum validation:

1. `Deployment Center` shows the deployment as completed.
2. The `GET /health` endpoint returns `200`.
3. The `POST /api/messages` endpoint returns `401/403` without Bot Framework auth and succeeds only for authenticated Bot traffic.

With the current code, the expected public routes are:

```text
GET  /health
POST /api/messages
```

Examples:

```text
https://<your-app>.azurewebsites.net/health
https://<your-app>.azurewebsites.net/api/messages
```

If `/health` returns `404`, the Function is not published or has not been indexed correctly.

If `/api/messages` returns `404`, Azure Bot Service will not be able to talk to your relay.

If `/api/messages` returns `401` or `403` from Azure Bot Service, verify `MicrosoftAppId`, `MicrosoftAppPassword`, `MicrosoftAppTenantId`, and that the Bot is calling the exact configured endpoint.

## 6. Configure Azure Bot Service

In your Azure Bot:

1. Open the bot configuration.
2. Set the `Messaging Endpoint` to exactly:

```text
https://<your-function-app>.azurewebsites.net/api/messages
```

Do not use `/health` for the bot endpoint.

## 7. Configure the Alexa channel

Alexa configuration has two parts.

### In Azure Bot Service

1. Open the Azure Bot resource.
2. Go to `Channels`.
3. Select `Alexa`.
4. Enter the `Skill ID` of your Alexa skill.
5. Save and copy the `Alexa service endpoint URI` generated by Azure.

### In the Alexa Developer Console

1. Create a `Custom` skill.
2. Use `Provision your own` for the backend.
3. In `Endpoint`, paste the `Alexa service endpoint URI` generated by Azure Bot Service.
4. In the interaction model, define:
   - the `invocationName`
   - sample utterances
   - an intent that captures free user input

Official reference:
- Connect a bot to Alexa: https://learn.microsoft.com/en-us/azure/bot-service/bot-service-channel-connect-alexa?view=azure-bot-service-4.0

Important:

- The URL pasted into Alexa is not the Azure Function URL.
- The Alexa skill points to the endpoint generated by Azure Bot Service.
- The Azure Function is only configured as the Azure Bot `Messaging Endpoint`.

## 8. Configure the local AzulClaw runtime

The local AzulClaw host must be able to consume `bot-inbound` and publish to `bot-outbound`.

Required environment variables in the local AzulClaw runtime:

```text
SERVICE_BUS_CONNECTION_STRING
SERVICE_BUS_INBOUND_QUEUE=bot-inbound
SERVICE_BUS_OUTBOUND_QUEUE=bot-outbound
SERVICE_BUS_USE_SESSIONS=auto
MicrosoftAppId
MicrosoftAppPassword
MicrosoftAppTenantId
```

Then start the backend:

```bash
python -m azul_backend.azul_brain.main_launcher
```

When everything is connected correctly, you should see logs similar to:

```text
[Worker] Service Bus inbound connection ESTABLISHED.
[Worker] Processing channel activity ...
[Worker] Sync reply queued on bot-outbound ...
```

## 9. Expected conversational behavior

### Fast cases

If the `fast brain` resolves the request inside the synchronous reply window:

- Azure Function waits for the reply in `bot-outbound`
- returns that reply to Azure Bot Service
- Alexa speaks the full answer

### Slow cases

If the response takes too long:

- Azure Function falls back to a short response
- Alexa says something brief to avoid timing out
- full processing can continue in the background

Official reference:
- Alexa progressive responses and timing: https://developer.amazon.com/es-ES/docs/alexa/custom-skills/progressive-response-api-reference.html

## 10. Troubleshooting

### `Functions` is empty in the Azure portal

Possible causes:

- incomplete deployment
- wrong runtime selected when the Function App was created
- deployment published from the wrong folder
- startup or indexing failure

### `/health` returns `404`

Possible causes:

- deployment is still in progress
- the published app is not the correct app
- the Function has not been indexed

### `/api/messages` returns `404`

Possible causes:

- `Messaging Endpoint` is misconfigured
- an old code version is still deployed
- the Function was not published correctly

### Alexa always says a fallback phrase such as "Vale, lo miro ahora mismo"

That means the relay did not receive a reply from `bot-outbound` in time.

Check:

- the local worker is running
- `bot-outbound` exists
- sessions are enabled only on `bot-outbound`
- there are no Service Bus errors in the logs

### The local worker receives nothing

Check:

- `SERVICE_BUS_CONNECTION_STRING`
- correct `bot-inbound` queue name
- outbound connectivity from the local machine to Azure Service Bus

### Azure Bot Service responds but Alexa does not speak

Check:

- correct Skill ID in the Alexa channel
- correct Azure Bot endpoint configured in the Alexa skill
- published Alexa interaction model
- whether the relay reply is arriving inside the synchronous time window

## 11. Final checklist

Before considering deployment complete, confirm all of the following:

- `GET /health` returns `200`
- `POST /api/messages` returns `200`
- unauthenticated `POST /api/messages` requests are rejected
- Azure Bot has the correct `Messaging Endpoint`
- the Alexa channel is configured with the correct Skill ID
- the Alexa skill points to the endpoint generated by Azure Bot
- the local runtime consumes `bot-inbound`
- the local runtime publishes to `bot-outbound`
- the user hears a real `fast brain` answer for simple questions

## Related files in this repository

- `azure/functions/bot_relay/function_app.py`
- `azure/functions/bot_relay/host.json`
- `azure/functions/bot_relay/requirements.txt`
- `azure/functions/bot_relay/local.settings.example.json`
- `azure/README.md`
- `docs/12_azure_bot_architecture.md`
- `azul_backend/azul_brain/channels/servicebus_worker.py`
- `azul_backend/azul_brain/main_launcher.py`
