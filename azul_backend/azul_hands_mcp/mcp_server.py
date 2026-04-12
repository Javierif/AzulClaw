"""AzulHands MCP server with secure workspace filesystem tools."""

import asyncio
import os
import shutil
import sys
from pathlib import Path

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

if __package__ in (None, ""):
    package_root = Path(__file__).resolve().parent
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    from path_validator import PathValidator, SecurityError
else:
    from .path_validator import PathValidator, SecurityError

# MCP server registered on the STDIO channel.
app = Server("azul-hands-mcp")

# Secure workspace (sandbox) for file operations.
# Keep in sync with HatchingProfile.workspace_root (override: AZUL_WORKSPACE_ROOT).
def _resolve_workspace_dir() -> str:
    override = os.environ.get("AZUL_WORKSPACE_ROOT", "").strip()
    if override:
        return override
    return str(Path.home() / "Documents" / "dev" / "AzulWorkspace")


WORKSPACE_DIR = _resolve_workspace_dir()

# Match desktop API: default folders + WORKSPACE.md (repo root on path for import).
try:
    _repo_root = Path(__file__).resolve().parents[2]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))
    from azul_backend.workspace_layout import ensure_workspace_scaffold

    ensure_workspace_scaffold(Path(WORKSPACE_DIR))
except Exception:
    Path(WORKSPACE_DIR).mkdir(parents=True, exist_ok=True)

# Path validator for blocking path traversal attempts.
validator = PathValidator(WORKSPACE_DIR)

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Main router that executes safe tools by name."""
    if name == "list_workspace_files":
        relative_path = arguments.get("path", "")
        try:
            safe_dir = validator.safe_resolve(relative_path)
            if not safe_dir.exists() or not safe_dir.is_dir():
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error: directory {relative_path} does not exist or is not a folder.",
                    )
                ]

            files = os.listdir(safe_dir)
            return [
                types.TextContent(
                    type="text", text=f"Files in {safe_dir}:\n" + "\n".join(files)
                )
            ]
        except SecurityError as error:
            return [types.TextContent(type="text", text=f"🛑 PATH DENIED: {error}")]

    if name == "read_safe_file":
        file_path = arguments.get("path", "")
        try:
            safe_file = validator.safe_resolve(file_path)
            if not safe_file.exists() or not safe_file.is_file():
                return [
                    types.TextContent(
                        type="text", text=f"Error: file {file_path} does not exist."
                    )
                ]

            with open(safe_file, "r", encoding="utf-8") as file_handle:
                content = file_handle.read()
            return [types.TextContent(type="text", text=content)]
        except SecurityError as error:
            return [types.TextContent(type="text", text=f"🛑 PATH DENIED: {error}")]

    if name == "move_safe_file":
        source_path = arguments.get("source", "")
        dest_path = arguments.get("destination", "")
        try:
            safe_source = validator.safe_resolve(source_path)
            safe_dest = validator.safe_resolve(dest_path)

            if not safe_source.exists():
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error: source file {source_path} does not exist.",
                    )
                ]

            shutil.move(str(safe_source), str(safe_dest))
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"✅ File successfully moved from {safe_source.name} "
                        f"to {safe_dest.parent.name}."
                    ),
                )
            ]
        except SecurityError as error:
            return [
                types.TextContent(
                    type="text",
                    text=f"🛑 PATH DENIED (Destructive Operation Aborted): {error}",
                )
            ]

    raise ValueError(f"Unknown tool: {name}")

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """Publishes metadata of available tools for the agent."""
    return [
        types.Tool(
            name="list_workspace_files",
            description="Lists files inside the user's secure workspace folder.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace to list.",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="read_safe_file",
            description="Reads a file to analyze its contents (only within the Workspace).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file to read."}
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="move_safe_file",
            description="Moves or renames files within the local Workspace (desktop organisation).",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Path of the file to move.",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination path to move the file to.",
                    },
                },
                "required": ["source", "destination"],
            },
        ),
    ]

async def main():
    """Starts the MCP server over STDIO for communication with AzulBrain."""
    print(f"[Info] AzulHands MCP Server started via STDIO (locked to: {WORKSPACE_DIR})")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
