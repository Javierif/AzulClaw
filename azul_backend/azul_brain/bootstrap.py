"""Startup utilities for the main AzulClaw components."""

import logging
import sys
import traceback
from pathlib import Path

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)

from .mcp_client import AzulHandsClient

LOGGER = logging.getLogger(__name__)

async def on_turn_error(context: TurnContext, error: Exception) -> None:
    """Global error handler for Bot Framework turns."""
    LOGGER.error("Error caught from the cognitive layer: %s", error)
    traceback.print_exc(file=sys.stderr)
    await context.send_activity(
        "AzulClaw's cognitive layer encountered an error. Restarting subsystems."
    )
    await context.send_activity(f"Exception: {error}")

def build_adapter(app_id: str, app_password: str) -> BotFrameworkAdapter:
    """Builds and configures the Bot Framework adapter."""
    settings = BotFrameworkAdapterSettings(app_id, app_password)
    adapter = BotFrameworkAdapter(settings)
    adapter.on_turn_error = on_turn_error
    return adapter

def build_mcp_script_path(base_path: Path) -> Path:
    """Resolves the path to the MCP server script."""
    return base_path.parent / "azul_hands_mcp" / "mcp_server.py"

def build_mcp_client(base_path: Path) -> AzulHandsClient:
    """Builds the AzulClaw MCP client."""
    mcp_script_path = build_mcp_script_path(base_path)
    return AzulHandsClient(str(mcp_script_path))