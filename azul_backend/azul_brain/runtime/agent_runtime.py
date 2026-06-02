"""Inference runtime on top of Microsoft Agent Framework with local fallback."""

from __future__ import annotations

import json
import inspect
import logging
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from agent_framework import Content, Message

from ..azure_auth import describe_azure_openai_auth
from ..cortex.kernel_setup import create_agent
from .process_registry import ProcessRegistry
from .store import RuntimeModelProfile, RuntimeSettings, RuntimeStore, to_iso_z

LOGGER = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>[\s\S]*?</think\s*>", re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think\s*>", re.IGNORECASE)
_CONTEXT_RETRY_NOTE = (
    "Previous attempt hit the model context limit. Work only with the compacted context. "
    "If a task spans many files or tool results, summarize first and continue in smaller batches."
)
_CONTEXT_PREFLIGHT_NOTE = (
    "The request context was pre-compacted to stay within the request budget. "
    "Work from the most relevant recent context and keep multi-step work in smaller batches."
)
_CONTEXT_COMPACTION_NOTE = (
    "Earlier context was compacted after a context-length rejection. "
    "Prioritize the most recent request and keep any plan batched."
)
_CONTEXT_RETRY_SYSTEM_LIMIT = 2
_CONTEXT_RETRY_SYSTEM_TEXT_LIMIT = 3_000
_CONTEXT_RETRY_HISTORY_LIMIT = 4
_CONTEXT_RETRY_HISTORY_TEXT_LIMIT = 1_500
_CONTEXT_RETRY_FINAL_TEXT_LIMIT = 4_000
_TRUNCATED_CONTEXT_SUFFIX = "\n\n[Context truncated]"
_PREFLIGHT_TEXT_CHAR_LIMIT = 250_000
_PREFLIGHT_BINARY_BYTE_LIMIT = 2_000_000


@dataclass
class RuntimeTurnResult:
    """Result of a runtime execution."""

    text: str
    model: RuntimeModelProfile | None
    attempts: list[dict[str, str]]
    process_id: str
    attempt_count: int = 0
    skipped_models: list[dict[str, str]] | None = None
    failed_attempts: list[dict[str, str]] | None = None
    value: Any = None


def _serialize_runtime_text(result: Any, *, fallback: str = "") -> str:
    text = getattr(result, "text", None)
    if isinstance(text, str) and text.strip():
        return _strip_reasoning_artifacts(text)

    value = getattr(result, "value", None)
    if isinstance(value, str):
        raw_value = value if value.strip() or not fallback else fallback
        return _strip_reasoning_artifacts(raw_value)
    if value is not None:
        if hasattr(value, "model_dump_json"):
            return _strip_reasoning_artifacts(value.model_dump_json())
        try:
            return _strip_reasoning_artifacts(json.dumps(value, ensure_ascii=False))
        except TypeError:
            return _strip_reasoning_artifacts(str(value))

    if fallback:
        return _strip_reasoning_artifacts(fallback)
    return _strip_reasoning_artifacts(str(result))


def _strip_reasoning_artifacts(text: str) -> str:
    """Removes model-internal thinking tags from user-visible output."""
    if not text:
        return text

    cleaned = _THINK_BLOCK_RE.sub("", text)
    dangling_close = list(_THINK_CLOSE_RE.finditer(cleaned))
    if dangling_close:
        first_close = dangling_close[0]
        suffix = cleaned[first_close.end():]
        if first_close.start() <= 200 and suffix and not suffix[0].isspace():
            cleaned = suffix
    if _THINK_OPEN_RE.search(cleaned) and not _THINK_CLOSE_RE.search(cleaned):
        cleaned = _THINK_OPEN_RE.sub("", cleaned)
    return cleaned.strip()


