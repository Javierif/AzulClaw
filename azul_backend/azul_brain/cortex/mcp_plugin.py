"""MCP tool adapter for consumption by the cognitive agent."""

import json

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

    async def list_skill_tools(self, skill_id: str = "") -> str:
        """Lists connected Marketplace skill tools exposed through MCP."""
        if not hasattr(self.mcp, "list_tool_catalog"):
            return "No Marketplace MCP skill tools are available."
        catalog = await self.mcp.list_tool_catalog(include_primary=False)
        if skill_id.strip():
            catalog = [item for item in catalog if str(item.get("skill_id", "")).strip() == skill_id.strip()]
        if not catalog:
            return "No Marketplace MCP skill tools are connected."
        lines = []
        for item in catalog:
            lines.append(
                f"{item.get('skill_id', '')}: {item.get('tool_name', '')} - {item.get('description', '')}".strip()
            )
        return "\n".join(lines)

    async def call_skill_tool(self, skill_id: str, tool_name: str, arguments_json: str = "{}") -> str:
        """Calls a connected Marketplace MCP tool using JSON object arguments."""
        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as error:
            raise ValueError(f"arguments_json must be valid JSON: {error}") from error
        if not isinstance(arguments, dict):
            raise ValueError("arguments_json must decode to a JSON object.")
        result = await self.mcp.call_tool(tool_name, arguments, skill_id=skill_id)
        return _extract_first_text(result)
