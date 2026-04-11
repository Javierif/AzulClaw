"""Cognitive agent configuration based on Microsoft Agent Framework."""

import os
from typing import Any

from agent_framework import Agent, Message, tool
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from ..soul.system_prompt import AZULCLAW_SYSTEM_PROMPT
from .mcp_plugin import MCPToolsPlugin


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



class _Result:
    """Minimal container for backwards compatibility with the previous contract."""

    def __init__(self, value: Any):
        self.value = value


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
            deployment_name,
            api_key=api_key,
            base_url=base_url,
        )
    else:
        endpoint_var = "AZURE_OPENAI_FAST_ENDPOINT" if lane == "fast" else "AZURE_OPENAI_SLOW_ENDPOINT"
        api_key_var = "AZURE_OPENAI_FAST_API_KEY" if lane == "fast" else "AZURE_OPENAI_SLOW_API_KEY"
        api_version_var = (
            "AZURE_OPENAI_FAST_API_VERSION" if lane == "fast" else "AZURE_OPENAI_SLOW_API_VERSION"
        )

        endpoint = (
            os.environ.get(endpoint_var, "").strip()
            or _require_env("AZURE_OPENAI_ENDPOINT")
        )
        api_key = (
            os.environ.get(api_key_var, "").strip()
            or _require_env("AZURE_OPENAI_API_KEY")
        )
        api_version = (
            os.environ.get(api_version_var, "").strip()
            or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()
        )

        chat_client = OpenAIChatCompletionClient(
            deployment_name,
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
        )

    agent = Agent(
        client=chat_client,
        instructions=AZULCLAW_SYSTEM_PROMPT,
        tools=_build_tools(mcp_client),
    )
    return AzulAgent(agent)