def _is_context_overflow_error(error_text: str) -> bool:
    normalized = (error_text or "").strip().lower()
    if not normalized:
        return False
    if (
        "context length" in normalized
        or "maximum context length" in normalized
        or "longer than the model's context length" in normalized
        or "too many tokens" in normalized
    ):
        return True
    return "input (" in normalized and "tokens" in normalized and "longer than" in normalized


def _truncate_context_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATED_CONTEXT_SUFFIX):
        return text[:limit]
    return text[: limit - len(_TRUNCATED_CONTEXT_SUFFIX)].rstrip() + _TRUNCATED_CONTEXT_SUFFIX


def _copy_message_with_text_limit(message: Message, *, max_text_chars: int) -> Message:
    raw_contents = getattr(message, "contents", []) or []
    if isinstance(raw_contents, str):
        return Message(
            role=getattr(message, "role", "user"),
            contents=_truncate_context_text(raw_contents, max_text_chars),
        )

    if not isinstance(raw_contents, list):
        raw_contents = list(raw_contents)

    remaining = max_text_chars
    new_contents: list[Content] = []
    for content in raw_contents:
        if isinstance(content, str):
            trimmed = _truncate_context_text(content, remaining)
            if trimmed:
                new_contents.append(Content.from_text(trimmed))
                remaining = max(0, remaining - len(trimmed))
            continue

        content_type = getattr(content, "type", None)
        if content_type == "text":
            text = str(getattr(content, "text", "") or "")
            trimmed = _truncate_context_text(text, remaining)
            if trimmed:
                new_contents.append(Content.from_text(trimmed))
                remaining = max(0, remaining - len(trimmed))
            continue
        new_contents.append(content)

    if not new_contents:
        new_contents = [Content.from_text("")]
    return Message(role=getattr(message, "role", "user"), contents=new_contents)


def _context_retry_instructions(instructions: str | None) -> str:
    scoped = (instructions or "").strip()
    if not scoped:
        return _CONTEXT_RETRY_NOTE
    if _CONTEXT_RETRY_NOTE in scoped:
        return scoped
    return f"{scoped}\n\n{_CONTEXT_RETRY_NOTE}"


def _context_preflight_instructions(instructions: str | None) -> str:
    scoped = (instructions or "").strip()
    if not scoped:
        return _CONTEXT_PREFLIGHT_NOTE
    if _CONTEXT_PREFLIGHT_NOTE in scoped:
        return scoped
    return f"{scoped}\n\n{_CONTEXT_PREFLIGHT_NOTE}"


def _compact_messages_for_context_retry(messages: list[Message]) -> list[Message]:
    if not messages:
        return []

    final_message = messages[-1]
    leading_system = [
        _copy_message_with_text_limit(message, max_text_chars=_CONTEXT_RETRY_SYSTEM_TEXT_LIMIT)
        for message in messages[:-1]
        if getattr(message, "role", "") == "system"
    ][:_CONTEXT_RETRY_SYSTEM_LIMIT]
    history = [
        _copy_message_with_text_limit(message, max_text_chars=_CONTEXT_RETRY_HISTORY_TEXT_LIMIT)
        for message in messages[:-1]
        if getattr(message, "role", "") != "system"
    ][-_CONTEXT_RETRY_HISTORY_LIMIT:]

    compacted: list[Message] = [*leading_system]
    omitted_count = max(0, len(messages[:-1]) - len(leading_system) - len(history))
    if omitted_count:
        compacted.append(Message(role="system", contents=_CONTEXT_COMPACTION_NOTE))
    compacted.append(
        _copy_message_with_text_limit(final_message, max_text_chars=_CONTEXT_RETRY_FINAL_TEXT_LIMIT)
    )
    return compacted


