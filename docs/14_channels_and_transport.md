# Channels and Transport

Last reviewed: 2026-04-15

## Purpose

This document explains how external channel traffic reaches AzulClaw without requiring the local runtime to expose a public inbound endpoint.

## Delivery model

Current production-style path:

```text
Channel -> Azure Bot Service -> Azure Function -> Azure Service Bus -> Local AzulClaw
```

## Transport layers

| Hop | Transport |
|---|---|
| Channel -> Azure Bot Service | Channel-specific managed transport |
| Azure Bot Service -> Azure Function | HTTPS / Bot Framework activity POST |
| Azure Function -> Azure Service Bus | Azure SDK over AMQP-backed messaging |
| Local worker -> Azure Service Bus | Outbound broker connection |

## Queue roles

| Queue | Purpose |
|---|---|
| `bot-inbound` | inbound activities for the local worker |
| `bot-outbound` | correlated replies back to the Function |

## Authorization points

- Bot Framework auth in the Azure Function when enabled
- Telegram user and chat allowlists in the relay
- repeated allowlist checks in the local runtime for defense in depth
