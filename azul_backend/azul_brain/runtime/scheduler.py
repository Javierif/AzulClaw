"""Local scheduler for runtime jobs (including system heartbeat)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

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
        try:
            try:
                prompt = job.prompt
                source = "cron"
                title = f"Scheduled job: {job.name}"

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
                    user_id=f"cron:{job.id}",
                    user_message=prompt,
                    lane=job.lane,
                    source=source,
                    store_memory=False,
                    title=title,
                )
                ok = True
                error_text = ""
            except Exception as error:
                ok = False
                error_text = str(error).strip() or error.__class__.__name__
                response = f"JOB_ERROR: {error_text}"
                LOGGER.exception("Job execution error %s (%s): %s", job.id, reason, error)

            updated = self.store.mark_job_run(job.id, run_time)
            result: dict[str, Any] = {
                "job_id": job.id,
                "reason": reason,
                "ok": ok,
                "response": response,
                "next_run_at": updated.next_run_at if updated else "",
            }
            if error_text:
                result["error"] = error_text
            return result
        finally:
            self.running_job_ids.discard(job.id)

    def _load_heartbeat_text(self) -> str:
        """Reads HEARTBEAT.md from the workspace, filtering comments and headers."""
        profile = HatchingStore().load()
        workspace_root = Path(profile.workspace_root)
        workspace_root.mkdir(parents=True, exist_ok=True)
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
