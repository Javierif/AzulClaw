import asyncio
import sys
from collections.abc import AsyncIterator
from typing import Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import contextlib
import colorama
from colorama import Fore, Style

colorama.init()

class AzulHandsClient:
    """
    Cliente MCP que se conecta al proceso AzulHands (el servidor que tiene acceso al Desktop).
    El Cerebro (Agent) instanciará esto para poder interactuar con el mundo físico.
    """
    
    def __init__(self, server_script_path: str):
        self.server_parameters = StdioServerParameters(
            command=sys.executable,
            args=[server_script_path],
            env=None
        )
        self.session: ClientSession | None = None
        self._exit_stack = contextlib.AsyncExitStack()

    async def connect(self):
        """Inicia el proceso hijo del servidor MCP y establece la sesión."""
        print(Fore.BLUE + "[INFO] Conectando el Cerebro con AzulHands (MCP Server)..." + Style.RESET_ALL)
        try:
            stdio_transport = await self._exit_stack.enter_async_context(stdio_client(self.server_parameters))
            self.read_stream, self.write_stream = stdio_transport
            self.session = await self._exit_stack.enter_async_context(ClientSession(self.read_stream, self.write_stream))
            
            await self.session.initialize()
            print(Fore.GREEN + "[OK] Conexión MCP Establecida. AzulClaw ahora tiene 'Manos'." + Style.RESET_ALL)
            
        except Exception as e:
            print(Fore.RED + f"[ERROR] Error al conectar con AzulHands: {e}" + Style.RESET_ALL)
            raise

    async def list_available_tools(self) -> list[Any]:
        """Obtiene el catálogo de herramientas que AzulHands nos permite usar hoy."""
        if not self.session:
            raise RuntimeError("No hay sesión MCP activa.")
            
        response = await self.session.list_tools()
        return response.tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Pide a AzulHands que ejecute una herramienta física."""
        if not self.session:
            raise RuntimeError("No hay sesión MCP activa.")
            
        print(Fore.MAGENTA + f"[REQ] Cerebro pide ejecutar: {tool_name} con {arguments}" + Style.RESET_ALL)
        result = await self.session.call_tool(tool_name, arguments)
        return result

    async def cleanup(self):
        """Cierra el proceso hijo limpiamente."""
        print("Cerrando conexión MCP y deteniendo AzulHands...")
        await self._exit_stack.aclose()


# Código de prueba rápida para validar que el IPC funciona
async def run_smoke_test():
    client = AzulHandsClient("../azul_hands_mcp/mcp_server.py")
    await client.connect()
    
    try:
        # Preguntar qué podemos hacer
        tools = await client.list_available_tools()
        print(f"\nHerramientas disponibles en la cuarentena:")
        for t in tools:
            print(f"- {t.name}: {t.description}")
            
        # Intentar listar archivos en la raíz del workspace
        print("\nPrueba: Listando archivos en el Desktop Workspace...")
        result = await client.call_tool("list_workspace_files", {"path": "."})
        print(result.content[0].text)
        
        # Intentar un Path Traversal Malicioso (debería ser bloqueado por el servidor)
        print("\nPrueba (Seguridad): Intentando leer C:\\Windows\\System32...")
        try:
            bad_result = await client.call_tool("list_workspace_files", {"path": "../../../../../Windows/System32"})
            print(Fore.YELLOW + bad_result.content[0].text + Style.RESET_ALL)
        except Exception as e:
            print(e)
            
    finally:
        await client.cleanup()


if __name__ == "__main__":
    import os
    # Asegurar ejecución en el cwd correcto durante la prueba
    # CWD debe ser azul_brain
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    asyncio.run(run_smoke_test())
