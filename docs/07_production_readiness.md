# Production Readiness

Last reviewed: 2026-04-23

## Purpose

This document captures the reliability expectations that matter if AzulClaw is to operate beyond a demo environment.

## Key readiness areas

### Context management

Long-running conversations need bounded context usage. Desired behavior includes:

- token-aware history budgeting
- selective summarization of older turns
- graceful handling of unusually large messages

### Tool loop protection

Agentic systems need safeguards against repeated no-progress tool calls.

Recommended protections:

- repeat-call detection
- ping-pong pattern detection
- hard session breakers after repeated failures

### Retry strategy

External dependencies will fail. The runtime should differentiate between:

- transient provider failures
- invalid request shapes
- auth or credential failures
- downstream transport outages

### Observability

Minimum production visibility should include:

- lane used
- model selected
- process id
- queue correlation id for channel traffic
- scheduler execution outcomes
- backend startup status for installed desktop builds
- recent launcher and MCP stderr logs exposed without opening the install folder

### Safe degradation

The product should fail usefully:

- desktop should surface backend unavailability clearly
- Azure relay should return bounded fallback replies when synchronous delivery fails
- memory or embedding failures should degrade to simpler behavior instead of crashing the turn

## Current strengths

- local state is explicit and durable
- channel delivery can be decoupled through Service Bus
- desktop chat already exposes runtime metadata
- workspace access is isolated behind MCP
- the Windows desktop package can launch its bundled backend and MCP server without a separate console
- desktop settings now expose backend reachability, enabled model counts, runtime paths, and recent logs
- Hatching and Settings now provide a first-run Azure setup path for endpoint, deployments, Key Vault URL, and Microsoft login
- installed builds can reapply persisted non-secret provider configuration without requiring users to edit local environment files

## Current gaps

- richer loop detection is still limited
- advanced observability remains light
- some reliability patterns are documented more clearly than they are productized
- installed-build validation still needs a clean-machine pass for the NSIS installer and bundled backend resources
- Key Vault, Entra app registration, and Azure RBAC prerequisites still need clear release notes because the app cannot create those cloud resources automatically
