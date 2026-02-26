import asyncio
import os
import shutil
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types
import mcp.types as types

# Importar el validador riguroso de rutas
from path_validator import PathValidator, SecurityError

# 1. Definición del Servidor MCP
app = Server("azul-hands-mcp")

# En producción, esto debería ser %USERPROFILE%\Desktop\AzulClaw_Workspace
# para que el Agente solo pueda tocar una jaula específica. 
# Lo definimos en el home del perfil actual.
USER_HOME = os.path.expanduser("~")
WORKSPACE_DIR = os.path.join(USER_HOME, "Desktop", "AzulWorkspace")

# Instanciar el validador con la jaula
validator = PathValidator(WORKSPACE_DIR)

# --- 2. DEFINICIÓN DE HERRAMIENTAS (Tools para la IA) ---

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Router principal de llamadas a herramientas."""
    if name == "list_workspace_files":
        relative_path = arguments.get("path", "")
        
        try:
            # Pasa por el Security Check
            safe_dir = validator.safe_resolve(relative_path)
            
            if not safe_dir.exists() or not safe_dir.is_dir():
                 return [types.TextContent(type="text", text=f"Error: El directorio {relative_path} no existe o no es una carpeta.")]
                 
            files = os.listdir(safe_dir)
            return [types.TextContent(type="text", text=f"Archivos en {safe_dir}:\n" + "\n".join(files))]
            
        except SecurityError as e:
            return [types.TextContent(type="text", text=f"🛑 PATH DENIED: {str(e)}")]

    elif name == "read_safe_file":
        file_path = arguments.get("path", "")
        
        try:
            safe_file = validator.safe_resolve(file_path)
            
            if not safe_file.exists() or not safe_file.is_file():
                 return [types.TextContent(type="text", text=f"Error: El archivo {file_path} no existe.")]
                 
            with open(safe_file, "r", encoding="utf-8") as f:
                content = f.read()
                
            return [types.TextContent(type="text", text=content)]
            
        except SecurityError as e:
            return [types.TextContent(type="text", text=f"🛑 PATH DENIED: {str(e)}")]

    elif name == "move_safe_file":
        source_path = arguments.get("source", "")
        dest_path = arguments.get("destination", "")
        
        try:
            safe_source = validator.safe_resolve(source_path)
            safe_dest = validator.safe_resolve(dest_path)
            
            if not safe_source.exists():
                return [types.TextContent(type="text", text=f"Error: El archivo de origen {source_path} no existe.")]
                
            shutil.move(str(safe_source), str(safe_dest))
            
            return [types.TextContent(type="text", text=f"✅ Archivo movido con éxito de {safe_source.name} a {safe_dest.parent.name}.")]
            
        except SecurityError as e:
            return [types.TextContent(type="text", text=f"🛑 PATH DENIED (Operación Destructiva Abortada): {str(e)}")]

    raise ValueError(f"Unknown tool: {name}")


# --- 3. EXPOSICIÓN DE LAS HERRAMIENTAS (Metadata para el Cerebro) ---

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """Le dice al cerebro de AzulBrain qué puede hacer con sus 'Manos' locales."""
    return [
        types.Tool(
            name="list_workspace_files",
            description="Lista los archivos dentro de la carpeta segura (Workspace) del usuario.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta relativa dentro del workspace a listar."}
                },
                "required": []
            }
        ),
        types.Tool(
            name="read_safe_file",
            description="Lee un archivo para analizar su contenido (solo dentro del Workspace).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta del archivo a leer."}
                },
                "required": ["path"]
            }
        ),
        types.Tool(
            name="move_safe_file",
            description="Mueve o renombra archivos dentro del Workspace local (organización de escritorio).",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Ruta del documento a mover."},
                    "destination": {"type": "string", "description": "Ruta a donde debe ser movido."}
                },
                "required": ["source", "destination"]
            }
        )
    ]

# --- 4. ARRANQUE DEL SERVIDOR VÍA STDIO ---
# STDIO es perfecto para procesos padre-hijo (El Cerebro será el padre, MCP el hijo)
async def main():
    print(f"[Info] AzulHands MCP Server Iniciado mediante STDIO (Locked en: {WORKSPACE_DIR})")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
