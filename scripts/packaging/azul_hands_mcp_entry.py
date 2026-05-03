"""PyInstaller entry point for the AzulHands MCP server."""

import asyncio

from azul_backend.azul_hands_mcp.mcp_server import main


if __name__ == "__main__":
    asyncio.run(main())
