"""Cognitive agent configuration based on Microsoft Agent Framework."""

import hashlib
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import aiohttp
from agent_framework import Agent, Message, tool
from agent_framework.azure import AzureOpenAIChatClient
from agent_framework.openai import OpenAIChatClient
from pydantic import BaseModel

from ..api.skill_services import (
    invoke_remote_agent,
    list_enabled_remote_agent_runtime_specs,
)
from ..azure_auth import (
    get_azure_openai_token_provider,
    get_default_azure_credential,
    resolve_azure_openai_auth_mode,
)
from ..config import derive_fast_azure_openai_endpoint
from ..foundry_url import (
    is_foundry_endpoint,
    normalize_azure_openai_endpoint,
    normalize_foundry_base_url,
)
from ..soul.system_prompt import AZULCLAW_SYSTEM_PROMPT
from .mcp_plugin import MCPToolsPlugin

LOGGER = logging.getLogger(__name__)
MAX_DYNAMIC_TOOL_NAME_LENGTH = 64

try:
    from openai.lib._parsing._completions import (
        type_to_response_format_param as _openai_response_format_param,
    )
except Exception:  # pragma: no cover - depends on installed OpenAI SDK internals
    _openai_response_format_param = None


def _require_env(var_name: str) -> str:
    """Gets a required environment variable or raises an explicit error."""
    value = os.environ.get(var_name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable for AI is missing: {var_name}")
    return value


def _normalize_openai_base_url(raw_base_url: str) -> str:
    """Normalises OpenAI-compatible URLs and ensures a /v1 suffix."""
    base_url = (raw_base_url or "").strip().rstrip("/")
    if not base_url:
        return "http://127.0.0.1:11434/v1"
    if base_url.endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def _foundry_chat_url(endpoint: str) -> str:
    """Builds the correct /v1/chat/completions URL for an AI Foundry project endpoint."""
    parsed = urlparse(endpoint)
    path = parsed.path
    v1_idx = path.find("/v1")
    clean_path = path[: v1_idx + 3] if v1_idx != -1 else ""
    return f"{parsed.scheme}://{parsed.netloc}{clean_path}/chat/completions"


def _response_format_param(response_format):
    """Builds a chat-completions response_format payload without requiring private SDK APIs."""
    if response_format is None or isinstance(response_format, dict):
        return response_format
    if _openai_response_format_param is not None:
        return _openai_response_format_param(response_format)
    if (
        isinstance(response_format, type)
        and issubclass(response_format, BaseModel)
    ):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": response_format.__name__,
                "schema": response_format.model_json_schema(),
            },
        }
    return response_format


def _stringify_result_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _compose_instructions(instructions: str | None) -> str:
    if instructions is None:
        return AZULCLAW_SYSTEM_PROMPT
    scoped = instructions.strip()
    if not scoped:
        return AZULCLAW_SYSTEM_PROMPT
    return f"{AZULCLAW_SYSTEM_PROMPT}\n\nTask-specific instructions:\n{scoped}"


class _Result:
    """Minimal container for backwards compatibility with the previous contract."""

    def __init__(self, value: Any):
        self.value = value

    @property
    def text(self) -> str:
        return _stringify_result_value(self.value)

    def __str__(self) -> str:
        return self.text


class _FoundryStream:
    """Async iterator that yields text chunks from an AI Foundry SSE stream."""

    def __init__(self, resp: aiohttp.ClientResponse):
        self._resp = resp
        self._final_text = ""

    def __aiter__(self):
        return self

    async def __anext__(self):
        async for line in self._resp.content:
            text = line.decode("utf-8", errors="replace").strip()
            if not text or not text.startswith("data:"):
                continue
            payload = text[5:].strip()
            if payload == "[DONE]":
                raise StopAsyncIteration
            try:
                chunk = json.loads(payload)
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content") or ""
                if content:
                    self._final_text += content
                    return _StreamChunk(content)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
        raise StopAsyncIteration

    async def get_final_response(self) -> _Result:
        return _Result(self._final_text)


class _StreamChunk:
    def __init__(self, text: str):
        self.text = text


