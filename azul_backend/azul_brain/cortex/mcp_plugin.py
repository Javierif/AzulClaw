"""MCP tool adapter for consumption by the cognitive agent."""

def _extract_first_text(result) -> str:
    """Extracts the first text block from an MCP response."""
    content = getattr(result, "content", None)
    if not content:
        return "No content."

    first = content[0]
    text = getattr(first, "text", None)
    return text if isinstance(text, str) else str(first)

class MCPToolsPlugin:
    """High-level proxy between the agent and AzulHands MCP."""

    def __init__(self, mcp_client):
        """Stores a reference to the already-connected MCP client."""
        self.mcp = mcp_client

    async def list_files(self, path: str = ".") -> str:
        """Lists files inside the secure workspace."""
        result = await self.mcp.call_tool("list_workspace_files", {"path": path})
        return _extract_first_text(result)

    async def read_file(self, path: str) -> str:
        """Reads a text file inside the secure workspace."""
        result = await self.mcp.call_tool("read_safe_file", {"path": path})
        return _extract_first_text(result)

    async def move_file(self, source: str, destination: str) -> str:
        """Moves or renames a file inside the secure workspace."""
        result = await self.mcp.call_tool(
            "move_safe_file",
            {"source": source, "destination": destination},
        )
        return _extract_first_text(result)