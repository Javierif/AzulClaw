"""Utilidades de arranque para componentes principales de AzulClaw."""

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
    """Manejador global de errores en turnos de Bot Framework."""
    LOGGER.error("Error capturado del sistema cognitivo: %s", error)
    traceback.print_exc(file=sys.stderr)
    await context.send_activity(
        "El cerebro de AzulClaw ha encontrado un error. Reiniciando subsistemas."
    )
    await context.send_activity(f"Exception: {error}")

def build_adapter(app_id: str, app_password: str) -> BotFrameworkAdapter:
    """Construye y configura el adapter de Bot Framework."""
    settings = BotFrameworkAdapterSettings(app_id, app_password)
    adapter = BotFrameworkAdapter(settings)
    adapter.on_turn_error = on_turn_error
    return adapter

def build_mcp_script_path(base_path: Path) -> Path:
    """Resuelve la ruta del servidor MCP."""
    return base_path.parent / "azul_hands_mcp" / "mcp_server.py"

def build_mcp_client(base_path: Path) -> AzulHandsClient:
    """Construye el cliente MCP de AzulClaw."""
    mcp_script_path = build_mcp_script_path(base_path)
    return AzulHandsClient(str(mcp_script_path))