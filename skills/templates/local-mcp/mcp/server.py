"""Example stdio MCP runtime for a local AzulClaw skill."""

from __future__ import annotations

import asyncio
import json

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

app = Server("example-local-mcp-skill")


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "echo_local_skill":
        message = str(arguments.get("message", "")).strip() or "Hello from the local MCP skill."
        return [types.TextContent(type="text", text=json.dumps({"message": message}, indent=2))]
    raise ValueError(f"Unknown tool: {name}")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="echo_local_skill",
            description="Echoes a message from the example local MCP skill.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                },
                "required": [],
            },
        )
    ]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