def _estimate_message_payload(messages: list[Message]) -> dict[str, int]:
    text_chars = 0
    binary_bytes = 0
    content_parts = 0
    for message in messages:
        contents = getattr(message, "contents", []) or []
        if isinstance(contents, str):
            contents = [contents]
        elif not isinstance(contents, list):
            contents = list(contents)

        for content in contents:
            content_parts += 1
            if isinstance(content, str):
                text_chars += len(content)
                continue
            content_type = getattr(content, "type", None)
            if content_type == "text":
                text_chars += len(str(getattr(content, "text", "") or ""))
                continue
            if content_type == "data":
                data = getattr(content, "data", b"") or b""
                if isinstance(data, bytes):
                    binary_bytes += len(data)
                else:
                    text_chars += len(str(data))
                continue
            if content_type == "uri":
                text_chars += len(str(getattr(content, "uri", "") or ""))
                continue
            text_chars += len(str(content))
    return {
        "message_count": len(messages),
        "content_parts": content_parts,
        "text_chars": text_chars,
        "binary_bytes": binary_bytes,
    }


def _preflight_limits() -> tuple[int, int]:
    try:
        text_limit = int(os.environ.get("AZUL_RUNTIME_PREFLIGHT_TEXT_CHARS", str(_PREFLIGHT_TEXT_CHAR_LIMIT)))
    except ValueError:
        text_limit = _PREFLIGHT_TEXT_CHAR_LIMIT
    try:
        binary_limit = int(os.environ.get("AZUL_RUNTIME_PREFLIGHT_BINARY_BYTES", str(_PREFLIGHT_BINARY_BYTE_LIMIT)))
    except ValueError:
        binary_limit = _PREFLIGHT_BINARY_BYTE_LIMIT
    return max(0, text_limit), max(0, binary_limit)


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

    def supports_multimodal_input(self, lane: str) -> bool:
        """Returns whether the selected deployment is expected to accept image inputs."""
        candidates = self._resolve_candidates(lane)
        if not candidates:
            candidates = self._resolve_candidates("auto")
        if not candidates:
            return False
        model = candidates[0]
        if model.capabilities:
            return self._capabilities_support_visual_input(model.capabilities)
        return model.provider == "azure"

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
                    "capabilities": model.capabilities,
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

    def _capabilities_support_visual_input(self, capabilities: list[str]) -> bool:
        normalized = [
            str(item).strip().lower().replace("_", "-")
            for item in capabilities
            if str(item).strip()
        ]
        for capability in normalized:
            if "embedding" in capability:
                continue
            if "vision" in capability or "image" in capability or "multimodal" in capability:
                return True
        return False

    def _log_request_payload(
        self,
        *,
        model: RuntimeModelProfile,
        messages: list[Message],
        source: str,
        title: str,
        tools_enabled: bool,
        instructions: str | None,
        phase: str,
    ) -> None:
        stats = _estimate_message_payload(messages)
        LOGGER.info(
            "[Runtime] %s model=%s deployment=%s source=%s title=%s tools=%s messages=%s contents=%s text_chars=%s binary_bytes=%s task_instructions_chars=%s",
            phase,
            model.label,
            model.deployment,
            source,
            title,
            tools_enabled,
            stats["message_count"],
            stats["content_parts"],
            stats["text_chars"],
            stats["binary_bytes"],
            len((instructions or "").strip()),
        )

    def _maybe_preflight_compact_messages(
        self,
        *,
        model: RuntimeModelProfile,
        messages: list[Message],
        source: str,
        title: str,
        tools_enabled: bool,
        instructions: str | None,
        process_id: str,
    ) -> tuple[list[Message], str | None]:
        stats = _estimate_message_payload(messages)
        text_limit, binary_limit = _preflight_limits()
        should_compact = (
            (text_limit > 0 and stats["text_chars"] > text_limit)
            or (binary_limit > 0 and stats["binary_bytes"] > binary_limit)
        )
        if not should_compact:
            return messages, instructions

        compacted_messages = _compact_messages_for_context_retry(messages)
        compacted_instructions = _context_preflight_instructions(instructions)
        self.process_registry.update(
            process_id,
            detail=f"Compacting context before dispatch to {model.label}.",
            model_id=model.id,
            model_label=model.label,
        )
        self._log_request_payload(
            model=model,
            messages=compacted_messages,
            source=source,
            title=title,
            tools_enabled=tools_enabled,
            instructions=compacted_instructions,
            phase="Preflight compacted runtime payload",
        )
        return compacted_messages, compacted_instructions

    async def _retry_with_compacted_context(
        self,
        *,
        model: RuntimeModelProfile,
        messages: list[Message],
        error_text: str,
        process_id: str,
        source: str,
        title: str,
        response_format: Any | None = None,
        tools_enabled: bool = True,
        instructions: str | None = None,
    ) -> tuple[Any | None, str]:
        if not _is_context_overflow_error(error_text):
            return None, ""

        compacted_messages = _compact_messages_for_context_retry(messages)
        self.process_registry.update(
            process_id,
            detail=f"{model.label} exceeded the context window. Retrying with compacted context.",
            model_id=model.id,
            model_label=model.label,
        )
        retry_instructions = _context_retry_instructions(instructions)
        self._log_request_payload(
            model=model,
            messages=compacted_messages,
            source=source,
            title=title,
            tools_enabled=tools_enabled,
            instructions=retry_instructions,
            phase="Retrying with compacted context",
        )
        try:
            retry_agent = await self._get_agent(
                model,
                tools_enabled=tools_enabled,
                instructions=retry_instructions,
            )
            result = await retry_agent.invoke_messages(
                compacted_messages,
                response_format=response_format,
            )
            return result, ""
        except Exception as retry_error:
            retry_text = str(retry_error).strip() or retry_error.__class__.__name__
            return None, retry_text

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
        skipped_models = self._inspect_candidate_skips(lane, selected_model_ids={model.id for model in candidates})
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
            return RuntimeTurnResult(
                text=detail,
                model=None,
                attempts=attempts,
                process_id=process.id,
                skipped_models=skipped_models,
                failed_attempts=attempts,
            )

        for index, model in enumerate(candidates, start=1):
            self.process_registry.update(
                process.id,
                detail=f"Trying {model.label} ({model.deployment}).",
                model_id=model.id,
                model_label=model.label,
                attempts=index,
            )
            streamed_parts: list[str] = []
            try:
                dispatch_messages, dispatch_instructions = self._maybe_preflight_compact_messages(
                    model=model,
                    messages=messages,
                    source=source,
                    title=title,
                    tools_enabled=tools_enabled,
                    instructions=instructions,
                    process_id=process.id,
                )
                self._log_request_payload(
                    model=model,
                    messages=dispatch_messages,
                    source=source,
                    title=title,
                    tools_enabled=tools_enabled,
                    instructions=dispatch_instructions,
                    phase="Invoking runtime stream",
                )
                agent = await self._get_agent(
                    model,
                    tools_enabled=tools_enabled,
                    instructions=dispatch_instructions,
                )
                value = None
                if model.streaming_enabled:
                    stream = agent.stream_messages(dispatch_messages)
                    if inspect.isawaitable(stream):
                        stream = await stream
                    async for update in stream:
                        chunk = self._extract_stream_chunk(update)
                        if not chunk:
                            continue
                        streamed_parts.append(chunk)
                        await on_delta(chunk)
                    final_response = await stream.get_final_response()
                    value = getattr(final_response, "value", None)
                    text = self._extract_final_text(final_response, fallback="".join(streamed_parts))
                else:
                    result = await agent.invoke_messages(dispatch_messages)
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
                    attempt_count=index,
                    skipped_models=skipped_models,
                    failed_attempts=attempts,
                    value=value,
                )
            except Exception as error:
                error_text = str(error).strip() or error.__class__.__name__
                if not streamed_parts:
                    recovered, retry_error = await self._retry_with_compacted_context(
                        model=model,
                        messages=dispatch_messages,
                        error_text=error_text,
                        process_id=process.id,
                        source=source,
                        title=title,
                        tools_enabled=tools_enabled,
                        instructions=dispatch_instructions,
                    )
                    if recovered is not None:
                        value = getattr(recovered, "value", None)
                        text = _serialize_runtime_text(recovered)
                        if text:
                            await on_delta(text)
                        self.last_errors.pop(model.id, None)
                        self.cooldowns.pop(model.id, None)
                        self.process_registry.finish(
                            process.id,
                            status="done",
                            detail=f"Completed with {model.label} after compacting context.",
                            model_id=model.id,
                            model_label=model.label,
                            attempts=index,
                        )
                        return RuntimeTurnResult(
                            text=text,
                            model=model,
                            attempts=attempts,
                            process_id=process.id,
                            attempt_count=index,
                            skipped_models=skipped_models,
                            failed_attempts=attempts,
                            value=value,
                        )
                    if retry_error:
                        error_text = f"{error_text} | Compacted retry failed: {retry_error}"
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
        return RuntimeTurnResult(
            text=summary,
            model=None,
            attempts=attempts,
            process_id=process.id,
            attempt_count=len(attempts),
            skipped_models=skipped_models,
            failed_attempts=attempts,
        )

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
        skipped_models = self._inspect_candidate_skips(lane, selected_model_ids={model.id for model in candidates})
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
            return RuntimeTurnResult(
                text=detail,
                model=None,
                attempts=attempts,
                process_id=process.id,
                skipped_models=skipped_models,
                failed_attempts=attempts,
            )

        for index, model in enumerate(candidates, start=1):
            self.process_registry.update(
                process.id,
                detail=f"Trying {model.label} ({model.deployment}).",
                model_id=model.id,
                model_label=model.label,
                attempts=index,
            )
            try:
                dispatch_messages, dispatch_instructions = self._maybe_preflight_compact_messages(
                    model=model,
                    messages=messages,
                    source=source,
                    title=title,
                    tools_enabled=tools_enabled,
                    instructions=instructions,
                    process_id=process.id,
                )
                self._log_request_payload(
                    model=model,
                    messages=dispatch_messages,
                    source=source,
                    title=title,
                    tools_enabled=tools_enabled,
                    instructions=dispatch_instructions,
                    phase="Invoking runtime",
                )
                agent = await self._get_agent(
                    model,
                    tools_enabled=tools_enabled,
                    instructions=dispatch_instructions,
                )
                result = await agent.invoke_messages(dispatch_messages, response_format=response_format)
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
                    attempt_count=index,
                    skipped_models=skipped_models,
                    failed_attempts=attempts,
                    value=value,
                )
            except Exception as error:
                error_text = str(error).strip() or error.__class__.__name__
                recovered, retry_error = await self._retry_with_compacted_context(
                    model=model,
                    messages=dispatch_messages,
                    error_text=error_text,
                    process_id=process.id,
                    source=source,
                    title=title,
                    response_format=response_format,
                    tools_enabled=tools_enabled,
                    instructions=dispatch_instructions,
                )
                if recovered is not None:
                    value = getattr(recovered, "value", None)
                    text = _serialize_runtime_text(recovered)
                    self.last_errors.pop(model.id, None)
                    self.cooldowns.pop(model.id, None)
                    self.process_registry.finish(
                        process.id,
                        status="done",
                        detail=f"Completed with {model.label} after compacting context.",
                        model_id=model.id,
                        model_label=model.label,
                        attempts=index,
                    )
                    return RuntimeTurnResult(
                        text=text,
                        model=model,
                        attempts=attempts,
                        process_id=process.id,
                        attempt_count=index,
                        skipped_models=skipped_models,
                        failed_attempts=attempts,
                        value=value,
                    )
                if retry_error:
                    error_text = f"{error_text} | Compacted retry failed: {retry_error}"
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
        return RuntimeTurnResult(
            text=summary,
            model=None,
            attempts=attempts,
            process_id=process.id,
            attempt_count=len(attempts),
            skipped_models=skipped_models,
            failed_attempts=attempts,
        )

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

    def _skip_detail_for_model(
        self,
        model: RuntimeModelProfile,
        *,
        now: float,
    ) -> dict[str, str] | None:
        if not model.enabled:
            return {
                "model_id": model.id,
                "model_label": model.label,
                "lane": model.lane,
                "reason": "disabled",
                "reason_label": "Profile disabled",
                "detail": "",
            }
        if not model.deployment.strip():
            return {
                "model_id": model.id,
                "model_label": model.label,
                "lane": model.lane,
                "reason": "no-deployment",
                "reason_label": "No deployment configured",
                "detail": "",
            }
        cooldown_until = self.cooldowns.get(model.id, 0.0)
        if cooldown_until > now:
            last_error = self.last_errors.get(model.id, "").strip()
            return {
                "model_id": model.id,
                "model_label": model.label,
                "lane": model.lane,
                "reason": "cooldown",
                "reason_label": "Cooling down after a previous failure",
                "detail": json.dumps(
                    {
                        "cooldown_until": to_iso_z(datetime.fromtimestamp(cooldown_until, timezone.utc)),
                        "last_error": last_error,
                    },
                    ensure_ascii=False,
                ),
            }
        probe = self._probe_status_for_model(model)
        if not bool(probe["available"]):
            return {
                "model_id": model.id,
                "model_label": model.label,
                "lane": model.lane,
                "reason": "probe-unavailable",
                "reason_label": "Availability probe failed",
                "detail": str(probe["detail"]),
            }
        return None

    def _inspect_candidate_skips(
        self,
        lane: str,
        *,
        selected_model_ids: set[str],
    ) -> list[dict[str, str]]:
        if self.store is None:
            return []
        settings = self.load_settings()
        effective_lane = lane if lane in {"auto", "fast", "slow"} else settings.default_lane
        preferred = [settings.default_lane, "slow", "fast"]
        if effective_lane == "fast":
            preferred = ["fast", "slow"]
        elif effective_lane == "slow":
            preferred = ["slow", "fast"]

        models_by_id = {model.id: model for model in settings.models}
        ordered_ids: list[str] = []
        seen_ids: set[str] = set()

        for wanted in preferred:
            if wanted in models_by_id and wanted not in seen_ids:
                ordered_ids.append(wanted)
                seen_ids.add(wanted)

        for model in settings.models:
            if model.id not in seen_ids:
                ordered_ids.append(model.id)
                seen_ids.add(model.id)

        now = time.time()
        skipped: list[dict[str, str]] = []
        for model_id in ordered_ids:
            if model_id in selected_model_ids:
                continue
            model = models_by_id.get(model_id)
            if model is None:
                continue
            detail = self._skip_detail_for_model(model, now=now)
            if detail is not None:
                skipped.append(detail)
        return skipped

    async def _get_agent(
        self,
        model: RuntimeModelProfile,
        *,
        tools_enabled: bool = True,
        instructions: str | None = None,
    ):
        effective_instructions = instructions.strip() if isinstance(instructions, str) else instructions
        if effective_instructions == "":
            effective_instructions = None
        instruction_key = "default"
        if effective_instructions is not None:
            instruction_key = sha1(effective_instructions.encode("utf-8")).hexdigest()[:12]
        tool_key = "tools" if tools_enabled else "no-tools"
        cache_key = f"{model.id}:{model.deployment}:{tool_key}:{instruction_key}"
        cached = self.agent_cache.get(cache_key)
        if cached is not None:
            return cached

        agent = await create_agent(
            self.mcp_client,
            model_profile=model,
            tools_enabled=tools_enabled,
            instructions=effective_instructions,
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
        available, detail = describe_azure_openai_auth(
            endpoint=endpoint,
            deployment=model.deployment,
            explicit_api_key=api_key,
        )
        return {"available": available, "detail": detail}

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
