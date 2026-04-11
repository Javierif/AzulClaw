"""Persistencia local para runtime, modelos y jobs programados."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal


def utc_now() -> datetime:
    """Devuelve la hora actual en UTC."""
    return datetime.now(timezone.utc)


def to_iso_z(value: datetime | None) -> str:
    """Serializa un datetime a ISO-8601 UTC con sufijo Z."""
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso_datetime(raw_value: str | None) -> datetime | None:
    """Parsea datetimes ISO-8601 aceptando sufijo Z."""
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
    """Perfil ejecutable de modelo para Agent Framework."""

    id: str
    label: str
    lane: Literal["fast", "slow"]
    provider: Literal["azure", "openai"]
    deployment: str
    enabled: bool = True
    streaming_enabled: bool = False
    description: str = ""


@dataclass
class RuntimeSettings:
    """Configuracion editable del runtime local."""

    default_lane: Literal["auto", "fast", "slow"] = "auto"
    heartbeat_enabled: bool = True
    heartbeat_interval_seconds: int = 900
    heartbeat_prompt: str = (
        "Heartbeat del sistema. Lee la checklist operativa y actua solo sobre lo indicado. "
        "Si no hay nada accionable, responde exactamente HEARTBEAT_OK."
    )
    models: list[RuntimeModelProfile] = field(default_factory=list)


@dataclass
class ScheduledJob:
    """Trabajo programado persistido en disco."""

    id: str
    name: str
    prompt: str
    lane: Literal["auto", "fast", "slow"] = "fast"
    schedule_kind: Literal["at", "every"] = "every"
    run_at: str = ""
    interval_seconds: int = 0
    enabled: bool = True
    created_at: str = field(default_factory=lambda: to_iso_z(utc_now()))
    updated_at: str = field(default_factory=lambda: to_iso_z(utc_now()))
    last_run_at: str = ""
    next_run_at: str = ""


@dataclass
class ProcessHistoryEntry:
    """Representacion persistida de una ejecucion reciente."""

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
    """Lee y escribe configuracion y estado basico del runtime."""

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
        """Construye configuracion por defecto desde variables de entorno."""
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
        heartbeat_interval = self._bounded_int(
            os.environ.get("AZUL_HEARTBEAT_INTERVAL_SECONDS", "900"),
            default=900,
            min_value=60,
            max_value=86_400,
        )
        default_lane = os.environ.get("AZUL_DEFAULT_LANE", "auto").strip().lower()
        if default_lane not in {"auto", "fast", "slow"}:
            default_lane = "auto"

        return RuntimeSettings(
            default_lane=default_lane,
            heartbeat_enabled=self._parse_bool(os.environ.get("AZUL_HEARTBEAT_ENABLED"), True),
            heartbeat_interval_seconds=heartbeat_interval,
            heartbeat_prompt=(
                os.environ.get("AZUL_HEARTBEAT_PROMPT", "").strip()
                or RuntimeSettings().heartbeat_prompt
            ),
            models=[
                fast_profile
                if fast_profile.deployment
                else RuntimeModelProfile(
                    id="fast",
                    label="Cerebro rapido",
                    lane="fast",
                    provider="azure",
                    deployment=fast_deployment,
                    enabled=True,
                    description="Turnos rapidos, heartbeats y tareas de baja latencia.",
                ),
                RuntimeModelProfile(
                    id="slow",
                    label="Cerebro lento",
                    lane="slow",
                    provider="azure",
                    deployment=slow_deployment,
                    enabled=True,
                    streaming_enabled=self._parse_bool(
                        os.environ.get("AZUL_SLOW_STREAMING_ENABLED"),
                        False,
                    ),
                    description="Turnos deliberados y tareas que requieren mas contexto.",
                ),
            ],
        )

    def load_settings(self) -> RuntimeSettings:
        """Carga ajustes persistidos fusionandolos con defaults seguros."""
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
            heartbeat_enabled=bool(raw.get("heartbeat_enabled", defaults.heartbeat_enabled)),
            heartbeat_interval_seconds=self._bounded_int(
                raw.get("heartbeat_interval_seconds"),
                default=defaults.heartbeat_interval_seconds,
                min_value=60,
                max_value=86_400,
            ),
            heartbeat_prompt=(
                str(raw.get("heartbeat_prompt", defaults.heartbeat_prompt)).strip()
                or defaults.heartbeat_prompt
            ),
            models=list(models_by_id.values()),
        )

    def save_settings(self, payload: dict[str, Any]) -> RuntimeSettings:
        """Valida y persiste ajustes editables del runtime."""
        current = self.load_settings()
        merged = RuntimeSettings(
            default_lane=current.default_lane,
            heartbeat_enabled=current.heartbeat_enabled,
            heartbeat_interval_seconds=current.heartbeat_interval_seconds,
            heartbeat_prompt=current.heartbeat_prompt,
            models=current.models,
        )

        raw_lane = str(payload.get("default_lane", merged.default_lane)).strip().lower()
        if raw_lane in {"auto", "fast", "slow"}:
            merged.default_lane = raw_lane

        if "heartbeat_enabled" in payload:
            merged.heartbeat_enabled = bool(payload.get("heartbeat_enabled"))

        if "heartbeat_interval_seconds" in payload:
            merged.heartbeat_interval_seconds = self._bounded_int(
                payload.get("heartbeat_interval_seconds"),
                default=merged.heartbeat_interval_seconds,
                min_value=60,
                max_value=86_400,
            )

        if "heartbeat_prompt" in payload:
            prompt = str(payload.get("heartbeat_prompt", "")).strip()
            if prompt:
                merged.heartbeat_prompt = prompt

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
        """Carga jobs persistidos de cron local."""
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
            if schedule_kind not in {"at", "every"}:
                schedule_kind = "every"

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
                    enabled=bool(item.get("enabled", True)),
                    created_at=str(item.get("created_at", "")).strip() or to_iso_z(utc_now()),
                    updated_at=str(item.get("updated_at", "")).strip() or to_iso_z(utc_now()),
                    last_run_at=str(item.get("last_run_at", "")).strip(),
                    next_run_at=str(item.get("next_run_at", "")).strip(),
                )
            )
        return jobs

    def save_jobs(self, jobs: list[ScheduledJob]) -> list[ScheduledJob]:
        """Persiste la lista completa de jobs."""
        payload = [asdict(job) for job in jobs]
        self.jobs_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return jobs

    def upsert_job(self, payload: dict[str, Any]) -> ScheduledJob:
        """Crea o actualiza un job programado."""
        jobs = self.load_jobs()
        by_id = {job.id: job for job in jobs}

        job_id = str(payload.get("id", "")).strip() or f"job-{utc_now().strftime('%Y%m%d%H%M%S')}"
        current = by_id.get(job_id)

        lane = str(payload.get("lane", current.lane if current else "fast")).strip().lower()
        if lane not in {"auto", "fast", "slow"}:
            lane = current.lane if current else "fast"

        schedule_kind = str(
            payload.get("schedule_kind", current.schedule_kind if current else "every")
        ).strip().lower()
        if schedule_kind not in {"at", "every"}:
            schedule_kind = current.schedule_kind if current else "every"

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

        if schedule_kind == "at" and parse_iso_datetime(run_at) is None:
            raise ValueError("run_at must be an ISO datetime for 'at' jobs")
        if schedule_kind == "every" and interval_seconds < 60:
            raise ValueError("interval_seconds must be >= 60 for recurring jobs")

        next_run_at = self._compute_next_run_at(
            schedule_kind=schedule_kind,
            run_at=run_at,
            interval_seconds=interval_seconds,
            previous_last_run_at=current.last_run_at if current else "",
        )

        job = ScheduledJob(
            id=job_id,
            name=name,
            prompt=prompt,
            lane=lane,
            schedule_kind=schedule_kind,
            run_at=run_at,
            interval_seconds=interval_seconds,
            enabled=bool(payload.get("enabled", current.enabled if current else True)),
            created_at=current.created_at if current else to_iso_z(utc_now()),
            updated_at=to_iso_z(utc_now()),
            last_run_at=current.last_run_at if current else "",
            next_run_at=next_run_at,
        )

        by_id[job.id] = job
        self.save_jobs(list(by_id.values()))
        return job

    def delete_job(self, job_id: str) -> bool:
        """Elimina un job por identificador."""
        safe_id = str(job_id).strip()
        jobs = [job for job in self.load_jobs() if job.id != safe_id]
        self.save_jobs(jobs)
        return True

    def mark_job_run(self, job_id: str, run_time: datetime | None = None) -> ScheduledJob | None:
        """Actualiza timestamps de ejecucion y siguiente disparo."""
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
            else:
                next_run_at = to_iso_z(target_time + timedelta(seconds=job.interval_seconds))

            updated = ScheduledJob(
                id=job.id,
                name=job.name,
                prompt=job.prompt,
                lane=job.lane,
                schedule_kind=job.schedule_kind,
                run_at=job.run_at,
                interval_seconds=job.interval_seconds,
                enabled=enabled,
                created_at=job.created_at,
                updated_at=to_iso_z(target_time),
                last_run_at=last_run_at,
                next_run_at=next_run_at,
            )
            next_jobs.append(updated)

        self.save_jobs(next_jobs)
        return updated

    def load_process_history(self) -> list[ProcessHistoryEntry]:
        """Carga ejecuciones recientes persistidas."""
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
        """Persiste historial reciente de procesos."""
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
    ) -> str:
        if schedule_kind == "at":
            return run_at

        reference = parse_iso_datetime(previous_last_run_at)
        if reference is None:
            reference = utc_now()
        return to_iso_z(reference + timedelta(seconds=interval_seconds))

    def _build_fast_profile(self) -> RuntimeModelProfile:
        """Resuelve el perfil rapido usando Ollama si existe o Azure mini si no."""
        if self._should_use_local_fast():
            model_id = (
                os.environ.get("AZUL_FAST_OLLAMA_MODEL", "").strip()
                or os.environ.get("OLLAMA_MODEL", "").strip()
                or "phi4-mini"
            )
            return RuntimeModelProfile(
                id="fast",
                label="Cerebro rapido",
                lane="fast",
                provider="openai",
                deployment=model_id,
                enabled=True,
                streaming_enabled=self._parse_bool(
                    os.environ.get("AZUL_FAST_STREAMING_ENABLED"),
                    True,
                ),
                description="Ruta rapida local via Ollama usando API OpenAI-compatible.",
            )

        deployment = (
            os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "").strip()
            or os.environ.get("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "").strip()
            or "gpt-4o-mini"
        )
        return RuntimeModelProfile(
            id="fast",
            label="Cerebro rapido",
            lane="fast",
            provider="azure",
            deployment=deployment,
            enabled=True,
            streaming_enabled=self._parse_bool(
                os.environ.get("AZUL_FAST_STREAMING_ENABLED"),
                True,
            ),
            description="Ruta rapida cloud usando un deployment mini de Azure OpenAI.",
        )

    def _should_use_local_fast(self) -> bool:
        """Decide si el perfil rapido debe intentar usar Ollama."""
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
            "heartbeat_enabled": settings.heartbeat_enabled,
            "heartbeat_interval_seconds": settings.heartbeat_interval_seconds,
            "heartbeat_prompt": settings.heartbeat_prompt,
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
