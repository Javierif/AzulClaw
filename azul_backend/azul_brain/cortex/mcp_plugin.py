"""Adaptador de herramientas MCP para consumo desde el agente cognitivo."""

def _extract_first_text(result) -> str:
    """Extrae el primer bloque textual de una respuesta MCP."""
    content = getattr(result, "content", None)
    if not content:
        return "Sin contenido."

    first = content[0]
    text = getattr(first, "text", None)
    return text if isinstance(text, str) else str(first)

class MCPToolsPlugin:
    """Proxy de alto nivel entre el agente y AzulHands MCP."""

    def __init__(self, mcp_client):
        """Guarda referencia al cliente MCP ya conectado."""
        self.mcp = mcp_client

    async def list_files(self, path: str = ".") -> str:
        """Lista archivos dentro del workspace seguro."""
        result = await self.mcp.call_tool("list_workspace_files", {"path": path})
        return _extract_first_text(result)

    async def read_file(self, path: str) -> str:
        """Lee un archivo de texto dentro del workspace seguro."""
        result = await self.mcp.call_tool("read_safe_file", {"path": path})
        return _extract_first_text(result)

    async def move_file(self, source: str, destination: str) -> str:
        """Mueve o renombra un archivo dentro del workspace seguro."""
        result = await self.mcp.call_tool(
            "move_safe_file",
            {"source": source, "destination": destination},
        )
        return _extract_first_text(result)