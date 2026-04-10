"""Cliente MCP para conectar AzulBrain con AzulHands mediante STDIO."""

import contextlib
import logging
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

LOGGER = logging.getLogger(__name__)


def _format_tool_names(tools: list[Any]) -> str:
    """Devuelve un listado legible con los nombres de tools MCP publicados."""
    names = [getattr(tool, "name", str(tool)) for tool in tools]
    return ", ".join(names) if names else "sin tools"

class AzulHandsClient:
    """
    Cliente MCP que abre un proceso hijo de AzulHands y expone operaciones remotas
    de herramientas de filesystem seguro.
    """

    def __init__(self, server_script_path: str):
        """Configura parámetros de arranque del servidor MCP hijo."""
        self.server_parameters = StdioServerParameters(
            command=sys.executable,
            args=[server_script_path],
            env=None,
        )
        self.session: ClientSession | None = None
        self._exit_stack = contextlib.AsyncExitStack()
        self.read_stream = None
        self.write_stream = None

    async def connect(self):
        """Inicia transporte STDIO, crea sesión MCP y ejecuta initialize()."""
        LOGGER.info("Conectando el Cerebro con AzulHands (MCP Server)...")
        try:
            stdio_transport = await self._exit_stack.enter_async_context(
                stdio_client(self.server_parameters)
            )
            self.read_stream, self.write_stream = stdio_transport
            self.session = await self._exit_stack.enter_async_context(
                ClientSession(self.read_stream, self.write_stream)
            )
            await self.session.initialize()
            LOGGER.info(
                "Conexión MCP establecida. AzulClaw ahora tiene acceso a herramientas."
            )
        except Exception as error:
            LOGGER.error("Error al conectar con AzulHands: %s", error)
            raise

    async def list_available_tools(self) -> list[Any]:
        """Recupera el catálogo de herramientas que publica el servidor MCP."""
        if not self.session:
            raise RuntimeError("No hay sesión MCP activa.")

        response = await self.session.list_tools()
        return response.tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Invoca una herramienta MCP remota con argumentos serializables."""
        if not self.session:
            raise RuntimeError("No hay sesión MCP activa.")

        LOGGER.info("Ejecutando tool MCP '%s' con argumentos %s", tool_name, arguments)
        result = await self.session.call_tool(tool_name, arguments)
        return result

    async def cleanup(self):
        """Cierra sesión y recursos de transporte del proceso MCP hijo."""
        LOGGER.info("Cerrando conexión MCP y deteniendo AzulHands...")
        await self._exit_stack.aclose()


async def _run_smoke_test() -> None:
    """Ejecuta una comprobación mínima del servidor MCP local."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    server_script_path = Path(__file__).resolve().parents[1] / "azul_hands_mcp" / "mcp_server.py"
    client = AzulHandsClient(str(server_script_path))

    try:
        await client.connect()
        tools = await client.list_available_tools()
        LOGGER.info("Tools MCP disponibles: %s", _format_tool_names(tools))

        workspace_listing = await client.call_tool("list_workspace_files", {"path": "."})
        LOGGER.info("Respuesta de list_workspace_files: %s", workspace_listing)

        denied_result = await client.call_tool(
            "read_safe_file",
            {"path": "../../../../../Windows/System32/drivers/etc/hosts"},
        )
        LOGGER.info("Bloqueo de path traversal: %s", denied_result)
    finally:
        await client.cleanup()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_run_smoke_test())
