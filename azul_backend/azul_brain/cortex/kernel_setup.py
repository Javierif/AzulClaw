"""Configuracion del agente cognitivo basado en Microsoft Agent Framework."""

import os
from typing import Any

from agent_framework import Agent, Message, tool
from agent_framework.azure import AzureOpenAIChatClient

from ..soul.system_prompt import AZULCLAW_SYSTEM_PROMPT
from .mcp_plugin import MCPToolsPlugin


def _require_env(var_name: str) -> str:
    """Obtiene una variable de entorno requerida o lanza error explicito."""
    value = os.environ.get(var_name, "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno requerida para IA: {var_name}")
    return value


class _Result:
    """Contenedor minimo para mantener compatibilidad con el contrato previo."""

    def __init__(self, value: Any):
        self.value = value


class AzulAgent:
    """Adapter que expone inferencia estructurada sobre Agent Framework."""

    def __init__(self, agent: Agent):
        self.agent = agent

    async def invoke_messages(self, messages: list[Message]) -> _Result:
        """Ejecuta una inferencia y normaliza su salida a _Result."""
        response = await self.agent.run(messages)
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return _Result(text)
        value = getattr(response, "value", None)
        return _Result(value if isinstance(value, str) else str(response))

    async def invoke_prompt(self, prompt: str) -> _Result:
        """Compatibilidad con llamadas antiguas basadas en un solo prompt."""
        return await self.invoke_messages([Message(role="user", text=prompt)])


def _build_tools(mcp_client):
    """Construye el catalogo de tools del agente sobre MCPToolsPlugin."""
    plugin = MCPToolsPlugin(mcp_client)

    @tool(
        name="listar_archivos",
        description="Lista archivos dentro del workspace seguro del usuario.",
    )
    async def listar_archivos(path: str = ".") -> str:
        return await plugin.list_files(path)

    @tool(
        name="leer_archivo",
        description="Lee un archivo de texto dentro del workspace seguro.",
    )
    async def leer_archivo(path: str = "") -> str:
        return await plugin.read_file(path)

    @tool(
        name="mover_archivo",
        description="Mueve o renombra archivos dentro del workspace seguro.",
    )
    async def mover_archivo(source: str = "", destination: str = "") -> str:
        return await plugin.move_file(source, destination)

    return [listar_archivos, leer_archivo, mover_archivo]


async def create_agent(mcp_client):
    """Crea el agente con cliente Azure OpenAI y tools MCP."""
    endpoint = _require_env("AZURE_OPENAI_ENDPOINT")
    api_key = _require_env("AZURE_OPENAI_API_KEY")
    deployment_name = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()

    chat_client = AzureOpenAIChatClient(
        api_key=api_key,
        endpoint=endpoint,
        deployment_name=deployment_name,
        api_version=api_version,
    )

    agent = Agent(
        client=chat_client,
        instructions=AZULCLAW_SYSTEM_PROMPT,
        tools=_build_tools(mcp_client),
    )
    return AzulAgent(agent)
