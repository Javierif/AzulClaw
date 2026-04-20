"""Inference runtime on top of Microsoft Agent Framework with local fallback."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from agent_framework import Message

from ..cortex.kernel_setup import create_agent
from .process_registry import ProcessRegistry
from .store import RuntimeModelProfile, RuntimeSettings, RuntimeStore, to_iso_z


@dataclass
class RuntimeTurnResult:
    """Result of a runtime execution."""

    text: str
    model: RuntimeModelProfile | None
    attempts: list[dict[str, str]]
    process_id: str
    value: Any = None


def _serialize_runtime_text(result: Any, *, fallback: str = "") -> str:
    text = getattr(result, "text", None)
    if isinstance(text, str) and text.strip():
        return text

    value = getattr(result, "value", None)
    if isinstance(value, str):
        return value if value.strip() or not fallback else fallback
    if value is not None:
        if hasattr(value, "model_dump_json"):
            return value.model_dump_json()
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    if fallback:
        return fallback
    return str(result)


class AgentRuntimeManager:
    """Selects profiles, applies fallback, and records executions."""

    def __init__(
        self,
        *,
        mcp_client: Any,
        store: RuntimeStore,
        process_registry: ProcessRegistry,
    ):
        self.mcp_client = mcp_client
        self.store = store
        self.process_registry = process_registry
        self.agent_cache: dict[str, Any] = {}
        self.cooldowns: dict[str, float] = {}
        self.last_errors: dict[str, str] = {}
        self.probe_cache: dict[str, tuple[float, dict[str, str | bool]]] = {}

    def load_settings(self) -> RuntimeSettings:
        """Loads the effective runtime configuration."""
        return self.store.load_settings()

    def save_settings(self, payload: dict[str, Any]) -> RuntimeSettings:
        """Persists runtime configuration."""
        return self.store.save_settings(payload)

    def list_model_status(self) -> list[dict]:
        """Exposes the current status of each model profile."""
        now = time.time()
        items: list[dict] = []
        for model in self.load_settings().models:
            cooldown_until = self.cooldowns.get(model.id, 0.0)
            probe = self._probe_status_for_model(model)
            items.append(
                {
                    "id": model.id,
                    "label": model.label,
                    "lane": model.lane,
                    "provider": model.provider,
                    "deployment": model.deployment,
                    "enabled": model.enabled,
                    "streaming_enabled": model.streaming_enabled,
                    "available": model.enabled and cooldown_until <= now and bool(probe["available"]),
                    "cooldown_until": (
                        to_iso_z(datetime.fromtimestamp(cooldown_until, timezone.utc))
                        if cooldown_until > now
                        else ""
                    ),
                    "last_error": self.last_errors.get(model.id, ""),
                    "description": model.description,
                    "probe_detail": str(probe["detail"]),
                }
            )
        return items

    async def execute_messages_stream(
        self,
        *,
        messages: list[Message],
        lane: str,
        title: str,
        source: str,
        kind: str,
        on_delta: Callable[[str], Awaitable[None]],
        tools_enabled: bool = True,
        instructions: str | None = None,
    ) -> RuntimeTurnResult:
        """Runs an inference with streaming when the profile supports it."""
        candidates = self._resolve_candidates(lane)
        process = self.process_registry.start(
            title=title,
            kind=kind,
            source=source,
            lane=lane,
            detail="Preparing Agent Framework.",
        )

        attempts: list[dict[str, str]] = []
        if not candidates:
            detail = "No enabled model profiles found."
            self.process_registry.finish(process.id, status="failed", detail=detail)
            return RuntimeTurnResult(text=detail, model=None, attempts=attempts, process_id=process.id)

        for index, model in enumerate(candidates, start=1):
            self.process_registry.update(
                process.id,
                detail=f"Trying {model.label} ({model.deployment}).",
                model_id=model.id,
                model_label=model.label,
                attempts=index,
            )
            try:
                agent = await self._get_agent(
                    model,
                    tools_enabled=tools_enabled,
                    instructions=instructions,
                )
                if model.streaming_enabled:
                    stream = agent.stream_messages(messages)
                    streamed_parts: list[str] = []
                    async for update in stream:
                        chunk = self._extract_stream_chunk(update)
                        if not chunk:
                            continue
                        streamed_parts.append(chunk)
                        await on_delta(chunk)
                    final_response = await stream.get_final_response()
                    text = self._extract_final_text(final_response, fallback="".join(streamed_parts))
                else:
                    result = await agent.invoke_messages(messages)
                    value = getattr(result, "value", None)
                    text = _serialize_runtime_text(result)
                    if text:
                        await on_delta(text)

                self.last_errors.pop(model.id, None)
                self.cooldowns.pop(model.id, None)
                self.process_registry.finish(
                    process.id,
                    status="done",
                    detail=(
                        f"Completed with {model.label}"
                        f"{' in streaming' if model.streaming_enabled else ''}."
                    ),
                    model_id=model.id,
                    model_label=model.label,
                    attempts=index,
                )
                return RuntimeTurnResult(
                    text=text,
                    model=model,
                    attempts=attempts,
                    process_id=process.id,
                    value=value,
                )
            except Exception as error:
                error_text = str(error).strip() or error.__class__.__name__
                attempts.append({"model_id": model.id, "label": model.label, "error": error_text})
                self.last_errors[model.id] = error_text
                self.cooldowns[model.id] = time.time() + 30
                self.process_registry.update(
                    process.id,
                    detail=f"Failed on {model.label}. Trying fallback.",
                    attempts=index,
                )

        summary = "All profiles failed. " + " | ".join(
            f"{item['label']}: {item['error']}" for item in attempts
        )
        self.process_registry.finish(
            process.id,
            status="failed",
            detail=summary,
            attempts=len(attempts),
        )
        return RuntimeTurnResult(text=summary, model=None, attempts=attempts, process_id=process.id)

    async def execute_messages(
        self,
        *,
        messages: list[Message],
        lane: str,
        title: str,
        source: str,
        kind: str,
        response_format: Any | None = None,
        tools_enabled: bool = True,
        instructions: str | None = None,
    ) -> RuntimeTurnResult:
        """Runs an inference with fallback between profiles."""
        candidates = self._resolve_candidates(lane)
        process = self.process_registry.start(
            title=title,
            kind=kind,
            source=source,
            lane=lane,
            detail="Preparing Agent Framework.",
        )

        attempts: list[dict[str, str]] = []
        if not candidates:
            detail = "No enabled model profiles found."
            self.process_registry.finish(process.id, status="failed", detail=detail)
            return RuntimeTurnResult(text=detail, model=None, attempts=attempts, process_id=process.id)

        for index, model in enumerate(candidates, start=1):
            self.process_registry.update(
                process.id,
                detail=f"Trying {model.label} ({model.deployment}).",
                model_id=model.id,
                model_label=model.label,
                attempts=index,
            )
            try:
                agent = await self._get_agent(
                    model,
                    tools_enabled=tools_enabled,
                    instructions=instructions,
                )
                result = await agent.invoke_messages(messages, response_format=response_format)
                value = getattr(result, "value", None)
                text = _serialize_runtime_text(result)
                self.last_errors.pop(model.id, None)
                self.cooldowns.pop(model.id, None)
                self.process_registry.finish(
                    process.id,
                    status="done",
                    detail=f"Completed with {model.label}.",
                    model_id=model.id,
                    model_label=model.label,
                    attempts=index,
                )
                return RuntimeTurnResult(
                    text=text,
                    model=model,
                    attempts=attempts,
                    process_id=process.id,
                    value=value,
                )
            except Exception as error:
                error_text = str(error).strip() or error.__class__.__name__
                attempts.append({"model_id": model.id, "label": model.label, "error": error_text})
                self.last_errors[model.id] = error_text
                self.cooldowns[model.id] = time.time() + 30
                self.process_registry.update(
                    process.id,
                    detail=f"Failed on {model.label}. Trying fallback.",
                    attempts=index,
                )

        summary = "All profiles failed. " + " | ".join(
            f"{item['label']}: {item['error']}" for item in attempts
        )
        self.process_registry.finish(
            process.id,
            status="failed",
            detail=summary,
            attempts=len(attempts),
        )
        return RuntimeTurnResult(text=summary, model=None, attempts=attempts, process_id=process.id)

    def _resolve_candidates(self, lane: str) -> list[RuntimeModelProfile]:
        settings = self.load_settings()
        models = [model for model in settings.models if model.enabled and model.deployment.strip()]
        if not models:
            return []

        effective_lane = lane if lane in {"auto", "fast", "slow"} else settings.default_lane
        preferred = [settings.default_lane, "slow", "fast"]
        if effective_lane == "fast":
            preferred = ["fast", "slow"]
        elif effective_lane == "slow":
            preferred = ["slow", "fast"]

        ordered: list[RuntimeModelProfile] = []
        seen: set[str] = set()
        now = time.time()

        for wanted in preferred:
            for model in models:
                if model.id != wanted or model.id in seen:
                    continue
                cooldown_until = self.cooldowns.get(model.id, 0.0)
                if cooldown_until > now:
                    continue
                if not bool(self._probe_status_for_model(model)["available"]):
                    continue
                ordered.append(model)
                seen.add(model.id)

        for model in models:
            if model.id in seen:
                continue
            cooldown_until = self.cooldowns.get(model.id, 0.0)
            if cooldown_until > now:
                continue
            if not bool(self._probe_status_for_model(model)["available"]):
                continue
            ordered.append(model)
            seen.add(model.id)

        return ordered

    async def _get_agent(
        self,
        model: RuntimeModelProfile,
        *,
        tools_enabled: bool = True,
        instructions: str | None = None,
    ):
        instruction_key = "default"
        if instructions is not None:
            instruction_key = sha1(instructions.encode("utf-8")).hexdigest()[:12]
        tool_key = "tools" if tools_enabled else "no-tools"
        cache_key = f"{model.id}:{model.deployment}:{tool_key}:{instruction_key}"
        cached = self.agent_cache.get(cache_key)
        if cached is not None:
            return cached

        agent = await create_agent(
            self.mcp_client,
            model_profile=model,
            tools_enabled=tools_enabled,
            instructions=instructions,
        )
        self.agent_cache[cache_key] = agent
        return agent

    def _probe_status_for_model(self, model: RuntimeModelProfile) -> dict[str, str | bool]:
        """Checks real provider availability when feasible."""
        cache_key = f"{model.provider}:{model.id}:{model.deployment}"
        now = time.time()
        cached = self.probe_cache.get(cache_key)
        if cached is not None and now - cached[0] < 15:
            return cached[1]

        if model.provider == "openai":
            result = self._probe_openai_compatible_model(model)
        else:
            result = self._probe_azure_model(model)

        self.probe_cache[cache_key] = (now, result)
        return result

    def _extract_stream_chunk(self, update: Any) -> str:
        text = getattr(update, "text", None)
        if isinstance(text, str) and text:
            return text
        return ""

    def _extract_final_text(self, response: Any, *, fallback: str = "") -> str:
        return _serialize_runtime_text(response, fallback=fallback)

    def _probe_azure_model(self, model: RuntimeModelProfile) -> dict[str, str | bool]:
        """Checks whether Azure configuration is sufficient."""
        lane = model.lane.strip().lower()
        endpoint_var = "AZURE_OPENAI_FAST_ENDPOINT" if lane == "fast" else "AZURE_OPENAI_SLOW_ENDPOINT"
        api_key_var = "AZURE_OPENAI_FAST_API_KEY" if lane == "fast" else "AZURE_OPENAI_SLOW_API_KEY"
        endpoint = os.environ.get(endpoint_var, "").strip() or os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        api_key = os.environ.get(api_key_var, "").strip() or os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        if not endpoint or not api_key or not model.deployment.strip():
            return {"available": False, "detail": "Incomplete Azure configuration"}
        return {"available": True, "detail": "Azure configuration ready"}

    def _probe_openai_compatible_model(self, model: RuntimeModelProfile) -> dict[str, str | bool]:
        """Checks that the OpenAI-compatible runtime responds and publishes the model."""
        base_url = self._resolve_openai_base_url()
        api_key = os.environ.get("AZUL_FAST_OLLAMA_API_KEY", "").strip() or "ollama"
        models_url = f"{base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        req = urlrequest.Request(models_url, headers=headers, method="GET")

        try:
            with urlrequest.urlopen(req, timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urlerror.URLError:
            binary = shutil.which("ollama")
            if binary:
                return {"available": False, "detail": "Ollama detected but server not responding"}
            return {"available": False, "detail": "Ollama not detected"}
        except Exception:
            return {"available": False, "detail": "Error querying local models"}

        raw_models = payload.get("data", []) if isinstance(payload, dict) else []
        model_ids = [
            str(item.get("id", "")).strip()
            for item in raw_models
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ]
        if model.deployment in model_ids:
            return {"available": True, "detail": f"Local model available at {base_url}"}
        if model_ids:
            return {"available": False, "detail": f"Ollama responds, but {model.deployment} is missing"}
        return {"available": False, "detail": "Ollama responds with no published models"}

    def _resolve_openai_base_url(self) -> str:
        base_url = (
            os.environ.get("AZUL_FAST_OLLAMA_BASE_URL", "").strip()
            or os.environ.get("OLLAMA_HOST", "").strip()
            or "http://127.0.0.1:11434/v1"
        ).rstrip("/")
        if base_url.endswith("/v1"):
            return base_url
        return f"{base_url}/v1"
