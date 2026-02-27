"""Servidor MCP de AzulHands con herramientas de filesystem en workspace seguro."""

import asyncio
import os
import shutil

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from path_validator import PathValidator, SecurityError

# Servidor MCP registrado para canal STDIO.
app = Server("azul-hands-mcp")

# Workspace seguro (jaula) para operaciones de archivos.
USER_HOME = os.path.expanduser("~")
WORKSPACE_DIR = os.path.join(USER_HOME, "Desktop", "AzulWorkspace")

# Validador de rutas para bloqueo de path traversal.
validator = PathValidator(WORKSPACE_DIR)

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Router principal que ejecuta herramientas seguras por nombre."""
    if name == "list_workspace_files":
        relative_path = arguments.get("path", "")
        try:
            safe_dir = validator.safe_resolve(relative_path)
            if not safe_dir.exists() or not safe_dir.is_dir():
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error: El directorio {relative_path} no existe o no es una carpeta.",
                    )
                ]

            files = os.listdir(safe_dir)
            return [
                types.TextContent(
                    type="text", text=f"Archivos en {safe_dir}:\n" + "\n".join(files)
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
                        type="text", text=f"Error: El archivo {file_path} no existe."
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
                        text=f"Error: El archivo de origen {source_path} no existe.",
                    )
                ]

            shutil.move(str(safe_source), str(safe_dest))
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"✅ Archivo movido con éxito de {safe_source.name} "
                        f"a {safe_dest.parent.name}."
                    ),
                )
            ]
        except SecurityError as error:
            return [
                types.TextContent(
                    type="text",
                    text=f"🛑 PATH DENIED (Operación Destructiva Abortada): {error}",
                )
            ]

    raise ValueError(f"Unknown tool: {name}")

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """Publica metadata de herramientas disponibles para el agente."""
    return [
        types.Tool(
            name="list_workspace_files",
            description="Lista los archivos dentro de la carpeta segura (Workspace) del usuario.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Ruta relativa dentro del workspace a listar.",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="read_safe_file",
            description="Lee un archivo para analizar su contenido (solo dentro del Workspace).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta del archivo a leer."}
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="move_safe_file",
            description="Mueve o renombra archivos dentro del Workspace local (organización de escritorio).",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Ruta del documento a mover.",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Ruta a donde debe ser movido.",
                    },
                },
                "required": ["source", "destination"],
            },
        ),
    ]

async def main():
    """Arranca el servidor MCP por STDIO para comunicación con AzulBrain."""
    print(f"[Info] AzulHands MCP Server Iniciado mediante STDIO (Locked en: {WORKSPACE_DIR})")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())