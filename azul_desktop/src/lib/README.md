# Lib

This folder contains frontend contracts and infrastructure helpers.

- `api.ts` wraps calls to the local backend
- `contracts.ts` defines shared TypeScript types for desktop state
- `conversation-copy.ts` keeps chat copy helpers in one place
- `mock-data.ts` provides fallback data for local UI resilience

Keep feature logic out of this folder unless it is genuinely shared across views.
