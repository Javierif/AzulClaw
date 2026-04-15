"""Cognitive agent configuration based on Microsoft Agent Framework."""

import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import aiohttp
from agent_framework import Agent, Message, tool
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from ..foundry_url import (
    is_foundry_endpoint,
    normalize_azure_openai_endpoint,
    normalize_foundry_base_url,
)
from ..soul.system_prompt import AZULCLAW_SYSTEM_PROMPT
from .mcp_plugin import MCPToolsPlugin

LOGGER = logging.getLogger(__name__)


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


class _Result:
    """Minimal container for backwards compatibility with the previous contract."""

    def __init__(self, value: Any):
        self.value = value


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

    def __init__(self, url: str, api_key: str, model: str):
        self._url = url
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._model = model

    def _messages_payload(self, messages: list[Message]) -> list[dict]:
        result = []
        for message in messages:
            parts = [
                content.text
                for content in message.contents
                if getattr(content, "type", None) == "text" and content.text
            ]
            result.append({"role": message.role, "content": "".join(parts)})
        return result

    async def invoke_messages(self, messages: list[Message]) -> _Result:
        payload = {
            "model": self._model,
            "messages": self._messages_payload(messages),
            "max_completion_tokens": 1024,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._url,
                json=payload,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"Foundry returned {resp.status}: {body[:300]}")
                data = await resp.json()
        content = data["choices"][0]["message"].get("content") or ""
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
            headers=self._headers,
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

    async def invoke_messages(self, messages: list[Message]) -> _Result:
        """Runs an inference and normalises its output to _Result."""
        response = await self.agent.run(messages)
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return _Result(text)
        value = getattr(response, "value", None)
        return _Result(value if isinstance(value, str) else str(response))

    async def invoke_prompt(self, prompt: str) -> _Result:
        """Backwards compatibility for legacy single-prompt calls."""
        return await self.invoke_messages([Message(role="user", contents=prompt)])

    def stream_messages(self, messages: list[Message]):
        """Returns the native Agent Framework stream for incremental responses."""
        return self.agent.run(messages, stream=True)


def _build_tools(mcp_client):
    """Builds the agent tool catalogue on top of MCPToolsPlugin."""
    plugin = MCPToolsPlugin(mcp_client)

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

    return [list_files, read_file, move_file]


async def create_agent(mcp_client, model_profile=None):
    """Creates the agent with the appropriate client and MCP tools."""
    provider = getattr(model_profile, "provider", "azure")
    deployment_name = (
        getattr(model_profile, "deployment", "").strip()
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o").strip()
    )
    lane = getattr(model_profile, "lane", "").strip().lower()

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
            instructions=AZULCLAW_SYSTEM_PROMPT,
            tools=_build_tools(mcp_client),
        )
        return AzulAgent(agent)

    if lane == "fast":
        endpoint = (
            os.environ.get("AZURE_OPENAI_FAST_ENDPOINT", "").strip()
            or _require_env("AZURE_OPENAI_ENDPOINT")
        )
        api_key = (
            os.environ.get("AZURE_OPENAI_FAST_API_KEY", "").strip()
            or _require_env("AZURE_OPENAI_API_KEY")
        )
        url = _foundry_chat_url(endpoint)
        LOGGER.debug("[Kernel] Fast lane using FoundryAgent: %s (%s)", url, deployment_name)
        return FoundryAgent(url=url, api_key=api_key, model=deployment_name)

    endpoint = (
        os.environ.get("AZURE_OPENAI_SLOW_ENDPOINT", "").strip()
        or _require_env("AZURE_OPENAI_ENDPOINT")
    )
    api_key = (
        os.environ.get("AZURE_OPENAI_SLOW_API_KEY", "").strip()
        or _require_env("AZURE_OPENAI_API_KEY")
    )
    api_version = (
        os.environ.get("AZURE_OPENAI_SLOW_API_VERSION", "").strip()
        or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()
    )

    if is_foundry_endpoint(endpoint):
        chat_client = OpenAIChatClient(
            model_id=deployment_name,
            api_key=api_key,
            base_url=normalize_foundry_base_url(endpoint),
        )
    else:
        chat_client = OpenAIChatCompletionClient(
            model=deployment_name,
            api_key=api_key,
            azure_endpoint=normalize_azure_openai_endpoint(endpoint),
            api_version=api_version,
        )

    agent = Agent(
        client=chat_client,
        instructions=AZULCLAW_SYSTEM_PROMPT,
        tools=_build_tools(mcp_client),
    )
    return AzulAgent(agent)
