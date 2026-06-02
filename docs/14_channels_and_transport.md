# Channels and Transport

Last reviewed: 2026-06-02

## Purpose

This document explains how external channel traffic reaches AzulClaw without requiring the local runtime to expose a public inbound endpoint.

Telegram is the current first-party channel connector in this repository. The
cloud relay uses Bot Framework activities, so the pattern can support other
configured Bot Framework channels when their channel-specific setup exists.

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

## First-party connector status

| Channel | Repository status |
|---|---|
| Telegram | First-party skill under `skills/official/telegram/` with relay Function and Terraform scaffolding |
| Other Bot Framework channels | Supported by the relay architecture when a channel-specific connector/configuration is provided |
