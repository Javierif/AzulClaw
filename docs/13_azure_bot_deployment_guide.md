# Azure Bot + Function + Service Bus Deployment Guide

This guide documents the end-to-end deployment flow for publishing **AzulClaw** behind **Azure Bot Service** using the recommended relay architecture:

```text
Channel -> Azure Bot Service -> Azure Function -> Azure Service Bus -> Local AzulClaw
Local AzulClaw -> Azure Service Bus -> Azure Function -> Azure Bot Service -> Channel
```

This guide is procedural.

For the architecture and transport rationale behind the design, read [Azure Bot Connectivity Architecture](12_azure_bot_architecture.md) and [Channels, Transport, and Message Delivery](14_channels_and_transport.md).

---

## 1. What this deployment gives you

With this setup:

- Azure Bot Service remains the channel-facing integration layer
- Azure Function becomes the public Bot Framework endpoint
- Azure Service Bus becomes the durable transport layer
- the local AzulClaw runtime stays private and consumes messages over outbound broker connections

That is the intended production-style model for AzulClaw channel delivery.

---

## 2. Prerequisites

You need:

- an Azure subscription
- a local checkout of AzulClaw
- Python and the backend dependencies installed for the local runtime
- permission to create Azure resources
- Bot Framework credentials:
  - `MicrosoftAppId`
  - `MicrosoftAppPassword`
- an Azure Service Bus connection string

If you plan to use Alexa:

- an Amazon Developer account
- an Alexa custom skill

If you plan to lock Telegram down to your own account:

- your Telegram numeric user ID
- optionally one or more Telegram conversation IDs

---

## 3. Azure resources you need

Create or provision the following Azure resources:

1. A Resource Group
2. A Storage Account for Azure Functions
3. An Azure Function App
4. An Azure Service Bus namespace
5. Two Service Bus queues:
   - `bot-inbound`
   - `bot-outbound`
6. An Azure Bot
7. Optionally, an Alexa skill connected to the Azure Bot Alexa channel

---

## 4. Prepare Azure Service Bus

Create the Service Bus namespace first because both the Function and the local runtime depend on it.

### Queue layout

| Queue | Role | Session requirement |
|---|---|---|
| `bot-inbound` | Carries inbound Bot Framework activities from the Function to the local worker | No |
| `bot-outbound` | Carries correlated synchronous replies from the local worker back to the Function | Optional overall, but required for the isolated synchronous request/reply mode |

### Recommended setup

1. Create the Service Bus namespace.
2. Prefer the **Standard** tier.
3. Create `bot-inbound`.
4. Create `bot-outbound`.
5. Enable **sessions** on `bot-outbound` if you want the isolated synchronous reply mode.
6. Keep `bot-inbound` as a non-session queue.

### Why the queues are different

- `bot-inbound` is consumed in parallel by the local worker with a non-session receiver.
- if sessions are enabled, `bot-outbound` uses `correlation_id` plus session-based isolation so the Function can wait for the correct reply for a specific request.
- if sessions are disabled, the system can still operate, but the isolated synchronous reply path is not available.
- channels such as Telegram can still work normally in that mode because they use the proactive reply path rather than depending on an inline correlated HTTP response.

Official reference:

- Azure Service Bus sessions: https://learn.microsoft.com/en-us/azure/service-bus-messaging/message-sessions

---

## 5. Create the Azure Function App

The Function App is the public Bot Framework relay. Azure Bot Service should point to it, not to the local machine.

### Recommended hosting option

Use **Flex Consumption** or another Azure Functions hosting option that supports the Python runtime you need.

Recommended settings when creating the app:

- Runtime stack: `Python`
- Runtime version: a Python version currently supported by Azure Functions
- Application Insights: enabled
- Storage Account: dedicated to the Function workload where practical

### Important deployment note

The Function project in this repository is:

```text
azure/functions/bot_relay
```

Deploy from that folder, not from the repository root.

Key files in that project:

- `function_app.py`
- `host.json`
- `requirements.txt`
- `local.settings.example.json`

---

## 6. Configure Azure Function application settings

Set these variables in **Function App -> Configuration -> Application settings**:

```text
SERVICE_BUS_CONNECTION_STRING
SERVICE_BUS_INBOUND_QUEUE=bot-inbound
SERVICE_BUS_OUTBOUND_QUEUE=bot-outbound
SERVICE_BUS_USE_SESSIONS=auto
BOT_RELAY_REQUIRE_AUTH=true
BOT_SYNC_REPLY_TIMEOUT_SECONDS=6.8
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_ALLOWED_CHAT_IDS=
MicrosoftAppId
MicrosoftAppPassword
```

