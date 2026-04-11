"""MCP client for connecting AzulBrain to AzulHands via STDIO."""

import contextlib
import logging
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

LOGGER = logging.getLogger(__name__)


def _format_tool_names(tools: list[Any]) -> str:
    """Returns a readable list of published MCP tool names."""
    names = [getattr(tool, "name", str(tool)) for tool in tools]
    return ", ".join(names) if names else "no tools"

class AzulHandsClient:
    """
    MCP client that spawns an AzulHands child process and exposes remote
    secure filesystem tool operations.
    """

    def __init__(self, server_script_path: str):
        """Configures startup parameters for the MCP child server."""
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
        """Starts the STDIO transport, creates an MCP session, and runs initialize()."""
        LOGGER.info("Connecting AzulBrain to AzulHands (MCP Server)...")
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
                "MCP connection established. AzulClaw now has access to tools."
            )
        except Exception as error:
            LOGGER.error("Error connecting to AzulHands: %s", error)
            raise

    async def list_available_tools(self) -> list[Any]:
        """Retrieves the tool catalogue published by the MCP server."""
        if not self.session:
            raise RuntimeError("No active MCP session.")

        response = await self.session.list_tools()
        return response.tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Invokes a remote MCP tool with serialisable arguments."""
        if not self.session:
            raise RuntimeError("No active MCP session.")

        LOGGER.info("Executing MCP tool '%s' with arguments %s", tool_name, arguments)
        result = await self.session.call_tool(tool_name, arguments)
        return result

    async def cleanup(self):
        """Closes the session and transport resources of the MCP child process."""
        LOGGER.info("Closing MCP connection and stopping AzulHands...")
        await self._exit_stack.aclose()


async def _run_smoke_test() -> None:
    """Runs a minimal smoke test against the local MCP server."""
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
