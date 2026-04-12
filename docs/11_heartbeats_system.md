# AzulClaw: Unified Heartbeats System (Scheduler)

**Last reviewed:** April 2026

## 1. Overview

The **Heartbeats** system in AzulClaw is the mechanism that gives the agent "its own initiative." Historically, there were two parallel concepts doing the same thing:
1. **System Heartbeat**: The system's base "pulse" to check context (reading state files and running a general check prompt).
2. **Scheduled Jobs**: Recurring tasks created by the user.

To simplify the cognitive architecture, an **architectural unification** has been made. Now, **everything is a Heartbeat**. All routines and automations (including the base system) share a single data model, the same UI view, and the same evaluation loop.

## 2. The System Heartbeat (`system-heartbeat`)

The main AzulClaw heartbeat is now treated as a *native/built-in Heartbeat*.
*   **Indestructible:** It cannot be deleted from the interface (it appears with a 🔒 lock icon).
*   **Automatic Context Injection:** The execution engine (Scheduler) intercepts the execution of this specific heartbeat (`system: true` with id `system-heartbeat`) and automatically injects the contents of the `HEARTBEAT.md` file (if it exists in the Workspace) alongside the prompt configured in the UI.
*   **Self-creation:** On brain startup in `store.py`, the `ensure_system_heartbeat_job()` function checks if it exists. If not, it creates it automatically with default values, ensuring the agent never loses its "pulse."

If the agent scans `HEARTBEAT.md` and finds nothing actionable, the thread closes silently returning a `HEARTBEAT_SKIP` instead of invoking an expensive System 2 inference.

## 3. Custom Heartbeats (User Jobs)

User automations are handled identically to the main pulse, allowing for reminders, periodic validations, or reports with the following properties:
*   Frequency (`interval_seconds`).
*   Specific prompt.
*   Pause/Resume mechanics.

## 4. Triage and Brain Selection (Lanes)

In the user interface, when creating a Heartbeat, manually selecting the "Brain" (e.g., forcing System 1 `fast` or System 2 `slow`) is no longer allowed. We maintain an opinionated stance where the _Lane_ is always **Auto**.
The workload is routed through our internal **Triage** system, where the `fast` agent decides at runtime whether the heartbeat task is trivial enough to solve locally or if it requires delegating to the `slow` model. This abstracts the cognitive load of coordinating LLMs away from the user.

## 5. Internal Architecture and Key Components

### 5.1 Backend
*   **`azul_backend/azul_brain/runtime/store.py`**: Holds the unified `ScheduledJob` model. We added the `system: bool` field and protected the deletion of built-in system tasks.
*   **`azul_backend/azul_brain/runtime/scheduler.py`**: Contains a single `_execute_job()` method to process all heartbeats in turn, avoiding redundancies.

### 5.2 Frontend
*   All user-facing text refers to translations of **"Heartbeats / Automations"**.
*   The old system pulse configuration views were merged with the jobs list, creating the unified `HeartbeatsShell.tsx` interface.