### What each important setting means

| Variable | Purpose |
|---|---|
| `SERVICE_BUS_CONNECTION_STRING` | Gives the Function access to the Service Bus namespace |
| `SERVICE_BUS_INBOUND_QUEUE` | Queue used for inbound activities |
| `SERVICE_BUS_OUTBOUND_QUEUE` | Queue used for correlated synchronous replies |
| `SERVICE_BUS_USE_SESSIONS` | Controls whether the isolated session-based sync reply path is used. `true` forces it, `false` disables it, and `auto` starts with session-based sync replies but disables that path automatically if the queue rejects session operations. |
| `BOT_RELAY_REQUIRE_AUTH` | When `true`, the Function requires valid Bot Framework authentication |
| `BOT_SYNC_REPLY_TIMEOUT_SECONDS` | Maximum time the Function waits for a synchronous reply before returning a fallback response |
| `TELEGRAM_ALLOWED_USER_IDS` | Optional Telegram sender allowlist applied before queueing |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Optional Telegram conversation allowlist applied before queueing |

### Notes

- `BOT_SYNC_REPLY_TIMEOUT_SECONDS=6.8` is chosen to leave headroom inside stricter voice-channel reply windows.
- `SERVICE_BUS_USE_SESSIONS=false` is valid if you do not need the isolated synchronous reply path.
- `SERVICE_BUS_USE_SESSIONS=auto` is a good default when you want the system to attempt the isolated sync path first and then fall back cleanly if the queue is not session-enabled.
- a non-session deployment is fully acceptable for Telegram-centric setups and other channel mixes that rely on proactive replies.
- `MicrosoftAppTenantId` is not required by the Function's Bot Framework auth path in the current implementation.
- If you want Telegram fully restricted to your own account, set `TELEGRAM_ALLOWED_USER_IDS` here and set the same value again in the local AzulClaw runtime.

### Local testing

For local Function testing:

- use `azure/functions/bot_relay/local.settings.example.json` as the template
- do not commit the real `local.settings.json`

---

## 7. Validate that the Azure Function is published

Before connecting Azure Bot Service, confirm the Function really exists and is routable.

### Minimum validation

1. Deployment Center shows the deployment as complete.
2. `GET /health` returns `200`.
3. `POST /api/messages` returns `401` or `403` without Bot Framework auth when `BOT_RELAY_REQUIRE_AUTH=true`.

### Expected public routes

```text
GET  /health
POST /api/messages
```

Examples:

```text
https://<your-function-app>.azurewebsites.net/health
https://<your-function-app>.azurewebsites.net/api/messages
```

### Interpretation of common failures

If `/health` returns `404`:

- deployment may still be in progress
- the wrong project may have been published
- function indexing may have failed

If `/api/messages` returns `404`:

- Azure Bot Service will not be able to reach the relay
- the wrong code version may be deployed
- the publish path may have been incorrect

If `/api/messages` returns `401` or `403` from Azure Bot Service:

- verify `MicrosoftAppId`
- verify `MicrosoftAppPassword`
- verify that the bot is calling the exact configured Function URL

---

## 8. Configure Azure Bot Service

In your Azure Bot resource:

1. Open the bot configuration.
2. Set the **Messaging Endpoint** to:

```text
https://<your-function-app>.azurewebsites.net/api/messages
```

Do not use `/health` as the bot endpoint.

This configuration makes Azure Bot Service send Bot Framework activities to the Function relay rather than directly to AzulClaw.

---

## 9. Configure the Alexa channel

Alexa is configured in two places.

### In Azure Bot Service

1. Open the Azure Bot resource.
2. Go to `Channels`.
3. Select `Alexa`.
4. Enter the Skill ID of your Alexa skill.
5. Save the configuration.
6. Copy the Alexa service endpoint URI generated by Azure.

### In the Alexa Developer Console

1. Create a `Custom` skill.
2. Use `Provision your own` for the backend.
3. In the skill endpoint, paste the **Azure Bot-generated Alexa endpoint**, not the Function URL.
4. Define:
   - the invocation name
   - sample utterances
   - an intent that captures free user input

Official reference:

- Connect a bot to Alexa: https://learn.microsoft.com/en-us/azure/bot-service/bot-service-channel-connect-alexa?view=azure-bot-service-4.0

### Important distinction

- Alexa talks to the endpoint generated by Azure Bot Service.
- Azure Bot Service talks to the Azure Function.
- The Azure Function talks to Azure Service Bus.

