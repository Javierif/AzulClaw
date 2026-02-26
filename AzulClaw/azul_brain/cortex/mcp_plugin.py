from semantic_kernel.functions import kernel_function


def _extract_first_text(result) -> str:
    """Extract a text payload from MCP CallToolResult-like objects."""
    content = getattr(result, "content", None)
    if not content:
        return "Sin contenido."

    first = content[0]
    text = getattr(first, "text", None)
    return text if isinstance(text, str) else str(first)


class MCPToolsPlugin:
    """Semantic Kernel plugin that proxies calls to AzulHands MCP tools."""

    def __init__(self, mcp_client):
        self.mcp = mcp_client

    @kernel_function(
        name="listar_archivos",
        description="Lista archivos dentro del workspace seguro del usuario.",
    )
    async def list_files(self, path: str = ".") -> str:
        result = await self.mcp.call_tool("list_workspace_files", {"path": path})
        return _extract_first_text(result)

    @kernel_function(
        name="leer_archivo",
        description="Lee un archivo de texto dentro del workspace seguro.",
    )
    async def read_file(self, path: str) -> str:
        result = await self.mcp.call_tool("read_safe_file", {"path": path})
        return _extract_first_text(result)

    @kernel_function(
        name="mover_archivo",
        description="Mueve o renombra archivos dentro del workspace seguro.",
    )
    async def move_file(self, source: str, destination: str) -> str:
        result = await self.mcp.call_tool(
            "move_safe_file",
            {"source": source, "destination": destination},
        )
        return _extract_first_text(result)
