"""Shared Azure authentication helpers for Azure OpenAI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import os
import time
from typing import Literal

try:
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
except ImportError:  # pragma: no cover - dependency availability varies by env
    DefaultAzureCredential = None
    get_bearer_token_provider = None


AZURE_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"
AUTH_MODE_ENV = "AZUL_AZURE_OPENAI_AUTH_MODE"
INTERACTIVE_BROWSER_ENV = "AZUL_ENABLE_INTERACTIVE_BROWSER_AUTH"
INTERACTIVE_BROWSER_CLIENT_ID_ENV = "AZUL_ENTRA_BROWSER_CLIENT_ID"
STARTUP_DEFAULT_CREDENTIAL_ENV = "AZUL_ENABLE_STARTUP_DEFAULT_AZURE_CREDENTIAL"
FRONTEND_AUTH_SOURCE = "frontend"


@dataclass
class _BearerAccessToken:
    token: str
    expires_on: int


@dataclass
class FrontendAzureToken:
    access_token: str
    expires_on: int
    scope: str
    tenant_id: str = ""
    client_id: str = ""

    def is_valid(self) -> bool:
        # Keep a small buffer so long-running calls do not start with a nearly expired token.
        return bool(self.access_token.strip()) and self.expires_on > int(time.time()) + 60


class FrontendAzureCredential:
    """Azure credential adapter backed by the token acquired in the desktop UI."""

    def get_token(self, *scopes: str, **_: object) -> _BearerAccessToken:
        token = get_frontend_azure_token()
        if token is None:
            raise RuntimeError("No active Microsoft login token is available from the desktop UI.")
        requested_scope = scopes[0] if scopes else AZURE_OPENAI_SCOPE
        if requested_scope != token.scope:
            raise RuntimeError(
                f"Frontend token scope mismatch. Expected {requested_scope}, got {token.scope}."
            )
        return _BearerAccessToken(token=token.access_token, expires_on=token.expires_on)


_frontend_token: FrontendAzureToken | None = None


def _parse_bool(raw_value: str | None, default: bool) -> bool:
    text = (raw_value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def resolve_azure_openai_auth_mode(explicit_api_key: str = "") -> Literal["api_key", "entra"]:
    """Returns the effective Azure OpenAI auth mode."""
    requested = (os.environ.get(AUTH_MODE_ENV, "auto") or "auto").strip().lower()
    if requested == "api_key":
        return "api_key"
    if requested == "entra":
        return "entra"
    if explicit_api_key.strip():
        return "api_key"
    if os.environ.get("AZURE_OPENAI_API_KEY", "").strip():
        return "api_key"
    return "entra"


def _interactive_browser_enabled() -> bool:
    """Interactive browser auth requires an explicit app registration client id."""
    if not _parse_bool(os.environ.get(INTERACTIVE_BROWSER_ENV), False):
        return False
    return bool(os.environ.get(INTERACTIVE_BROWSER_CLIENT_ID_ENV, "").strip())


def _startup_default_credential_enabled() -> bool:
    return _parse_bool(os.environ.get(STARTUP_DEFAULT_CREDENTIAL_ENV), False)


def set_frontend_azure_token(
    *,
    access_token: str,
    expires_on: int,
    scope: str = AZURE_OPENAI_SCOPE,
    tenant_id: str = "",
    client_id: str = "",
) -> FrontendAzureToken:
    """Stores a frontend-acquired Microsoft token in memory for backend Azure calls."""
    global _frontend_token
    token = FrontendAzureToken(
        access_token=access_token.strip(),
        expires_on=int(expires_on),
        scope=(scope or AZURE_OPENAI_SCOPE).strip(),
        tenant_id=tenant_id.strip(),
        client_id=client_id.strip(),
    )
    if not token.is_valid():
        raise ValueError("Microsoft login token is missing or expired.")
    _frontend_token = token
    get_default_azure_credential.cache_clear()
    get_azure_openai_token_provider.cache_clear()
    os.environ[AUTH_MODE_ENV] = "entra"
    if token.tenant_id:
        os.environ["AZURE_TENANT_ID"] = token.tenant_id
    if token.client_id:
        os.environ[INTERACTIVE_BROWSER_CLIENT_ID_ENV] = token.client_id
    return token


def get_frontend_azure_token() -> FrontendAzureToken | None:
    """Returns the active frontend token if it is still usable."""
    if _frontend_token is None or not _frontend_token.is_valid():
        return None
    return _frontend_token


def has_frontend_azure_token() -> bool:
    return get_frontend_azure_token() is not None


@lru_cache(maxsize=1)
def get_default_azure_credential():
    """Creates a shared DefaultAzureCredential for Azure OpenAI."""
    if has_frontend_azure_token():
        return FrontendAzureCredential()

    if DefaultAzureCredential is None:
        raise RuntimeError(
            "azure-identity is required for Microsoft Entra ID authentication."
        )

    kwargs: dict[str, object] = {}
    if _interactive_browser_enabled():
        kwargs["exclude_interactive_browser_credential"] = False
        kwargs["interactive_browser_client_id"] = os.environ[
            INTERACTIVE_BROWSER_CLIENT_ID_ENV
        ].strip()
        tenant_id = os.environ.get("AZURE_TENANT_ID", "").strip()
        if tenant_id:
            kwargs["interactive_browser_tenant_id"] = tenant_id
    return DefaultAzureCredential(**kwargs)


@lru_cache(maxsize=1)
def get_azure_openai_token_provider():
    """Returns a bearer-token provider callable for Azure OpenAI."""
    frontend_token = get_frontend_azure_token()
    if frontend_token is not None:
        return lambda: get_default_azure_credential().get_token(AZURE_OPENAI_SCOPE).token

    if get_bearer_token_provider is None:
        raise RuntimeError(
            "azure-identity is required for Microsoft Entra ID authentication."
        )
    return get_bearer_token_provider(get_default_azure_credential(), AZURE_OPENAI_SCOPE)


def acquire_azure_openai_token() -> str:
    """Acquires a fresh Azure OpenAI bearer token using the shared credential."""
    credential = get_default_azure_credential()
    token = credential.get_token(AZURE_OPENAI_SCOPE)
    return token.token


def describe_azure_openai_auth(
    *,
    endpoint: str,
    deployment: str,
    explicit_api_key: str = "",
) -> tuple[bool, str]:
    """Returns whether Azure OpenAI auth is sufficiently configured for the runtime."""
    if not endpoint.strip() or not deployment.strip():
        return False, "Incomplete Azure configuration"

    auth_mode = resolve_azure_openai_auth_mode(explicit_api_key)
    if auth_mode == "api_key":
        if explicit_api_key.strip() or os.environ.get("AZURE_OPENAI_API_KEY", "").strip():
            return True, "Azure API key configuration ready"
        return False, "Azure API key auth selected but no key is configured"

    if has_frontend_azure_token():
        return True, "Azure Entra ID ready (desktop Microsoft login)"
    if DefaultAzureCredential is None or get_bearer_token_provider is None:
        return False, "azure-identity is not installed"
    if _interactive_browser_enabled():
        return True, "Azure Entra ID ready (interactive browser enabled)"
    if _startup_default_credential_enabled():
        return True, "Azure Entra ID ready (default credential enabled)"
    return False, "Azure Entra ID requires desktop Microsoft login"


@dataclass
class AzureOpenAIAuthSnapshot:
    mode: Literal["api_key", "entra"]
    startup_enabled: bool
    status: Literal["idle", "authenticating", "authenticated", "failed", "disabled"]
    detail: str
    last_error: str = ""
    last_success_at: str = ""
    source: str = ""
    requires_frontend_login: bool = False


class AzureOpenAIAuthState:
    """Tracks eager Azure OpenAI authentication for desktop startup."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._status: Literal["idle", "authenticating", "authenticated", "failed", "disabled"] = "idle"
        self._detail = "Waiting to authenticate"
        self._last_error = ""
        self._last_success_at = ""

    def snapshot(self) -> AzureOpenAIAuthSnapshot:
        mode = resolve_azure_openai_auth_mode()
        has_frontend_token = has_frontend_azure_token()
        interactive_browser_enabled = _interactive_browser_enabled()
        startup_default_credential_enabled = _startup_default_credential_enabled()
        startup_enabled = mode == "entra" and (
            has_frontend_token
            or interactive_browser_enabled
            or startup_default_credential_enabled
        )
        requires_frontend_login = (
            mode == "entra"
            and not has_frontend_token
            and not interactive_browser_enabled
            and not startup_default_credential_enabled
            and self._status != "authenticated"
        )
        status = self._status
        detail = self._detail
        if mode == "api_key":
            status = "disabled"
            detail = "API key mode does not use desktop sign-in"
            requires_frontend_login = False
        elif not startup_enabled and not has_frontend_token:
            status = "idle"
            detail = "Waiting for desktop Microsoft login"
        source = FRONTEND_AUTH_SOURCE if has_frontend_token else "default"
        return AzureOpenAIAuthSnapshot(
            mode=mode,
            startup_enabled=startup_enabled,
            status=status,
            detail=detail,
            last_error=self._last_error,
            last_success_at=self._last_success_at,
            source=source,
            requires_frontend_login=requires_frontend_login,
        )

    async def ensure_authenticated(self) -> AzureOpenAIAuthSnapshot:
        """Triggers Azure OpenAI token acquisition when Entra mode is active."""
        mode = resolve_azure_openai_auth_mode()
        if mode != "entra":
            self._status = "disabled"
            self._detail = "API key mode does not use desktop sign-in"
            self._last_error = ""
            return self.snapshot()

        if (
            not has_frontend_azure_token()
            and not _interactive_browser_enabled()
            and not _startup_default_credential_enabled()
        ):
            self._status = "idle"
            self._detail = "Waiting for desktop Microsoft login"
            self._last_error = ""
            return self.snapshot()

        async with self._lock:
            self._status = "authenticating"
            self._detail = "Requesting Microsoft Entra token"
            self._last_error = ""
            try:
                await asyncio.to_thread(acquire_azure_openai_token)
            except Exception as error:
                self._status = "failed"
                self._last_error = str(error).strip() or error.__class__.__name__
                self._detail = "Microsoft Entra authentication failed"
                return self.snapshot()

            self._status = "authenticated"
            self._detail = "Microsoft Entra token acquired"
            self._last_success_at = (
                datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            )
            return self.snapshot()
