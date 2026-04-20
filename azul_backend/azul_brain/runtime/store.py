"""Local persistence for runtime, models, and scheduled jobs."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal


def utc_now() -> datetime:
    """Returns the current UTC time."""
    return datetime.now(timezone.utc)


def to_iso_z(value: datetime | None) -> str:
    """Serialises a datetime to ISO-8601 UTC with a Z suffix."""
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso_datetime(raw_value: str | None) -> datetime | None:
    """Parses ISO-8601 datetimes accepting a Z suffix."""
    text = (raw_value or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _runtime_root() -> Path:
    return Path(__file__).resolve().parents[3] / "memory"


def _default_settings_path() -> Path:
    return _runtime_root() / "runtime_settings.json"


def _default_jobs_path() -> Path:
    return _runtime_root() / "runtime_jobs.json"


def _default_process_history_path() -> Path:
    return _runtime_root() / "runtime_process_history.json"


@dataclass
class RuntimeModelProfile:
    """Executable model profile for Agent Framework."""

    id: str
    label: str
    lane: Literal["fast", "slow"]
    provider: Literal["azure", "openai"]
    deployment: str
    enabled: bool = True
    streaming_enabled: bool = False
    description: str = ""


SYSTEM_HEARTBEAT_JOB_ID = "system-heartbeat"
SYSTEM_HEARTBEAT_DEFAULT_PROMPT = (
    "System heartbeat. Read HEARTBEAT.md if it exists. "
    "Follow it strictly. If nothing needs attention, respond exactly HEARTBEAT_OK."
)
SYSTEM_HEARTBEAT_DEFAULT_INTERVAL = 900


@dataclass
class RuntimeSettings:
    """Editable local runtime configuration."""

    default_lane: Literal["auto", "fast", "slow"] = "auto"

    models: list[RuntimeModelProfile] = field(default_factory=list)


@dataclass
class ScheduledJob:
    """Scheduled job persisted to disk."""

    id: str
    name: str
    prompt: str
    lane: Literal["auto", "fast", "slow"] = "fast"
    schedule_kind: Literal["at", "every", "cron"] = "every"
    run_at: str = ""
    interval_seconds: int = 0
    cron_expression: str = ""
    enabled: bool = True
    system: bool = False
    source: str = "user"
    delivery_kind: Literal["desktop_chat", "none"] = "desktop_chat"
    delivery_user_id: str = "desktop-user"
    delivery_conversation_id: str = ""
    created_at: str = field(default_factory=lambda: to_iso_z(utc_now()))
    updated_at: str = field(default_factory=lambda: to_iso_z(utc_now()))
    last_run_at: str = ""
    next_run_at: str = ""


@dataclass
class ProcessHistoryEntry:
    """Persisted representation of a recent execution."""

    id: str
    title: str
    kind: str
    source: str
    lane: str
    status: str
    detail: str
    started_at: str
    updated_at: str
    model_id: str = ""
    model_label: str = ""
    attempts: int = 0


class RuntimeStore:
    """Reads and writes basic runtime configuration and state."""

    def __init__(
        self,
        settings_path: Path | None = None,
        jobs_path: Path | None = None,
        process_history_path: Path | None = None,
    ):
        self.settings_path = settings_path or _default_settings_path()
        self.jobs_path = jobs_path or _default_jobs_path()
        self.process_history_path = process_history_path or _default_process_history_path()

        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
        self.process_history_path.parent.mkdir(parents=True, exist_ok=True)

    def default_settings(self) -> RuntimeSettings:
        """Builds default configuration from environment variables."""
        fast_profile = self._build_fast_profile()
        slow_deployment = (
            os.environ.get("AZURE_OPENAI_SLOW_DEPLOYMENT", "").strip()
            or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
            or "gpt-4o"
        )
        fast_deployment = (
            os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "").strip()
            or os.environ.get("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "").strip()
            or "gpt-4o-mini"
        )
        default_lane = os.environ.get("AZUL_DEFAULT_LANE", "auto").strip().lower()
        if default_lane not in {"auto", "fast", "slow"}:
            default_lane = "auto"

        return RuntimeSettings(
            default_lane=default_lane,
            models=[
                fast_profile
                if fast_profile.deployment
                else RuntimeModelProfile(
                    id="fast",
                    label="Fast brain",
                    lane="fast",
                    provider="azure",
                    deployment=fast_deployment,
                    enabled=True,
                    description="Fast turns, heartbeats, and low-latency tasks.",
                ),
                RuntimeModelProfile(
                    id="slow",
                    label="Slow brain",
                    lane="slow",
                    provider="azure",
                    deployment=slow_deployment,
                    enabled=True,
                    streaming_enabled=self._parse_bool(
                        os.environ.get("AZUL_SLOW_STREAMING_ENABLED"),
                        False,
                    ),
                    description="Deliberate turns and tasks that require more context.",
                ),
            ],
        )

    def load_settings(self) -> RuntimeSettings:
        """Loads persisted settings merged with safe defaults."""
        defaults = self.default_settings()
        if not self.settings_path.exists():
            return defaults

        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return defaults

        default_lane = str(raw.get("default_lane", defaults.default_lane)).strip().lower()
        if default_lane not in {"auto", "fast", "slow"}:
            default_lane = defaults.default_lane

        models_by_id = {model.id: model for model in defaults.models}
        saved_models = raw.get("models", [])
        if isinstance(saved_models, list):
            for item in saved_models:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id", "")).strip()
                if model_id not in models_by_id:
                    continue
                current = models_by_id[model_id]
                deployment = str(item.get("deployment", current.deployment)).strip() or current.deployment
                label = str(item.get("label", current.label)).strip() or current.label
                lane = str(item.get("lane", current.lane)).strip().lower()
                if lane not in {"fast", "slow"}:
                    lane = current.lane
                provider = str(item.get("provider", current.provider)).strip().lower()
                if provider not in {"azure", "openai"}:
                    provider = current.provider
                models_by_id[model_id] = RuntimeModelProfile(
                    id=current.id,
                    label=label,
                    lane=lane,
                    provider=provider,
                    deployment=deployment,
                    enabled=bool(item.get("enabled", current.enabled)),
                    streaming_enabled=bool(
                        item.get("streaming_enabled", current.streaming_enabled)
                    ),
                    description=(
                        str(item.get("description", current.description)).strip()
                        or current.description
                    ),
                )

        return RuntimeSettings(
            default_lane=default_lane,
            models=list(models_by_id.values()),
        )

    def save_settings(self, payload: dict[str, Any]) -> RuntimeSettings:
        """Validates and persists editable runtime settings."""
        current = self.load_settings()
        merged = RuntimeSettings(
            default_lane=current.default_lane,
            models=current.models,
        )

        raw_lane = str(payload.get("default_lane", merged.default_lane)).strip().lower()
        if raw_lane in {"auto", "fast", "slow"}:
            merged.default_lane = raw_lane

        model_updates = payload.get("models", [])
        if isinstance(model_updates, list):
            by_id = {model.id: model for model in merged.models}
            for item in model_updates:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id", "")).strip()
                current_model = by_id.get(model_id)
                if current_model is None:
                    continue
                by_id[model_id] = RuntimeModelProfile(
                    id=current_model.id,
                    label=str(item.get("label", current_model.label)).strip() or current_model.label,
                    lane=current_model.lane,
                    provider=current_model.provider,
                    deployment=(
                        str(item.get("deployment", current_model.deployment)).strip()
                        or current_model.deployment
                    ),
                    enabled=bool(item.get("enabled", current_model.enabled)),
                    streaming_enabled=bool(
                        item.get("streaming_enabled", current_model.streaming_enabled)
                    ),
                    description=(
                        str(item.get("description", current_model.description)).strip()
                        or current_model.description
                    ),
                )
            merged.models = list(by_id.values())

        self.settings_path.write_text(
            json.dumps(self._settings_to_dict(merged), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return merged

    def load_jobs(self) -> list[ScheduledJob]:
        """Loads persisted jobs from the local cron store."""
        if not self.jobs_path.exists():
            return []

        try:
            raw = json.loads(self.jobs_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        if not isinstance(raw, list):
            return []

        jobs: list[ScheduledJob] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("id", "")).strip()
            prompt = str(item.get("prompt", "")).strip()
            if not job_id or not prompt:
                continue

            lane = str(item.get("lane", "fast")).strip().lower()
            if lane not in {"auto", "fast", "slow"}:
                lane = "fast"

            schedule_kind = str(item.get("schedule_kind", "every")).strip().lower()
            if schedule_kind not in {"at", "every", "cron"}:
                schedule_kind = "every"

            is_system_job = bool(item.get("system", False)) or job_id == SYSTEM_HEARTBEAT_JOB_ID
            source = str(item.get("source", "user")).strip() or "user"
            if is_system_job:
                source = "system"
            delivery_kind = str(item.get("delivery_kind", "desktop_chat")).strip().lower()
            if delivery_kind not in {"desktop_chat", "none"}:
                delivery_kind = "desktop_chat"

            jobs.append(
                ScheduledJob(
                    id=job_id,
                    name=str(item.get("name", job_id)).strip() or job_id,
                    prompt=prompt,
                    lane=lane,
                    schedule_kind=schedule_kind,
                    run_at=str(item.get("run_at", "")).strip(),
                    interval_seconds=self._bounded_int(
                        item.get("interval_seconds"),
                        default=0,
                        min_value=0,
                        max_value=31_536_000,
                    ),
                    cron_expression=str(item.get("cron_expression", "")).strip(),
                    enabled=bool(item.get("enabled", True)),
                    system=is_system_job,
                    source=source,
                    delivery_kind=delivery_kind,
                    delivery_user_id=str(item.get("delivery_user_id", "desktop-user")).strip()
                    or "desktop-user",
                    delivery_conversation_id=str(item.get("delivery_conversation_id", "")).strip(),
                    created_at=str(item.get("created_at", "")).strip() or to_iso_z(utc_now()),
                    updated_at=str(item.get("updated_at", "")).strip() or to_iso_z(utc_now()),
                    last_run_at=str(item.get("last_run_at", "")).strip(),
                    next_run_at=str(item.get("next_run_at", "")).strip(),
                )
            )
        return jobs

    def save_jobs(self, jobs: list[ScheduledJob]) -> list[ScheduledJob]:
        """Persists the full job list."""
        payload = [asdict(job) for job in jobs]
        self.jobs_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return jobs

    def upsert_job(self, payload: dict[str, Any]) -> ScheduledJob:
        """Creates or updates a scheduled job."""
        jobs = self.load_jobs()
        by_id = {job.id: job for job in jobs}

        job_id = str(payload.get("id", "")).strip() or f"job-{utc_now().strftime('%Y%m%d%H%M%S')}"
        current = by_id.get(job_id)
        is_system_job = bool(current.system) if current else job_id == SYSTEM_HEARTBEAT_JOB_ID

        lane = str(payload.get("lane", current.lane if current else "fast")).strip().lower()
        if lane not in {"auto", "fast", "slow"}:
            lane = current.lane if current else "fast"

        schedule_kind = str(
            payload.get("schedule_kind", current.schedule_kind if current else "every")
        ).strip().lower()
        if schedule_kind not in {"at", "every", "cron"}:
            schedule_kind = current.schedule_kind if current else "every"
        if is_system_job:
            schedule_kind = "every"

        prompt = str(payload.get("prompt", current.prompt if current else "")).strip()
        if not prompt:
            raise ValueError("prompt is required")

        name = str(payload.get("name", current.name if current else job_id)).strip() or job_id
        interval_seconds = self._bounded_int(
            payload.get("interval_seconds", current.interval_seconds if current else 0),
            default=current.interval_seconds if current else 0,
            min_value=0,
            max_value=31_536_000,
        )
        run_at = str(payload.get("run_at", current.run_at if current else "")).strip()
        cron_expression = str(
            payload.get("cron_expression", current.cron_expression if current else "")
        ).strip()
        if is_system_job:
            run_at = ""
            cron_expression = ""

        if schedule_kind == "at" and parse_iso_datetime(run_at) is None:
            raise ValueError("run_at must be an ISO datetime for 'at' jobs")
        if schedule_kind == "every" and interval_seconds < 60:
            raise ValueError("interval_seconds must be >= 60 for recurring jobs")
        if schedule_kind == "cron" and not self._is_valid_cron_expression(cron_expression):
            raise ValueError("cron_expression must be a valid 5-field cron expression")

        next_run_at = self._compute_next_run_at(
            schedule_kind=schedule_kind,
            run_at=run_at,
            interval_seconds=interval_seconds,
            cron_expression=cron_expression,
            previous_last_run_at=current.last_run_at if current else "",
        )

        source = "system" if is_system_job else (current.source if current else "user")
        delivery_kind = str(
            payload.get("delivery_kind", current.delivery_kind if current else "desktop_chat")
        ).strip().lower()
        if delivery_kind not in {"desktop_chat", "none"}:
            delivery_kind = current.delivery_kind if current else "desktop_chat"
        delivery_user_id = str(
            payload.get("delivery_user_id", current.delivery_user_id if current else "desktop-user")
        ).strip() or "desktop-user"
        delivery_conversation_id = str(
            payload.get(
                "delivery_conversation_id",
                current.delivery_conversation_id if current else "",
            )
        ).strip()

        job = ScheduledJob(
            id=job_id,
            name=name,
            prompt=prompt,
            lane=lane,
            schedule_kind=schedule_kind,
            run_at=run_at,
            interval_seconds=interval_seconds,
            cron_expression=cron_expression,
            enabled=bool(payload.get("enabled", current.enabled if current else True)),
            system=is_system_job,
            source=source,
            delivery_kind=delivery_kind,
            delivery_user_id=delivery_user_id,
            delivery_conversation_id=delivery_conversation_id,
            created_at=current.created_at if current else to_iso_z(utc_now()),
            updated_at=to_iso_z(utc_now()),
            last_run_at=current.last_run_at if current else "",
            next_run_at=next_run_at,
        )

        by_id[job.id] = job
        self.save_jobs(list(by_id.values()))
        return job

    def delete_job(self, job_id: str) -> bool:
        """Deletes a job by identifier. System jobs cannot be deleted."""
        safe_id = str(job_id).strip()
        jobs = self.load_jobs()
        target = next((j for j in jobs if j.id == safe_id), None)
        if safe_id == SYSTEM_HEARTBEAT_JOB_ID or (target and target.system):
            raise ValueError("System jobs cannot be deleted")
        jobs = [j for j in jobs if j.id != safe_id]
        self.save_jobs(jobs)
        return True

    def ensure_system_heartbeat_job(self) -> ScheduledJob:
        """Creates or repairs the system heartbeat job if needed."""
        jobs = self.load_jobs()
        existing = next((j for j in jobs if j.id == SYSTEM_HEARTBEAT_JOB_ID), None)
        heartbeat_interval = self._bounded_int(
            existing.interval_seconds if existing else os.environ.get("AZUL_HEARTBEAT_INTERVAL_SECONDS", "900"),
            default=SYSTEM_HEARTBEAT_DEFAULT_INTERVAL,
            min_value=60,
            max_value=86_400,
        )

        if existing is not None:
            repaired = ScheduledJob(
                id=SYSTEM_HEARTBEAT_JOB_ID,
                name=existing.name.strip() or "System heartbeat",
                prompt=existing.prompt.strip() or SYSTEM_HEARTBEAT_DEFAULT_PROMPT,
                lane=existing.lane if existing.lane in {"auto", "fast", "slow"} else "fast",
                schedule_kind="every",
                run_at="",
                interval_seconds=heartbeat_interval,
                cron_expression="",
                enabled=existing.enabled,
                system=True,
                source="system",
                delivery_kind=existing.delivery_kind,
                delivery_user_id=existing.delivery_user_id or "desktop-user",
                delivery_conversation_id=existing.delivery_conversation_id,
                created_at=existing.created_at or to_iso_z(utc_now()),
                updated_at=existing.updated_at or to_iso_z(utc_now()),
                last_run_at=existing.last_run_at,
                next_run_at=existing.next_run_at
                or self._compute_next_run_at(
                    schedule_kind="every",
                    run_at="",
                    interval_seconds=heartbeat_interval,
                    cron_expression="",
                    previous_last_run_at=existing.last_run_at,
                ),
            )

            self.save_jobs(
                [repaired if job.id == SYSTEM_HEARTBEAT_JOB_ID else job for job in jobs]
            )
            return repaired

        heartbeat_prompt = (
            os.environ.get("AZUL_HEARTBEAT_PROMPT", "").strip()
            or SYSTEM_HEARTBEAT_DEFAULT_PROMPT
        )
        heartbeat_enabled = self._parse_bool(
            os.environ.get("AZUL_HEARTBEAT_ENABLED"), True
        )

        job = ScheduledJob(
            id=SYSTEM_HEARTBEAT_JOB_ID,
            name="System heartbeat",
            prompt=heartbeat_prompt,
            lane="fast",
            schedule_kind="every",
            interval_seconds=heartbeat_interval,
            cron_expression="",
            enabled=heartbeat_enabled,
            system=True,
            source="system",
            delivery_kind="desktop_chat",
            delivery_user_id="desktop-user",
            next_run_at=self._compute_next_run_at(
                schedule_kind="every",
                run_at="",
                interval_seconds=heartbeat_interval,
                cron_expression="",
                previous_last_run_at="",
            ),
        )
        jobs.insert(0, job)
        self.save_jobs(jobs)
        return job

    def mark_job_run(self, job_id: str, run_time: datetime | None = None) -> ScheduledJob | None:
        """Updates execution and next-fire timestamps."""
        jobs = self.load_jobs()
        target_time = run_time or utc_now()
        updated: ScheduledJob | None = None

        next_jobs: list[ScheduledJob] = []
        for job in jobs:
            if job.id != job_id:
                next_jobs.append(job)
                continue

            last_run_at = to_iso_z(target_time)
            next_run_at = ""
            enabled = job.enabled

            if job.schedule_kind == "at":
                enabled = False
            elif job.schedule_kind == "cron":
                next_run_at = self._compute_next_run_at(
                    schedule_kind="cron",
                    run_at="",
                    interval_seconds=0,
                    cron_expression=job.cron_expression,
                    previous_last_run_at=last_run_at,
                )
            else:
                interval_seconds = max(60, job.interval_seconds)
                next_run_at = to_iso_z(target_time + timedelta(seconds=interval_seconds))

            updated = ScheduledJob(
                id=job.id,
                name=job.name,
                prompt=job.prompt,
                lane=job.lane,
                schedule_kind=job.schedule_kind,
                run_at=job.run_at,
                interval_seconds=max(60, job.interval_seconds)
                if job.schedule_kind == "every"
                else job.interval_seconds,
                cron_expression=job.cron_expression,
                enabled=enabled,
                system=job.system,
                source=job.source,
                delivery_kind=job.delivery_kind,
                delivery_user_id=job.delivery_user_id,
                delivery_conversation_id=job.delivery_conversation_id,
                created_at=job.created_at,
                updated_at=to_iso_z(target_time),
                last_run_at=last_run_at,
                next_run_at=next_run_at,
            )
            next_jobs.append(updated)

        self.save_jobs(next_jobs)
        return updated

    def set_job_delivery_conversation(
        self,
        job_id: str,
        conversation_id: str,
    ) -> ScheduledJob | None:
        """Persists the desktop chat conversation used for proactive deliveries."""
        safe_id = str(job_id).strip()
        safe_conversation_id = str(conversation_id).strip()
        if not safe_id or not safe_conversation_id:
            return None

        jobs = self.load_jobs()
        updated: ScheduledJob | None = None
        next_jobs: list[ScheduledJob] = []
        for job in jobs:
            if job.id != safe_id:
                next_jobs.append(job)
                continue
            updated = ScheduledJob(
                id=job.id,
                name=job.name,
                prompt=job.prompt,
                lane=job.lane,
                schedule_kind=job.schedule_kind,
                run_at=job.run_at,
                interval_seconds=job.interval_seconds,
                cron_expression=job.cron_expression,
                enabled=job.enabled,
                system=job.system,
                source=job.source,
                delivery_kind=job.delivery_kind,
                delivery_user_id=job.delivery_user_id,
                delivery_conversation_id=safe_conversation_id,
                created_at=job.created_at,
                updated_at=to_iso_z(utc_now()),
                last_run_at=job.last_run_at,
                next_run_at=job.next_run_at,
            )
            next_jobs.append(updated)

        if updated is not None:
            self.save_jobs(next_jobs)
        return updated

    def load_process_history(self) -> list[ProcessHistoryEntry]:
        """Loads persisted recent executions."""
        if not self.process_history_path.exists():
            return []

        try:
            raw = json.loads(self.process_history_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        if not isinstance(raw, list):
            return []

        items: list[ProcessHistoryEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            process_id = str(item.get("id", "")).strip()
            if not process_id:
                continue
            items.append(
                ProcessHistoryEntry(
                    id=process_id,
                    title=str(item.get("title", process_id)).strip() or process_id,
                    kind=str(item.get("kind", "agent-run")).strip() or "agent-run",
                    source=str(item.get("source", "chat")).strip() or "chat",
                    lane=str(item.get("lane", "auto")).strip() or "auto",
                    status=str(item.get("status", "done")).strip() or "done",
                    detail=str(item.get("detail", "")).strip(),
                    started_at=str(item.get("started_at", "")).strip(),
                    updated_at=str(item.get("updated_at", "")).strip(),
                    model_id=str(item.get("model_id", "")).strip(),
                    model_label=str(item.get("model_label", "")).strip(),
                    attempts=self._bounded_int(item.get("attempts"), default=0, min_value=0, max_value=99),
                )
            )
        return items

    def save_process_history(self, items: list[ProcessHistoryEntry]) -> list[ProcessHistoryEntry]:
        """Persists recent process history."""
        payload = [asdict(item) for item in items]
        self.process_history_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return items

    def _compute_next_run_at(
        self,
        *,
        schedule_kind: str,
        run_at: str,
        interval_seconds: int,
        previous_last_run_at: str,
        cron_expression: str = "",
    ) -> str:
        if schedule_kind == "at":
            return run_at
        if schedule_kind == "cron":
            return self._compute_next_cron_run_at(cron_expression, previous_last_run_at)

        reference = parse_iso_datetime(previous_last_run_at)
        if reference is None:
            reference = utc_now()
        return to_iso_z(reference + timedelta(seconds=interval_seconds))

    def _compute_next_cron_run_at(self, cron_expression: str, previous_last_run_at: str) -> str:
        try:
            from croniter import croniter
        except ModuleNotFoundError as error:
            raise ValueError("croniter dependency is required for cron scheduled jobs") from error

        reference = (parse_iso_datetime(previous_last_run_at) or utc_now()).astimezone()
        return to_iso_z(croniter(cron_expression, reference).get_next(datetime))

    def _is_valid_cron_expression(self, cron_expression: str) -> bool:
        if not cron_expression:
            return False
        if len(cron_expression.split()) != 5:
            return False
        try:
            from croniter import croniter
        except ModuleNotFoundError as error:
            raise ValueError("croniter dependency is required for cron scheduled jobs") from error
        return bool(croniter.is_valid(cron_expression))

    def _build_fast_profile(self) -> RuntimeModelProfile:
        """Resolves the fast profile using Ollama if available, Azure mini otherwise."""
        if self._should_use_local_fast():
            model_id = (
                os.environ.get("AZUL_FAST_OLLAMA_MODEL", "").strip()
                or os.environ.get("OLLAMA_MODEL", "").strip()
                or "phi4-mini"
            )
            return RuntimeModelProfile(
                id="fast",
                label="Fast brain",
                lane="fast",
                provider="openai",
                deployment=model_id,
                enabled=True,
                streaming_enabled=self._parse_bool(
                    os.environ.get("AZUL_FAST_STREAMING_ENABLED"),
                    True,
                ),
                description="Fast local route via Ollama using the OpenAI-compatible API.",
            )

        deployment = (
            os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "").strip()
            or os.environ.get("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "").strip()
            or "gpt-4o-mini"
        )
        return RuntimeModelProfile(
            id="fast",
            label="Fast brain",
            lane="fast",
            provider="azure",
            deployment=deployment,
            enabled=True,
            streaming_enabled=self._parse_bool(
                os.environ.get("AZUL_FAST_STREAMING_ENABLED"),
                True,
            ),
            description="Fast cloud route using an Azure OpenAI mini deployment.",
        )

    def _should_use_local_fast(self) -> bool:
        """Decides whether the fast profile should attempt to use Ollama."""
        preference = os.environ.get("AZUL_FAST_PROVIDER", "auto").strip().lower()
        if preference == "azure":
            return False

        binary_available = bool(shutil.which("ollama"))
        explicit_remote = bool(os.environ.get("OLLAMA_HOST", "").strip())
        custom_base_url = (
            os.environ.get("AZUL_FAST_OLLAMA_BASE_URL", "").strip()
            and os.environ.get("AZUL_FAST_OLLAMA_BASE_URL", "").strip() != "http://127.0.0.1:11434/v1"
        )
        if preference == "ollama":
            return binary_available or explicit_remote or custom_base_url
        return binary_available

    def _settings_to_dict(self, settings: RuntimeSettings) -> dict[str, Any]:
        return {
            "default_lane": settings.default_lane,
            "models": [asdict(model) for model in settings.models],
        }

    def _parse_bool(self, raw_value: str | None, default: bool) -> bool:
        text = (raw_value or "").strip().lower()
        if not text:
            return default
        return text in {"1", "true", "yes", "on"}

    def _bounded_int(
        self,
        raw_value: Any,
        *,
        default: int,
        min_value: int,
        max_value: int,
    ) -> int:
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            return default
        return max(min_value, min(parsed, max_value))