class FoundryAgent:
    """Direct aiohttp agent for AI Foundry /v1/ endpoints."""

    def __init__(
        self,
        url: str,
        api_key: str | None,
        model: str,
        instructions: str = "",
        token_provider=None,
    ):
        self._url = url
        self._api_key = (api_key or "").strip()
        self._token_provider = token_provider
        self._model = model
        self._instructions = instructions.strip()

    def _headers(self) -> dict[str, str]:
        if self._token_provider is not None:
            token = self._token_provider()
            return {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _messages_payload(self, messages: list[Message]) -> list[dict]:
        result = []
        if self._instructions:
            result.append({"role": "system", "content": self._instructions})
        for message in messages:
            serialized_parts: list[dict[str, Any]] = []
            plain_text_parts: list[str] = []
            for content in message.contents:
                content_type = getattr(content, "type", None)
                if content_type == "text" and content.text:
                    serialized_parts.append({"type": "text", "text": content.text})
                    plain_text_parts.append(content.text)
                    continue
                if content_type in {"data", "uri"}:
                    uri = getattr(content, "uri", "") or ""
                    media_type = str(getattr(content, "media_type", "") or "").lower()
                    if media_type.startswith("image/") and uri:
                        serialized_parts.append({"type": "image_url", "image_url": {"url": uri}})
            if serialized_parts and any(part["type"] == "image_url" for part in serialized_parts):
                result.append({"role": message.role, "content": serialized_parts})
            else:
                result.append({"role": message.role, "content": "".join(plain_text_parts)})
        return result

    async def invoke_messages(self, messages: list[Message], response_format=None) -> _Result:
        payload = {
            "model": self._model,
            "messages": self._messages_payload(messages),
            "max_completion_tokens": 1024,
        }
        if response_format is not None:
            payload["response_format"] = _response_format_param(response_format)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._url,
                json=payload,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"Foundry returned {resp.status}: {body[:300]}")
                data = await resp.json()
        content = data["choices"][0]["message"].get("content") or ""
        if (
            response_format is not None
            and isinstance(response_format, type)
            and issubclass(response_format, BaseModel)
        ):
            return _Result(response_format.model_validate_json(content))
        return _Result(content)

    def stream_messages(self, messages: list[Message]):
        """Returns a coroutine that resolves to an async iterator of stream chunks."""
        return self._stream(messages)

    async def _stream(self, messages: list[Message]):
        payload = {
            "model": self._model,
            "messages": self._messages_payload(messages),
            "max_completion_tokens": 2048,
            "stream": True,
        }
        session = aiohttp.ClientSession()
        resp = await session.post(
            self._url,
            json=payload,
            headers=self._headers(),
            timeout=aiohttp.ClientTimeout(total=60),
        )
        if resp.status != 200:
            body = await resp.text()
            await session.close()
            raise RuntimeError(f"Foundry stream returned {resp.status}: {body[:300]}")
        return _FoundryStream(resp)


class AzulAgent:
    """Adapter that exposes structured inference over Agent Framework."""

    def __init__(self, agent: Agent):
        self.agent = agent

    async def invoke_messages(self, messages: list[Message], response_format=None) -> _Result:
        """Runs an inference and normalises its output to _Result."""
        options = {"response_format": response_format} if response_format is not None else None
        response = await self.agent.run(messages, options=options)
        text = getattr(response, "text", None)
        value = getattr(response, "value", None)
        if value is not None and not isinstance(value, str):
            return _Result(value)
        if isinstance(text, str) and text.strip():
            return _Result(text)
        return _Result(value if isinstance(value, str) else str(response))

    async def invoke_prompt(self, prompt: str) -> _Result:
        """Backwards compatibility for legacy single-prompt calls."""
        return await self.invoke_messages([Message(role="user", contents=prompt)])

    def stream_messages(self, messages: list[Message]):
        """Returns the native Agent Framework stream for incremental responses."""
        return self.agent.run(messages, stream=True)


def _normalize_dynamic_tool_name(skill_id: str, tool_name: str, used_names: set[str]) -> str:
    skill_slug = re.sub(r"[^a-z0-9]+", "_", skill_id.lower()).strip("_")
    tool_slug = re.sub(r"[^a-z0-9]+", "_", tool_name.lower()).strip("_")
    candidate = f"skill_{skill_slug}__{tool_slug}".strip("_")

    def _bounded_name(base: str, suffix: str = "") -> str:
        limit = MAX_DYNAMIC_TOOL_NAME_LENGTH - len(suffix)
        if len(base) <= limit:
            return f"{base}{suffix}"
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
        keep = max(1, limit - len(digest) - 1)
        truncated = base[:keep].rstrip("_-") or base[:keep]
        return f"{truncated}_{digest}{suffix}"

    candidate = _bounded_name(candidate)
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate
    index = 2
    while _bounded_name(candidate, f"_{index}") in used_names:
        index += 1
    unique = _bounded_name(candidate, f"_{index}")
    used_names.add(unique)
    return unique


