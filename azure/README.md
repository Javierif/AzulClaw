# Azure Relay

`azure/` contains the cloud-side relay used when AzulClaw needs public channel connectivity without exposing the local runtime directly.

## What lives here

```text
azure/
`- functions/
   `- bot_relay/
      |- function_app.py
      |- access_control.py
      |- local.settings.example.json
      `- requirements.txt
```

## Current role

The relay:

- accepts Bot Framework traffic at `POST /api/messages`
- validates Bot Framework auth when enabled
- applies early Telegram allowlists
- pushes inbound activities to Azure Service Bus
- optionally waits for a synchronous reply on the outbound queue

The health endpoint is `GET /health`.

## Related documentation

- [Azure Bot Architecture](../docs/12_azure_bot_architecture.md)
- [Azure Bot Deployment Guide](../docs/13_azure_bot_deployment_guide.md)
- [Channels and Transport](../docs/14_channels_and_transport.md)
