# Cognitive Design

Last reviewed: 2026-04-20

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

Heartbeats are scheduled prompts executed by the local runtime, but not every heartbeat uses the same cognitive envelope.

The system heartbeat is the built-in automation that checks `HEARTBEAT.md` in the workspace. It is workspace-aware and can use the normal orchestration path.

User-created heartbeats are different. They are proactive scheduled messages or lightweight tasks, so they run isolated from normal chat history as `cron:<job_id>` and use a no-tools Agent Framework runtime with heartbeat-specific instructions. This prevents a simple reminder from reading workspace files, reacting to unrelated chat context, or asking where to send the message.

This means autonomy is not a single global product mode. It is the scheduling engine plus an execution envelope selected for the type of heartbeat.

## Design constraints

- The UI must never become the source of cognitive truth.
- Tool use must remain bounded by backend policies and MCP validation.
- Commentary is a user experience layer, not a claim of chain-of-thought exposure.