def _tool_schema_summary(input_schema: Any) -> str:
    if not isinstance(input_schema, dict):
        return "Pass a JSON object string for the tool arguments."
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    if not isinstance(properties, dict) or not properties:
        return "Pass a JSON object string for the tool arguments."
    parts = []
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        type_name = str(schema.get("type", "value"))
        suffix = " required" if name in required else ""
        parts.append(f"{name}:{type_name}{suffix}")
    joined = ", ".join(parts[:8])
    return f"Pass arguments_json as a JSON object. Expected fields: {joined}." if joined else "Pass arguments_json as a JSON object."


async def _build_tools(mcp_client):
    """Builds the agent tool catalogue on top of MCPToolsPlugin."""
    plugin = MCPToolsPlugin(mcp_client)
    tools = []
    used_names = {"list_files", "read_file", "move_file", "list_skill_tools", "list_remote_agents"}

    @tool(
        name="list_files",
        description="Lists files inside the user's secure workspace.",
    )
    async def list_files(path: str = ".") -> str:
        return await plugin.list_files(path)

    @tool(
        name="read_file",
        description="Reads a text file inside the secure workspace.",
    )
    async def read_file(path: str = "") -> str:
        return await plugin.read_file(path)

    @tool(
        name="move_file",
        description="Moves or renames files inside the secure workspace.",
    )
    async def move_file(source: str = "", destination: str = "") -> str:
        return await plugin.move_file(source, destination)

    tools.extend([list_files, read_file, move_file])

    if hasattr(mcp_client, "list_tool_catalog"):
        @tool(
            name="list_skill_tools",
            description="Lists Marketplace MCP tools connected from enabled local skills.",
        )
        async def list_skill_tools(skill_id: str = "") -> str:
            return await plugin.list_skill_tools(skill_id)

        tools.append(list_skill_tools)

        try:
            catalog = await mcp_client.list_tool_catalog(include_primary=False)
        except Exception as error:
            LOGGER.warning("Could not load Marketplace MCP tool catalog: %s", error)
            catalog = []

        for item in catalog:
            skill_id = str(item.get("skill_id", "")).strip()
            tool_name = str(item.get("tool_name", "")).strip()
            if not skill_id or not tool_name:
                continue
            dynamic_name = _normalize_dynamic_tool_name(skill_id, tool_name, used_names)
            description = str(item.get("description", "")).strip() or f"Runs MCP tool {tool_name}."
            schema_summary = _tool_schema_summary(item.get("input_schema"))

            def _make_invoke_skill_tool(bound_skill_id: str, bound_tool_name: str):
                async def invoke_skill_tool(arguments_json: str = "{}") -> str:
                    return await plugin.call_skill_tool(bound_skill_id, bound_tool_name, arguments_json)
                return invoke_skill_tool

            tools.append(
                tool(
                    name=dynamic_name,
                    description=f"[{skill_id}] {description} {schema_summary}",
                )(_make_invoke_skill_tool(skill_id, tool_name))
            )

    @tool(
        name="list_remote_agents",
        description="Lists enabled Marketplace remote-agent skills and their HTTPS endpoints.",
    )
    async def list_remote_agents() -> str:
        items = list_enabled_remote_agent_runtime_specs()
        if not items:
            return "No Marketplace remote-agent skills are enabled."
        lines = []
        for item in items:
            lines.append(
                f"{item.get('skill_id', '')}: {item.get('endpoint', '')} - {item.get('message', '')}".strip()
            )
        return "\n".join(lines)

    tools.append(list_remote_agents)

    for item in list_enabled_remote_agent_runtime_specs():
        skill_id = str(item.get("skill_id", "")).strip()
        if not skill_id:
            continue
        dynamic_name = _normalize_dynamic_tool_name(skill_id, "remote_agent", used_names)
        description = str(item.get("description", "")).strip() or "Calls the configured remote agent."

        def _make_remote_agent_tool(bound_skill_id: str):
            async def invoke_bound_remote_agent(prompt: str = "", context_json: str = "{}") -> str:
                try:
                    context = json.loads(context_json or "{}")
                except json.JSONDecodeError as error:
                    raise ValueError(f"context_json must be valid JSON: {error}") from error
                if not isinstance(context, dict):
                    raise ValueError("context_json must decode to a JSON object.")
                return await invoke_remote_agent(bound_skill_id, prompt, context)
            return invoke_bound_remote_agent

        tools.append(
            tool(
                name=dynamic_name,
                description=f"[{skill_id}] {description} Pass prompt plus optional context_json as a JSON object.",
            )(_make_remote_agent_tool(skill_id))
        )

    return tools


