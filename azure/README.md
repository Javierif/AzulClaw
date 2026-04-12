# Azure Cloud Resources for AzulClaw

This folder contains the Azure-side pieces used to expose AzulClaw safely to public channels such as Alexa through Azure Bot Service.

## What is here

- `functions/bot_relay`
  Public Azure Function that receives Bot Framework traffic and relays it to AzulClaw through Azure Service Bus.

## Current public routes

With the current code, the published Function exposes:

- `GET /health`
- `POST /api/messages`

Example:

- `https://<your-function-app>.azurewebsites.net/health`
- `https://<your-function-app>.azurewebsites.net/api/messages`

## Deployment guide

For the full end-to-end deployment process, read:

- [docs/13_azure_bot_deployment_guide.md](../docs/13_azure_bot_deployment_guide.md)

That guide covers:

- Azure Service Bus creation
- Azure Function App creation and deployment
- Application Settings
- Azure Bot Service configuration
- Alexa channel configuration
- Local AzulClaw runtime configuration
- Validation and troubleshooting
