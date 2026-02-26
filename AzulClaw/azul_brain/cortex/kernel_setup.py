import os

import semantic_kernel as sk
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

from cortex.mcp_plugin import MCPToolsPlugin


def _require_env(var_name: str) -> str:
    value = os.environ.get(var_name, "").strip()
    if not value:
        raise RuntimeError(
            f"Falta variable de entorno requerida para IA: {var_name}"
        )
    return value


async def create_kernel(mcp_client):
    """Create a Semantic Kernel configured with Azure OpenAI and MCP tools."""
    endpoint = _require_env("AZURE_OPENAI_ENDPOINT")
    api_key = _require_env("AZURE_OPENAI_API_KEY")
    deployment_name = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o").strip()

    kernel = sk.Kernel()
    kernel.add_service(
        AzureChatCompletion(
            service_id="azure-chat",
            deployment_name=deployment_name,
            endpoint=endpoint,
            api_key=api_key,
        )
    )
    kernel.add_plugin(MCPToolsPlugin(mcp_client), plugin_name="desktop")
    return kernel
