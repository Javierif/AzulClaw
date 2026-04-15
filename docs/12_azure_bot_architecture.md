# Azure Bot Architecture

Last reviewed: 2026-04-15

## Purpose

This document describes the current cloud relay used when AzulClaw connects to public messaging channels.

## Current topology

```text
Channel -> Azure Bot Service -> Azure Function -> Azure Service Bus -> Local AzulClaw
```

Replies follow the reverse path.

## Roles

### Azure Bot Service

- channel-facing integration layer
- normalizes external traffic into Bot Framework activities

### Azure Function relay

- public HTTP ingress
- Bot Framework authentication gate when enabled
- early Telegram allowlist enforcement
- Service Bus enqueue and optional synchronous reply handling

### Azure Service Bus

- durable decoupling layer between cloud ingress and the local runtime
- inbound queue for activities
- outbound queue for correlated replies

### Local AzulClaw worker

- consumes queued activities
- routes them into the same local orchestrator used by desktop chat
- returns replies through Service Bus when required
