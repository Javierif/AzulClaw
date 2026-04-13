"""Runtime configuration for AzulClaw."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path

ENV_LOCAL_FILENAME = ".env.local"
DEFAULT_PORT = 3978
HOST = "localhost"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeConfig:
    """Typed runtime configuration for the main process."""

    app_id: str
    app_password: str
    tenant_id: str
    port: int
    service_bus_connection_string: str = ""
    service_bus_inbound_queue: str = "bot-inbound"
    service_bus_outbound_queue: str = "bot-outbound"
    service_bus_use_sessions: str = "true"


def load_env_file(env_file_path: Path) -> None:
    """Loads variables from .env.local without overwriting already-defined variables."""
    if not env_file_path.exists():
        return

    with env_file_path.open(encoding="utf-8") as env_file:
        for raw_line in env_file:
            stripped_line = raw_line.strip()
            if not stripped_line or stripped_line.startswith("#"):
                continue
            if "=" not in stripped_line:
                continue
            key, value = stripped_line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value


def parse_port(raw_port: str, default_port: int = DEFAULT_PORT) -> int:
    """Converts PORT to an integer with a safe fallback."""
    try:
        return int(raw_port)
    except ValueError:
        LOGGER.warning(
            "Invalid PORT value ('%s'). Falling back to default port %s.",
            raw_port,
            default_port,
        )
        return default_port


def load_runtime_config(base_path: Path) -> RuntimeConfig:
    """Loads environment variables and returns typed runtime configuration."""
    load_env_file(base_path / ENV_LOCAL_FILENAME)
    app_id = os.environ.get("MicrosoftAppId", "")
    app_password = os.environ.get("MicrosoftAppPassword", "")
    tenant_id = os.environ.get("MicrosoftAppTenantId", "")
    port = parse_port(os.environ.get("PORT", str(DEFAULT_PORT)))
    
    # Service Bus Extensions
    service_bus_conn = os.environ.get("SERVICE_BUS_CONNECTION_STRING", "")
    service_bus_inbound = os.environ.get("SERVICE_BUS_INBOUND_QUEUE", "bot-inbound")
    service_bus_outbound = os.environ.get("SERVICE_BUS_OUTBOUND_QUEUE", "bot-outbound")
    service_bus_use_sessions = os.environ.get("SERVICE_BUS_USE_SESSIONS", "true")

    return RuntimeConfig(
        app_id=app_id, 
        app_password=app_password, 
        tenant_id=tenant_id,
        port=port,
        service_bus_connection_string=service_bus_conn,
        service_bus_inbound_queue=service_bus_inbound,
        service_bus_outbound_queue=service_bus_outbound,
        service_bus_use_sessions=service_bus_use_sessions,
    )
