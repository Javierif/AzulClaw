"""Local scheduler for runtime jobs (including system heartbeat)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from agent_framework import Message

from azul_backend.workspace_layout import ensure_workspace_scaffold

from ..api.hatching_store import HatchingStore
from .store import (
    SYSTEM_HEARTBEAT_JOB_ID,
    RuntimeStore,
    ScheduledJob,
    parse_iso_datetime,
    to_iso_z,
    utc_now,
)

LOGGER = logging.getLogger(__name__)

USER_HEARTBEAT_INSTRUCTIONS = """
You are AzulClaw writing a proactive desktop chat message for the user.

Rules:
- Return only the message that should be shown to the user.
- Do not mention cron, scheduler, heartbeat, job execution, tools, files, workspace, or HEARTBEAT.md unless the user's task explicitly asks about those things.
- Do not inspect or discuss files. This execution has no tool access and no workspace duties.
- Do not ask where to send the message. The desktop chat is already the destination.
- For greetings, reminders, nudges, and check-ins, write the message directly.
- Reply in the same language as the scheduled task unless the task asks for another language.
- Keep the message concise and natural.
""".strip()


class RuntimeScheduler:
    """Runs all scheduled jobs on the same orchestrator."""

    def __init__(
        self,
        *,
        store: RuntimeStore,
        orchestrator: Any,
    ):
        self.store = store
        self.orchestrator = orchestrator
        self.stop_event = asyncio.Event()
        self.worker_task: asyncio.Task | None = None
        self.running_job_ids: set[str] = set()
        self.last_scheduler_error: str = ""

    async def start(self) -> None:
        """Starts the scheduler loop."""
        if self.worker_task is not None and not self.worker_task.done():
            return
        self.stop_event.clear()
        self.last_scheduler_error = ""
        # Ensure the system heartbeat job exists on startup
        self.store.ensure_system_heartbeat_job()
        self.worker_task = asyncio.create_task(self._run_loop(), name="azul-runtime-scheduler")

    async def stop(self) -> None:
        """Stops the background scheduler."""
        self.stop_event.set()
        if self.worker_task is None:
            return
        self.worker_task.cancel()
        try:
            await self.worker_task
        except asyncio.CancelledError:
            pass
        self.worker_task = None

    def get_status(self) -> dict[str, Any]:
        """Visible status of the scheduler."""
        return {
            "scheduler_running": self.worker_task is not None and not self.worker_task.done(),
            "scheduler_last_error": self.last_scheduler_error,
            "jobs_total": len(self.store.load_jobs()),
            "jobs_running": len(self.running_job_ids),
        }

    async def run_job_now(self, job_id: str) -> dict[str, Any]:
        """Manually runs a scheduled job."""
        job = next((item for item in self.store.load_jobs() if item.id == job_id), None)
        if job is None:
            raise ValueError("job not found")
        return await self._execute_job(job, reason="manual")

    async def _run_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self._tick()
                self.last_scheduler_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_scheduler_error = str(error).strip() or error.__class__.__name__
                LOGGER.exception("Scheduler loop error: %s", error)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        """Check all jobs and fire any that are due."""
        for job in self.store.load_jobs():
            if not job.enabled or job.id in self.running_job_ids:
                continue
            due_at = parse_iso_datetime(job.next_run_at or job.run_at)
            if due_at is None or due_at > utc_now():
                continue
            self.running_job_ids.add(job.id)
            asyncio.create_task(self._execute_job(job, reason="scheduled"))

    async def _execute_job(self, job: ScheduledJob, *, reason: str) -> dict[str, Any]:
        """Executes a job. System heartbeat jobs get HEARTBEAT.md injected."""
        run_time = utc_now()
        delivery: dict[str, str] = {}
        try:
            try:
                source = "cron"
                title = f"Scheduled job: {job.name}"
                execution_user_id = f"cron:{job.id}"

                # System heartbeat: inject HEARTBEAT.md content
                if job.system and job.id == SYSTEM_HEARTBEAT_JOB_ID:
                    heartbeat_text = self._load_heartbeat_text()
                    if not heartbeat_text:
                        # Nothing actionable — skip
                        self.store.mark_job_run(job.id, run_time)
                        return {
                            "job_id": job.id,
                            "reason": reason,
                            "ok": True,
                            "response": "HEARTBEAT_SKIP",
                        }
                    prompt = f"{job.prompt}\n\nActive checklist:\n{heartbeat_text}"
                    source = "heartbeat"
                    title = "Workspace heartbeat"
                    response = await self.orchestrator.process_message(
                        user_id=execution_user_id,
                        user_message=prompt,
                        lane=job.lane,
                        source=source,
                        store_memory=False,
                        title=title,
                    )
                else:
                    response = await self._execute_user_job(job, reason=reason)
                ok = True
                error_text = ""
            except Exception as error:
                ok = False
                error_text = str(error).strip() or error.__class__.__name__
                response = f"JOB_ERROR: {error_text}"
                LOGGER.exception("Job execution error %s (%s): %s", job.id, reason, error)

            delivery = self._deliver_to_desktop_chat(
                job,
                response,
                ok=ok,
                error_text=error_text,
            )
            updated = self.store.mark_job_run(job.id, run_time)
            result: dict[str, Any] = {
                "job_id": job.id,
                "reason": reason,
                "ok": ok,
                "response": response,
                "next_run_at": updated.next_run_at if updated else "",
                "delivery": delivery,
            }
            if error_text:
                result["error"] = error_text
            return result
        finally:
            self.running_job_ids.discard(job.id)

    def _deliver_to_desktop_chat(
        self,
        job: ScheduledJob,
        response: str,
        *,
        ok: bool,
        error_text: str,
    ) -> dict[str, str]:
        """Stores proactive job output in a visible desktop chat conversation."""
        if job.delivery_kind == "none":
            return {"kind": "none"}

        clean_response = (response or "").strip()
        if not clean_response:
            return {"kind": "none"}
        if job.system and clean_response in {"HEARTBEAT_OK", "HEARTBEAT_SKIP"}:
            return {"kind": "none"}

        memory = getattr(self.orchestrator, "memory", None)
        if memory is None:
            return {"kind": "none", "error": "memory unavailable"}

        user_id = (job.delivery_user_id or "desktop-user").strip() or "desktop-user"
        conversation_title = self._delivery_conversation_title(job)
        conversation_id = memory.get_active_conversation_id(user_id)
        if conversation_id:
            conversation_title = memory.get_conversation_title(conversation_id) or conversation_title

        if not conversation_id:
            conversation_id = (job.delivery_conversation_id or "").strip()
        if conversation_id and not memory.conversation_exists(conversation_id):
            conversation_id = ""

        if not conversation_id:
            conversation_id, conversation_title = memory.get_or_create_named_conversation(
                user_id,
                conversation_title,
            )
            self.store.set_job_delivery_conversation(job.id, conversation_id)

        if ok:
            content = clean_response
        else:
            detail = error_text or clean_response
            content = f"Heartbeat failed: {job.name}\n\n{detail}"
        memory.add_message(
            user_id,
            "assistant",
            content,
            conversation_id=conversation_id,
        )
        return {
            "kind": "desktop_chat",
            "user_id": user_id,
            "conversation_id": conversation_id,
            "conversation_title": conversation_title,
        }

    def _delivery_conversation_title(self, job: ScheduledJob) -> str:
        name = (job.name or "Heartbeat").strip()
        if name.lower().startswith("heartbeat"):
            return name[:80]
        return f"Heartbeat: {name}"[:80]

    async def _execute_user_job(self, job: ScheduledJob, *, reason: str) -> str:
        """Runs a user-created heartbeat without workspace tools or chat history."""
        prompt = self._build_user_job_prompt(job, reason=reason)
        runtime_manager = getattr(self.orchestrator, "runtime_manager", None)
        if runtime_manager is not None and hasattr(runtime_manager, "execute_messages"):
            result = await runtime_manager.execute_messages(
                messages=[Message(role="user", contents=prompt)],
                lane=job.lane,
                title=f"Scheduled job: {job.name}",
                source="cron",
                kind="scheduled-heartbeat",
                tools_enabled=False,
                instructions=USER_HEARTBEAT_INSTRUCTIONS,
            )
            return result.text

        return await self.orchestrator.process_message(
            user_id=f"cron:{job.id}",
            user_message=prompt,
            lane=job.lane,
            source="cron",
            store_memory=False,
            title=f"Scheduled job: {job.name}",
        )

    def _build_user_job_prompt(self, job: ScheduledJob, *, reason: str) -> str:
        """Builds an isolated execution prompt for user-created scheduled jobs."""
        return (
            "Recurring reminder execution.\n\n"
            "Write the exact desktop chat message the user should receive now. "
            "Do not explain the execution. Do not ask for a destination. "
            "Do not inspect files, workspace, or HEARTBEAT.md. "
            "If the task is a reminder or greeting, produce the reminder or greeting directly.\n\n"
            f"Run reason: {reason}\n"
            f"Reminder name: {job.name}\n"
            f"Scheduled task:\n{job.prompt}"
        )

    def _load_heartbeat_text(self) -> str:
        """Reads HEARTBEAT.md from the workspace, filtering comments and headers."""
        profile = HatchingStore().load()
        workspace_root = Path(profile.workspace_root).expanduser()
        workspace_root.mkdir(parents=True, exist_ok=True)
        ensure_workspace_scaffold(workspace_root)
        heartbeat_path = workspace_root / "HEARTBEAT.md"
        if not heartbeat_path.exists():
            heartbeat_path.write_text(
                "# HEARTBEAT.md\n\n# Leave this file empty or with comments only to skip heartbeats.\n",
                encoding="utf-8",
            )
            return ""

        content = heartbeat_path.read_text(encoding="utf-8").strip()
        lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return "\n".join(lines)
