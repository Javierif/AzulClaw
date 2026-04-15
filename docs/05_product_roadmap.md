# Product Roadmap

Last reviewed: 2026-04-15

## Current state

AzulClaw already ships the foundations of a usable local agent:

- desktop shell with onboarding and multiple product surfaces
- local Python runtime with streaming chat
- persistent memory in SQLite
- unified heartbeats and scheduled jobs
- secure workspace access through MCP
- optional Azure relay for channels

## Near-term priorities

### 1. Production hardening

- stronger retry and circuit-breaker behavior around tool use
- clearer operational diagnostics and logs
- improved failure states in the desktop shell

### 2. Cognitive quality

- better progress narration for slow-lane work
- more deliberate memory extraction and retrieval tuning
- clearer boundaries between ephemeral context and durable memory

### 3. Desktop maturity

- deeper runtime controls
- richer skills and integration management
- more polished multi-session and process visibility

## Medium-term priorities

- packaging and installer polish for desktop distribution
- broader channel support with the Azure relay pattern
- stronger observability and support tooling
- safer approval workflows for sensitive actions

## Longer-term direction

- richer agent autonomy without compromising workspace confinement
- clearer permission surfaces for new tools and integrations
- more structured project and document workflows inside the sandbox
