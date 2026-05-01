"""Runtime configuration for AzulClaw."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path

from .channels.access_control import parse_csv_allowlist

ENV_LOCAL_FILENAME = ".env.local"
KEY_VAULT_URL_ENV = "AZUL_KEY_VAULT_URL"
KEY_VAULT_NAME_ENV = "AZUL_KEY_VAULT_NAME"
KEY_VAULT_ENV_KEYS_ENV = "AZUL_KEY_VAULT_ENV_KEYS"
KEY_VAULT_SECRET_NAMES_ENV = "AZUL_KEY_VAULT_SECRET_NAMES"
KEY_VAULT_STRICT_ENV = "AZUL_KEY_VAULT_STRICT"
DEFAULT_PORT = 3978
HOST = "localhost"

LOGGER = logging.getLogger(__name__)

DEFAULT_KEY_VAULT_ENV_KEYS = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_FAST_DEPLOYMENT",
    "AZURE_OPENAI_FAST_ENDPOINT",
    "AZURE_OPENAI_FAST_API_KEY",
    "AZURE_OPENAI_FAST_API_VERSION",
    "AZURE_OPENAI_SLOW_DEPLOYMENT",
    "AZURE_OPENAI_SLOW_ENDPOINT",
    "AZURE_OPENAI_SLOW_API_KEY",
    "AZURE_OPENAI_SLOW_API_VERSION",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
    "AZUL_AZURE_OPENAI_AUTH_MODE",
    "AZUL_DEFAULT_LANE",
    "AZUL_EMBEDDING_DIM",
    "AZUL_ENABLE_INTERACTIVE_BROWSER_AUTH",
    "AZUL_ENABLE_STARTUP_DEFAULT_AZURE_CREDENTIAL",
    "AZUL_ENTRA_BROWSER_CLIENT_ID",
    "AZUL_FAST_OLLAMA_API_KEY",
    "AZUL_FAST_OLLAMA_BASE_URL",
    "AZUL_FAST_OLLAMA_MODEL",
    "AZUL_FAST_PROVIDER",
    "AZUL_FAST_STREAMING_ENABLED",
    "AZUL_HYBRID_TEXT_WEIGHT",
    "AZUL_HYBRID_VECTOR_WEIGHT",
    "AZUL_MEMORY_DB_PATH",
    "AZUL_PREFERENCE_EXTRACTION_ENABLED",
    "AZUL_RUNTIME_DIR",
    "AZUL_SLOW_STREAMING_ENABLED",
    "AZUL_WORKSPACE_ROOT",
    "AZURE_TENANT_ID",
    "BOT_SYNC_REPLY_TIMEOUT_SECONDS",
    "MEMORY_MAX_MESSAGES",
    "MicrosoftAppId",
    "MicrosoftAppPassword",
    "MicrosoftAppTenantId",
    "PORT",
    "SERVICE_BUS_CONNECTION_STRING",
    "SERVICE_BUS_INBOUND_QUEUE",
    "SERVICE_BUS_OUTBOUND_QUEUE",
    "SERVICE_BUS_USE_SESSIONS",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "TELEGRAM_ALLOWED_USER_IDS",
    "VECTOR_MEMORY_ENABLED",
)


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
    service_bus_use_sessions: str = "auto"
    bot_sync_reply_timeout_seconds: float = 6.8
    telegram_allowed_user_ids: frozenset[str] = frozenset()
    telegram_allowed_chat_ids: frozenset[str] = frozenset()


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


def env_key_to_key_vault_secret_name(env_key: str) -> str:
    """Maps an environment variable name to an Azure Key Vault secret name."""
    return env_key.replace("_", "-")


def parse_key_vault_secret_keys(raw_value: str) -> list[str]:
    """Parses optional extra env keys to load from Key Vault."""
    keys: list[str] = []
    seen: set[str] = set()
    for item in raw_value.replace("\n", ",").split(","):
        key = item.strip()
        if not key or key in seen:
            continue
        keys.append(key)
        seen.add(key)
    return keys


def key_vault_secret_keys() -> list[str]:
    """Returns the env keys the runtime should try to hydrate from Key Vault."""
    configured_keys = ",".join(
        value
        for value in (
            os.environ.get(KEY_VAULT_ENV_KEYS_ENV, ""),
            os.environ.get(KEY_VAULT_SECRET_NAMES_ENV, ""),
        )
        if value.strip()
    )
    keys: list[str] = []
    seen: set[str] = set()
    for key in (
        *DEFAULT_KEY_VAULT_ENV_KEYS,
        *parse_key_vault_secret_keys(configured_keys),
    ):
        if key and key not in seen:
            keys.append(key)
            seen.add(key)
    return keys


def _load_azure_hatching_config() -> dict[str, str]:
    try:
        from .api.hatching_store import HatchingStore
    except ImportError:
        return {}
    try:
        raw_config = HatchingStore().load().skill_configs.get("Azure", {})
    except Exception as error:
        LOGGER.debug("Could not read Azure settings from Hatching profile: %s", error)
        return {}
    if not isinstance(raw_config, dict):
        return {}
    return {str(key): str(value) for key, value in raw_config.items()}


def _set_env_if_value(env_key: str, value: str, *, overwrite: bool = False) -> bool:
    cleaned = str(value or "").strip()
    if not cleaned:
        return False
    if not overwrite and os.environ.get(env_key, "").strip():
        return False
    os.environ[env_key] = cleaned
    return True


def apply_hatching_azure_runtime_settings() -> None:
    """Applies persisted desktop Azure settings to the backend process.

    Hatching/Settings stores non-secret Azure configuration. Applying it on
    startup avoids requiring users to repeat the Azure wizard after every
    backend restart. Secrets remain in Key Vault or user/machine env vars.
    """
    azure_config = _load_azure_hatching_config()
    if not azure_config:
        return

    loaded = 0
    endpoint = azure_config.get("endpoint", "").strip().rstrip("/")
    if _set_env_if_value("AZURE_OPENAI_ENDPOINT", endpoint):
        loaded += 1

    deployment = azure_config.get("deployment", "").strip()
    if deployment:
        if _set_env_if_value("AZURE_OPENAI_DEPLOYMENT", deployment):
            loaded += 1
        if _set_env_if_value("AZURE_OPENAI_SLOW_DEPLOYMENT", deployment):
            loaded += 1

    if _set_env_if_value(
        "AZURE_OPENAI_FAST_DEPLOYMENT",
        azure_config.get("fastDeployment", "").strip(),
    ):
        loaded += 1
    if _set_env_if_value(
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
        azure_config.get("embeddingDeployment", "").strip(),
    ):
        loaded += 1

    key_vault_url = azure_config.get("keyVaultUrl", "").strip().rstrip("/")
    if key_vault_url and _set_env_if_value(KEY_VAULT_URL_ENV, key_vault_url):
        loaded += 1

    if _set_env_if_value("AZURE_TENANT_ID", azure_config.get("tenantId", "").strip()):
        loaded += 1
    if _set_env_if_value(
        "AZUL_ENTRA_BROWSER_CLIENT_ID",
        azure_config.get("clientId", "").strip(),
    ):
        loaded += 1

    if azure_config.get("connected", "").strip().lower() == "true":
        if _set_env_if_value("AZUL_AZURE_OPENAI_AUTH_MODE", "entra"):
            loaded += 1

    if loaded:
        LOGGER.info("Applied %s Azure setting(s) from the desktop profile.", loaded)


def key_vault_secret_name_overrides() -> dict[str, str]:
    """Returns env-key to Key Vault secret-name overrides from the local profile."""
    azure_config = _load_azure_hatching_config()
    mapping = {
        "MicrosoftAppId": "microsoftAppIdSecretName",
        "MicrosoftAppPassword": "microsoftAppPasswordSecretName",
        "MicrosoftAppTenantId": "microsoftAppTenantIdSecretName",
    }
    overrides: dict[str, str] = {}
    for env_key, config_key in mapping.items():
        secret_name = azure_config.get(config_key, "").strip()
        if secret_name:
            overrides[env_key] = secret_name
    return overrides


def _parse_bool(raw_value: str | None, default: bool = False) -> bool:
    text = (raw_value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def resolve_key_vault_url() -> str:
    """Resolves the configured Key Vault URL from URL or vault name settings."""
    explicit_url = os.environ.get(KEY_VAULT_URL_ENV, "").strip()
    if explicit_url:
        return explicit_url.rstrip("/")
    vault_name = os.environ.get(KEY_VAULT_NAME_ENV, "").strip()
    if not vault_name:
        azure_config = _load_azure_hatching_config()
        profile_url = str(azure_config.get("keyVaultUrl", "")).strip()
        if profile_url:
            return profile_url.rstrip("/")
        vault_name = str(azure_config.get("keyVaultName", "")).strip()
    if not vault_name:
        return ""
    if vault_name.startswith("https://"):
        return vault_name.rstrip("/")
    return f"https://{vault_name}.vault.azure.net"


def load_key_vault_secrets(secret_client=None) -> None:
    """Hydrates unset runtime environment variables from Azure Key Vault."""
    vault_url = resolve_key_vault_url()
    if not vault_url:
        return

    strict = _parse_bool(os.environ.get(KEY_VAULT_STRICT_ENV), False)
    if secret_client is None:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError as error:
            message = "Key Vault is configured, but azure-keyvault-secrets is not installed."
            if strict:
                raise RuntimeError(message) from error
            LOGGER.warning("%s Continuing without Key Vault settings.", message)
            return

        secret_client = SecretClient(
            vault_url=vault_url,
            credential=DefaultAzureCredential(),
        )

    loaded_count = 0
    secret_name_overrides = key_vault_secret_name_overrides()
    for env_key in key_vault_secret_keys():
        if os.environ.get(env_key, "").strip():
            continue
        secret_name = secret_name_overrides.get(env_key) or env_key_to_key_vault_secret_name(env_key)
        try:
            secret = secret_client.get_secret(secret_name)
        except Exception as error:
            if error.__class__.__name__ == "ResourceNotFoundError":
                continue
            message = f"Could not load secret '{secret_name}' from Key Vault '{vault_url}': {error}"
            if strict:
                raise RuntimeError(message) from error
            LOGGER.warning("%s Continuing without remaining Key Vault settings.", message)
            break

        value = str(getattr(secret, "value", "") or "")
        if value:
            os.environ[env_key] = value
            loaded_count += 1

    if loaded_count:
        LOGGER.info("Loaded %s setting(s) from Azure Key Vault.", loaded_count)


def find_project_root(start_path: Path) -> Path:
    """Finds the nearest ancestor that looks like the repository root."""
    current = start_path.resolve()

    while True:
        if (current / ".git").exists() or (current / "pyproject.toml").exists():
            return current
        if current.parent == current:
            return start_path.resolve()
        current = current.parent


def load_env_files(base_path: Path) -> None:
    """Loads .env.local from the current directory up to the project root only."""
    candidates: list[Path] = []
    current = base_path.resolve()
    project_root = find_project_root(base_path)

    while True:
        candidates.append(current / ENV_LOCAL_FILENAME)
        if current == project_root:
            break
        current = current.parent

    for env_file_path in reversed(candidates):
        load_env_file(env_file_path)


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


def parse_float(raw_value: str, default_value: float, variable_name: str) -> float:
    """Converts an env var to float with a safe fallback."""
    try:
        return float(raw_value)
    except ValueError:
        LOGGER.warning(
            "Invalid %s value ('%s'). Falling back to default %s.",
            variable_name,
            raw_value,
            default_value,
        )
        return default_value


def load_runtime_config(base_path: Path) -> RuntimeConfig:
    """Loads environment variables and returns typed runtime configuration."""
    load_env_files(base_path)
    apply_hatching_azure_runtime_settings()
    load_key_vault_secrets()
    app_id = os.environ.get("MicrosoftAppId", "")
    app_password = os.environ.get("MicrosoftAppPassword", "")
    tenant_id = os.environ.get("MicrosoftAppTenantId", "")
    port = parse_port(os.environ.get("PORT", str(DEFAULT_PORT)))

    # Service Bus Extensions
    service_bus_conn = os.environ.get("SERVICE_BUS_CONNECTION_STRING", "")
    service_bus_inbound = os.environ.get("SERVICE_BUS_INBOUND_QUEUE", "bot-inbound")
    service_bus_outbound = os.environ.get("SERVICE_BUS_OUTBOUND_QUEUE", "bot-outbound")
    service_bus_use_sessions = os.environ.get("SERVICE_BUS_USE_SESSIONS", "auto")
    bot_sync_reply_timeout_seconds = parse_float(
        os.environ.get("BOT_SYNC_REPLY_TIMEOUT_SECONDS", "6.8"),
        6.8,
        "BOT_SYNC_REPLY_TIMEOUT_SECONDS",
    )
    telegram_allowed_user_ids = parse_csv_allowlist(
        os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    )
    telegram_allowed_chat_ids = parse_csv_allowlist(
        os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    )

    return RuntimeConfig(
        app_id=app_id,
        app_password=app_password,
        tenant_id=tenant_id,
        port=port,
        service_bus_connection_string=service_bus_conn,
        service_bus_inbound_queue=service_bus_inbound,
        service_bus_outbound_queue=service_bus_outbound,
        service_bus_use_sessions=service_bus_use_sessions,
        bot_sync_reply_timeout_seconds=bot_sync_reply_timeout_seconds,
        telegram_allowed_user_ids=telegram_allowed_user_ids,
        telegram_allowed_chat_ids=telegram_allowed_chat_ids,
    )
