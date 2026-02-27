"""Cliente MCP para conectar AzulBrain con AzulHands mediante STDIO."""

import contextlib
import sys
from typing import Any

import colorama
from colorama import Fore, Style
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

colorama.init()

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
        print(
            Fore.BLUE
            + "[INFO] Conectando el Cerebro con AzulHands (MCP Server)..."
            + Style.RESET_ALL
        )
        try:
            stdio_transport = await self._exit_stack.enter_async_context(
                stdio_client(self.server_parameters)
            )
            self.read_stream, self.write_stream = stdio_transport
            self.session = await self._exit_stack.enter_async_context(
                ClientSession(self.read_stream, self.write_stream)
            )
            await self.session.initialize()
            print(
                Fore.GREEN
                + "[OK] Conexión MCP Establecida. AzulClaw ahora tiene 'Manos'."
                + Style.RESET_ALL
            )
        except Exception as error:
            print(
                Fore.RED
                + f"[ERROR] Error al conectar con AzulHands: {error}"
                + Style.RESET_ALL
            )
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

        print(
            Fore.MAGENTA
            + f"[REQ] Cerebro pide ejecutar: {tool_name} con {arguments}"
            + Style.RESET_ALL
        )
        result = await self.session.call_tool(tool_name, arguments)
        return result

    async def cleanup(self):
        """Cierra sesión y recursos de transporte del proceso MCP hijo."""
        print("Cerrando conexión MCP y deteniendo AzulHands...")
        await self._exit_stack.aclose()