Those are three separate integration boundaries.

---

## 10. Configure the local AzulClaw runtime

The local runtime must be able to:

- consume `bot-inbound`
- publish synchronous replies to `bot-outbound`
- send proactive Bot Framework replies when appropriate

Set these environment variables in the local AzulClaw runtime:

```text
SERVICE_BUS_CONNECTION_STRING
SERVICE_BUS_INBOUND_QUEUE=bot-inbound
SERVICE_BUS_OUTBOUND_QUEUE=bot-outbound
SERVICE_BUS_USE_SESSIONS=auto
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_ALLOWED_CHAT_IDS=
MicrosoftAppId
MicrosoftAppPassword
MicrosoftAppTenantId
```

### Telegram lockdown note

If you want only your Telegram account to reach AzulClaw:

- set `TELEGRAM_ALLOWED_USER_IDS` in the Azure Function
- set the same `TELEGRAM_ALLOWED_USER_IDS` in the local runtime

That gives you early rejection in Azure plus local defense in depth.

### Start the backend

```bash
python -m azul_backend.azul_brain.main_launcher
```

### Expected runtime signals

When the worker is connected correctly, you should see logs similar to:

```text
[Worker] Service Bus inbound connection ESTABLISHED.
[Worker] Processing channel activity ...
[Worker] Sync reply queued on bot-outbound ...
```

---

## 11. Expected runtime behavior

### Fast-path requests

If the request is handled inside the synchronous reply window:

- the local worker processes the activity
- the worker puts a correlated reply on `bot-outbound`
- the Function returns that reply to Azure Bot Service
- the channel receives an inline answer

### Slow-path requests

If the request exceeds the synchronous wait window:

- the Function returns a fallback response
- the channel avoids timing out
- the local runtime may continue processing and deliver a later response through the proactive path where appropriate

This behavior is particularly important for voice-first integrations such as Alexa.

---

## 12. End-to-end validation checklist

Before considering the deployment healthy, confirm all of the following:

- `GET /health` returns `200`
- unauthenticated `POST /api/messages` requests are rejected when auth is enabled
- Azure Bot has the correct `Messaging Endpoint`
- `bot-inbound` exists and is reachable
- `bot-outbound` exists and, if you want isolated synchronous replies, has sessions enabled
- the local runtime consumes `bot-inbound`
- the local runtime can publish to `bot-outbound`
- simple channel requests receive a real inline answer
- Telegram allowlists behave as expected if configured

---

## 13. Troubleshooting

### The Azure portal shows no Functions

Possible causes:

- incomplete deployment
- wrong runtime selected when the Function App was created
- deployment published from the wrong folder
- startup or indexing failure

### `/health` returns `404`

Possible causes:

- deployment is still in progress
- the wrong project was deployed
- function indexing failed

### `/api/messages` returns `404`

Possible causes:

- the Function was not published correctly
- the bot is pointing at the wrong URL
- an older code version is still deployed

### `/api/messages` returns `401` or `403`

Possible causes:

- `MicrosoftAppId` is wrong
- `MicrosoftAppPassword` is wrong
- Azure Bot Service is calling a different endpoint than the one you configured

### The local worker receives nothing

Check:

- `SERVICE_BUS_CONNECTION_STRING`
- correct queue names
- outbound connectivity from the local host to Azure Service Bus

### The channel only hears fallback phrases

That usually means the Function did not receive a synchronous reply from `bot-outbound` before timeout.

Check:

- the local worker is running
- `bot-outbound` exists
- if you expect isolated synchronous replies, sessions are enabled on `bot-outbound`
- `SERVICE_BUS_USE_SESSIONS` matches the mode you actually want
- there are no Service Bus errors in either the Function logs or local worker logs

### Telegram still reaches AzulClaw from another account

Check:

- `TELEGRAM_ALLOWED_USER_IDS` is configured in the Function
- the same allowlist is configured in the local runtime
- the sender ID is really the numeric Telegram user ID you expect

---

## 14. Files involved in this deployment path

These are the key files in the repository for this deployment:

- `azure/functions/bot_relay/function_app.py`
- `azure/functions/bot_relay/host.json`
- `azure/functions/bot_relay/requirements.txt`
- `azure/functions/bot_relay/local.settings.example.json`
- `azure/README.md`
- `docs/12_azure_bot_architecture.md`
- `docs/14_channels_and_transport.md`
- `azul_backend/azul_brain/channels/servicebus_worker.py`
- `azul_backend/azul_brain/channels/access_control.py`
- `azul_backend/azul_brain/main_launcher.py`
