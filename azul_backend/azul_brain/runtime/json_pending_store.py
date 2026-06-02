"""Shared JSON persistence for single-list, TTL-pruned dataclass stores."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Generic, TypeVar

from .store import parse_iso_datetime, utc_now

T = TypeVar("T")


class JsonPendingStore(Generic[T]):
    """Persists a list of dataclass records in a single JSON file.

    Owns the load/parse/prune-expired/save mechanics shared by the pending
    approval and preview stores. Subclasses provide the per-type deserializer
    and may override the expiry side-effect hook.
    """

    def __init__(self, path: Path, ttl_seconds: int) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _deserialize(self, item: dict[str, Any]) -> T | None:
        """Builds a record from a raw dict, or returns None to skip it."""
        raise NotImplementedError

    def _created_at(self, item: T) -> str:
        return str(getattr(item, "created_at", "") or "")

    def _on_prune_expired(self, item: T) -> None:
        """Hook invoked when a record is dropped during expiry pruning."""

    def load(self) -> list[T]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        items: list[T] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            parsed = self._deserialize(entry)
            if parsed is not None:
                items.append(parsed)
        active: list[T] = []
        for item in items:
            if self._is_expired(item):
                self._on_prune_expired(item)
                continue
            active.append(item)
        if len(active) != len(items):
            self._save(active)
        return active

    def _save(self, items: list[T]) -> None:
        self.path.write_text(
            json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _is_expired(self, item: T) -> bool:
        if self.ttl_seconds <= 0:
            return False
        created_at = parse_iso_datetime(self._created_at(item))
        if created_at is None:
            return False
        return (utc_now() - created_at).total_seconds() > self.ttl_seconds
