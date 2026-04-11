"""Scheduler local para jobs y heartbeats del runtime."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..api.hatching_store import HatchingStore
from .store import RuntimeStore, ScheduledJob, parse_iso_datetime, to_iso_z, utc_now

LOGGER = logging.getLogger(__name__)


class RuntimeScheduler:
    """Ejecuta jobs recurrentes y heartbeats sobre el mismo orquestador."""

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
        self.last_heartbeat_at: str = ""
        self.last_heartbeat_result: str = ""
        self.last_heartbeat_error: str = ""
        self.next_heartbeat_at: str = ""
        self.last_scheduler_error: str = ""

    async def start(self) -> None:
        """Arranca el loop de scheduler."""
        if self.worker_task is not None and not self.worker_task.done():
            return
        self.stop_event.clear()
        self.last_scheduler_error = ""
        self.worker_task = asyncio.create_task(self._run_loop(), name="azul-runtime-scheduler")

    async def stop(self) -> None:
        """Detiene el scheduler en segundo plano."""
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
        """Estado visible de heartbeats y scheduler."""
        settings = self.orchestrator.runtime_manager.load_settings()
        workspace_root = HatchingStore().load().workspace_root
        return {
            "heartbeat": {
                "enabled": settings.heartbeat_enabled,
                "interval_seconds": settings.heartbeat_interval_seconds,
                "prompt": settings.heartbeat_prompt,
                "next_run_at": self.next_heartbeat_at,
                "last_run_at": self.last_heartbeat_at,
                "last_result": self.last_heartbeat_result,
                "last_error": self.last_heartbeat_error,
                "workspace_root": workspace_root,
                "heartbeat_file": str(Path(workspace_root) / "HEARTBEAT.md"),
            },
            "scheduler_running": self.worker_task is not None and not self.worker_task.done(),
            "scheduler_last_error": self.last_scheduler_error,
            "jobs_total": len(self.store.load_jobs()),
            "jobs_running": len(self.running_job_ids),
        }

    async def run_job_now(self, job_id: str) -> dict[str, Any]:
        """Ejecuta manualmente un job programado."""
        job = next((item for item in self.store.load_jobs() if item.id == job_id), None)
        if job is None:
            raise ValueError("job not found")
        return await self._execute_job(job, reason="manual")

    async def run_heartbeat_now(self) -> dict[str, Any]:
        """Ejecuta el heartbeat inmediatamente, aunque no toque por horario."""
        return await self._execute_heartbeat(reason="manual", reschedule=False)

    async def _run_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self._tick()
                self.last_scheduler_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_scheduler_error = str(error).strip() or error.__class__.__name__
                LOGGER.exception("Fallo en el loop del scheduler: %s", error)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        await self._tick_heartbeat()
        for job in self.store.load_jobs():
            if not job.enabled or job.id in self.running_job_ids:
                continue
            due_at = parse_iso_datetime(job.next_run_at or job.run_at)
            if due_at is None or due_at > utc_now():
                continue
            self.running_job_ids.add(job.id)
            asyncio.create_task(self._execute_job(job, reason="scheduled"))

    async def _tick_heartbeat(self) -> None:
        settings = self.orchestrator.runtime_manager.load_settings()
        if not settings.heartbeat_enabled:
            self.next_heartbeat_at = ""
            return

        now = utc_now()
        if not self.next_heartbeat_at:
            self.next_heartbeat_at = to_iso_z(now)

        next_due = parse_iso_datetime(self.next_heartbeat_at)
        if next_due is None or next_due > now:
            return

        await self._execute_heartbeat(reason="scheduled", reschedule=True)

    async def _execute_heartbeat(self, *, reason: str, reschedule: bool) -> dict[str, Any]:
        settings = self.orchestrator.runtime_manager.load_settings()
        run_time = utc_now()
        run_at = to_iso_z(run_time)

        if not settings.heartbeat_enabled:
            self.next_heartbeat_at = ""
        if reschedule and settings.heartbeat_enabled:
            self.next_heartbeat_at = to_iso_z(
                run_time + timedelta(seconds=settings.heartbeat_interval_seconds)
            )

        try:
            heartbeat_text = self._load_heartbeat_text()
            if not heartbeat_text:
                self.last_heartbeat_at = run_at
                self.last_heartbeat_result = "HEARTBEAT_SKIP"
                self.last_heartbeat_error = ""
                return {
                    "ok": True,
                    "reason": reason,
                    "run_at": run_at,
                    "result": self.last_heartbeat_result,
                    "next_run_at": self.next_heartbeat_at,
                }

            response = await self.orchestrator.process_message(
                user_id="heartbeat-system",
                user_message=f"{settings.heartbeat_prompt}\n\nChecklist activa:\n{heartbeat_text}",
                lane="fast",
                source="heartbeat",
                store_memory=False,
                title="Heartbeat del workspace",
            )
            self.last_heartbeat_at = run_at
            self.last_heartbeat_result = response[:160]
            self.last_heartbeat_error = ""
            return {
                "ok": True,
                "reason": reason,
                "run_at": run_at,
                "result": self.last_heartbeat_result,
                "next_run_at": self.next_heartbeat_at,
            }
        except Exception as error:
            error_text = str(error).strip() or error.__class__.__name__
            self.last_heartbeat_at = run_at
            self.last_heartbeat_result = "HEARTBEAT_ERROR"
            self.last_heartbeat_error = error_text
            LOGGER.exception("Fallo ejecutando heartbeat (%s): %s", reason, error)
            return {
                "ok": False,
                "reason": reason,
                "run_at": run_at,
                "result": self.last_heartbeat_result,
                "error": error_text,
                "next_run_at": self.next_heartbeat_at,
            }

    async def _execute_job(self, job: ScheduledJob, *, reason: str) -> dict[str, Any]:
        run_time = utc_now()
        try:
            try:
                response = await self.orchestrator.process_message(
                    user_id=f"cron:{job.id}",
                    user_message=job.prompt,
                    lane=job.lane,
                    source="cron",
                    store_memory=False,
                    title=f"Job programado: {job.name}",
                )
                ok = True
                error_text = ""
            except Exception as error:
                ok = False
                error_text = str(error).strip() or error.__class__.__name__
                response = f"JOB_ERROR: {error_text}"
                LOGGER.exception("Fallo ejecutando job %s (%s): %s", job.id, reason, error)

            updated = self.store.mark_job_run(job.id, run_time)
            result = {
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
        profile = HatchingStore().load()
        workspace_root = Path(profile.workspace_root)
        workspace_root.mkdir(parents=True, exist_ok=True)
        heartbeat_path = workspace_root / "HEARTBEAT.md"
        if not heartbeat_path.exists():
            heartbeat_path.write_text(
                "# HEARTBEAT.md\n\n# Deja este archivo vacio o con comentarios para omitir heartbeats.\n",
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
