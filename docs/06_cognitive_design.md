# Cognitive Design

Last reviewed: 2026-04-15

## Purpose

AzulClaw does not behave like a single monolithic chatbot. It uses a lane-based reasoning model so the product can respond quickly when possible and deliberately when necessary.

## Lane model

### Fast lane

The fast lane is intended for:

- short, low-latency answers
- triage
- commentary and lightweight progress narration
- background preference extraction
- heartbeat tasks that do not need deep reasoning

### Slow lane

The slow lane is intended for:

- heavier reasoning
- more expensive synthesis
- tasks that benefit from broader context or more deliberate planning

### Auto

`auto` is the user-facing default. The runtime decides which lane should execute the turn.

## Desktop chat behavior

The desktop streaming contract emits:

- `start`
- `commentary`
- `progress`
- `delta`
- `done`
- `error`

This gives the UI enough structure to show operational narration without pretending that partial reasoning is a final answer.

## Memory in the cognition loop

Memory supports cognition in three ways:

1. recent history is available for conversational continuity
2. hybrid retrieval brings back durable facts and preferences
3. background extraction turns repeated user signals into future context

## Heartbeats and autonomy

Heartbeats are scheduled prompts executed by the same core runtime. The system heartbeat is the built-in automation that checks `HEARTBEAT.md` in the workspace.

This means autonomy is not a separate product mode. It is the same orchestration engine applied on a schedule.

## Design constraints

- The UI must never become the source of cognitive truth.
- Tool use must remain bounded by backend policies and MCP validation.
- Commentary is a user experience layer, not a claim of chain-of-thought exposure.
