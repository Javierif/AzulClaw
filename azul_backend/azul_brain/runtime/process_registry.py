"""Registro de ejecuciones activas y recientes del runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal
from uuid import uuid4

from .store import ProcessHistoryEntry, RuntimeStore, to_iso_z, utc_now


@dataclass
class RuntimeProcess:
    """Proceso visible para la desktop app."""

    id: str
    title: str
    kind: str
    source: str
    lane: str
    status: Literal["running", "waiting", "done", "failed"]
    detail: str
    started_at: str
    updated_at: str
    model_id: str = ""
    model_label: str = ""
    attempts: int = 0


class ProcessRegistry:
    """Mantiene procesos activos y un historial corto persistido."""

    def __init__(self, store: RuntimeStore, max_history: int = 40):
        self.store = store
        self.max_history = max_history
        self.active: dict[str, RuntimeProcess] = {}
        self.history: list[RuntimeProcess] = [
            RuntimeProcess(**asdict(item)) for item in self.store.load_process_history()
        ]

    def start(self, *, title: str, kind: str, source: str, lane: str, detail: str) -> RuntimeProcess:
        """Abre un proceso en estado running."""
        now = to_iso_z(utc_now())
        process = RuntimeProcess(
            id=f"run-{uuid4().hex[:12]}",
            title=title,
            kind=kind,
            source=source,
            lane=lane,
            status="running",
            detail=detail,
            started_at=now,
            updated_at=now,
        )
        self.active[process.id] = process
        return process

    def update(
        self,
        process_id: str,
        *,
        detail: str | None = None,
        status: Literal["running", "waiting", "done", "failed"] | None = None,
        model_id: str | None = None,
        model_label: str | None = None,
        attempts: int | None = None,
    ) -> RuntimeProcess | None:
        """Actualiza un proceso activo."""
        process = self.active.get(process_id)
        if process is None:
            return None

        if detail is not None:
            process.detail = detail
        if status is not None:
            process.status = status
        if model_id is not None:
            process.model_id = model_id
        if model_label is not None:
            process.model_label = model_label
        if attempts is not None:
            process.attempts = max(0, attempts)
        process.updated_at = to_iso_z(utc_now())
        return process

    def finish(
        self,
        process_id: str,
        *,
        status: Literal["done", "failed"],
        detail: str,
        model_id: str = "",
        model_label: str = "",
        attempts: int = 0,
    ) -> RuntimeProcess | None:
        """Cierra un proceso y lo mueve a historial persistido."""
        process = self.active.pop(process_id, None)
        if process is None:
            return None

        process.status = status
        process.detail = detail
        process.updated_at = to_iso_z(utc_now())
        if model_id:
            process.model_id = model_id
        if model_label:
            process.model_label = model_label
        process.attempts = max(0, attempts)

        self.history.insert(0, process)
        self.history = self.history[: self.max_history]
        self.store.save_process_history(
            [ProcessHistoryEntry(**asdict(item)) for item in self.history]
        )
        return process

    def list_processes(self) -> list[dict]:
        """Devuelve procesos activos y recientes ordenados por actualidad."""
        running = sorted(self.active.values(), key=lambda item: item.updated_at, reverse=True)
        return [asdict(item) for item in [*running, *self.history]]