async def create_agent(
    mcp_client,
    model_profile=None,
    *,
    tools_enabled: bool = True,
    instructions: str | None = None,
):
    """Creates the agent with the appropriate client and MCP tools."""
    provider = getattr(model_profile, "provider", "azure")
    deployment_name = (
        getattr(model_profile, "deployment", "").strip()
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o").strip()
    )
    lane = getattr(model_profile, "lane", "").strip().lower()
    effective_instructions = _compose_instructions(instructions)
    tools = await _build_tools(mcp_client) if tools_enabled else []

    if provider == "openai":
        base_url = _normalize_openai_base_url(
            os.environ.get("AZUL_FAST_OLLAMA_BASE_URL", "").strip()
            or os.environ.get("OLLAMA_HOST", "").strip()
            or "http://127.0.0.1:11434/v1"
        )
        api_key = os.environ.get("AZUL_FAST_OLLAMA_API_KEY", "").strip() or "ollama"
        chat_client = OpenAIChatClient(
            model_id=deployment_name,
            api_key=api_key,
            base_url=base_url,
        )
        agent = Agent(
            client=chat_client,
            instructions=effective_instructions,
            tools=tools,
        )
        return AzulAgent(agent)

    if lane == "fast":
        endpoint = (
            os.environ.get("AZURE_OPENAI_FAST_ENDPOINT", "").strip()
            or _require_env("AZURE_OPENAI_ENDPOINT")
        )
        api_key = (
            os.environ.get("AZURE_OPENAI_FAST_API_KEY", "").strip()
            or os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        )
        auth_mode = resolve_azure_openai_auth_mode(api_key)
        token_provider = get_azure_openai_token_provider() if auth_mode == "entra" else None
        if auth_mode != "entra" and not api_key:
            api_key = _require_env("AZURE_OPENAI_API_KEY")
        url = _foundry_chat_url(derive_fast_azure_openai_endpoint(endpoint))
        LOGGER.debug("[Kernel] Fast lane using FoundryAgent: %s (%s)", url, deployment_name)
        return FoundryAgent(
            url=url,
            api_key=api_key,
            model=deployment_name,
            instructions=effective_instructions,
            token_provider=token_provider,
        )

    endpoint = (
        os.environ.get("AZURE_OPENAI_SLOW_ENDPOINT", "").strip()
        or _require_env("AZURE_OPENAI_ENDPOINT")
    )
    api_key = (
        os.environ.get("AZURE_OPENAI_SLOW_API_KEY", "").strip()
        or os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    )
    auth_mode = resolve_azure_openai_auth_mode(api_key)
    api_version = (
        os.environ.get("AZURE_OPENAI_SLOW_API_VERSION", "").strip()
        or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()
    )

    if is_foundry_endpoint(endpoint):
        kwargs = {
            "model_id": deployment_name,
            "base_url": normalize_foundry_base_url(endpoint),
        }
        if auth_mode == "entra":
            kwargs["api_key"] = get_azure_openai_token_provider()
        else:
            kwargs["api_key"] = api_key or _require_env("AZURE_OPENAI_API_KEY")
        chat_client = OpenAIChatClient(**kwargs)
    else:
        kwargs = {
            "deployment_name": deployment_name,
            "endpoint": normalize_azure_openai_endpoint(endpoint),
            "api_version": api_version,
        }
        if auth_mode == "entra":
            kwargs["credential"] = get_default_azure_credential()
        else:
            kwargs["api_key"] = api_key or _require_env("AZURE_OPENAI_API_KEY")
        chat_client = AzureOpenAIChatClient(**kwargs)

    agent = Agent(
        client=chat_client,
        instructions=effective_instructions,
        tools=tools,
    )
    return AzulAgent(agent)
