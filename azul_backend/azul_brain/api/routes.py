"""HTTP routes for the desktop app."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from dataclasses import replace
from urllib.parse import quote, urlparse

import aiohttp
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError, ContentTypeError

from ..azure_auth import AZURE_OPENAI_SCOPE, set_frontend_azure_token
from ..bootstrap import build_adapter
from ..config import apply_hatching_azure_runtime_settings
from .services import (
    list_workspace_entries,
    load_hatching_profile,
    load_memory_runtime_settings,
    save_hatching_profile,
    save_memory_runtime_settings,
    summarize_jobs,
    summarize_memory,
    summarize_processes,
    summarize_runtime,
    wipe_local_user_data,
)

LOGGER = logging.getLogger(__name__)
AZURE_ARM_BASE_URL = "https://management.azure.com"
AZURE_SUBSCRIPTIONS_API_VERSION = "2022-12-01"
AZURE_COGNITIVE_API_VERSION = "2024-10-01"
AZURE_KEY_VAULT_API_VERSION = "2023-07-01"
KEY_VAULT_HOST_SUFFIXES = (
    "vault.azure.net",
    "vault.azure.cn",
    "vault.usgovcloudapi.net",
    "vault.microsoftazure.de",
)


def _desktop_user_id(value: object = "desktop-user") -> str:
    return str(value or "desktop-user").strip() or "desktop-user"


def _conversation_belongs_to_user(memory, conversation_id: str | None, user_id: str) -> bool:
    if not conversation_id:
        return False
    checker = getattr(memory, "conversation_belongs_to_user", None)
    if callable(checker):
        return bool(checker(conversation_id, user_id))
    return bool(memory.conversation_exists(conversation_id))


async def health_handler(_: web.Request) -> web.Response:
    """Returns basic health status of the local backend."""
    return web.json_response({"status": "ok"})


def _default_backend_log_dir() -> Path:
    override = os.environ.get("AZUL_BACKEND_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    runtime_override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if runtime_override:
        return Path(runtime_override).expanduser().parent / "logs"
    return Path(__file__).resolve().parents[3] / "memory" / "runtime-logs"


def _tail_text(path: Path, max_bytes: int = 20000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(-max_bytes, os.SEEK_END)
            data = handle.read()
        return data.decode("utf-8", errors="replace")
    except OSError as error:
        return f"Could not read log: {error}"


def _request_api_base(req: web.Request) -> str:
    host = req.host.strip()
    if host:
        return f"{req.scheme or 'http'}://{host}"
    return f"http://localhost:{os.environ.get('PORT', '3978')}"


def _validate_key_vault_url(value: str) -> str:
    parsed = urlparse(value.strip().rstrip("/"))
    if parsed.scheme.lower() != "https":
        raise ValueError("key_vault_url must use https.")
    if parsed.username or parsed.password:
        raise ValueError("key_vault_url must not contain credentials.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("key_vault_url must be the vault base URL.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("key_vault_url has an invalid port.") from error
    if port not in (None, 443):
        raise ValueError("key_vault_url must not specify a non-standard port.")
    host = (parsed.hostname or "").lower()
    if not any(host.endswith(f".{suffix}") for suffix in KEY_VAULT_HOST_SUFFIXES):
        raise ValueError("key_vault_url must point to an Azure Key Vault host.")
    return f"https://{host}"


async def desktop_backend_status_handler(req: web.Request) -> web.Response:
    """Returns backend diagnostics and recent launcher logs for Settings."""
    runtime_dir = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    log_dir = _default_backend_log_dir()
    models = req.app["runtime_manager"].list_model_status()
    enabled_models = [model for model in models if model.get("enabled")]

    logs = []
    for filename in (
        "desktop-backend.out.log",
        "desktop-backend.err.log",
        "azul-hands-mcp.err.log",
    ):
        log_path = log_dir / filename
        logs.append(
            {
                "name": filename,
                "path": str(log_path),
                "exists": log_path.exists(),
                "content": _tail_text(log_path),
            }
        )

    scheduler_status = req.app["scheduler"].get_status()
    auth_snapshot = req.app["azure_auth_state"].snapshot()

    return web.json_response(
        {
            "status": "running",
            "api_base": _request_api_base(req),
            "runtime_dir": runtime_dir,
            "log_dir": str(log_dir),
            "models_total": len(models),
            "models_enabled": len(enabled_models),
            "scheduler_running": scheduler_status["scheduler_running"],
            "auth": {
                "mode": auth_snapshot.mode,
                "startup_enabled": auth_snapshot.startup_enabled,
                "status": auth_snapshot.status,
                "detail": auth_snapshot.detail,
                "last_error": auth_snapshot.last_error,
                "last_success_at": auth_snapshot.last_success_at,
                "source": auth_snapshot.source,
                "requires_frontend_login": auth_snapshot.requires_frontend_login,
            },
            "logs": logs,
        }
    )


async def desktop_backend_auth_ensure_handler(req: web.Request) -> web.Response:
    """Triggers Azure OpenAI desktop authentication on demand."""
    auth_snapshot = await req.app["azure_auth_state"].ensure_authenticated()
    return web.json_response(
        {
            "mode": auth_snapshot.mode,
            "startup_enabled": auth_snapshot.startup_enabled,
            "status": auth_snapshot.status,
            "detail": auth_snapshot.detail,
            "last_error": auth_snapshot.last_error,
            "last_success_at": auth_snapshot.last_success_at,
            "source": auth_snapshot.source,
            "requires_frontend_login": auth_snapshot.requires_frontend_login,
        }
    )


async def desktop_azure_connect_handler(req: web.Request) -> web.Response:
    """Receives a Microsoft token from the desktop UI and applies Azure settings."""
    payload = await req.json()

    access_token = str(payload.get("access_token", "")).strip()
    expires_on = int(payload.get("expires_on", 0) or 0)
    tenant_id = str(payload.get("tenant_id", "")).strip()
    client_id = str(payload.get("client_id", "")).strip()
    endpoint = str(payload.get("endpoint", "")).strip().rstrip("/")
    deployment = str(payload.get("deployment", "")).strip()
    fast_deployment = str(payload.get("fast_deployment", "")).strip()
    embedding_deployment = str(payload.get("embedding_deployment", "")).strip()
    key_vault_url = str(payload.get("key_vault_url", "")).strip().rstrip("/")
    scope = str(payload.get("scope", AZURE_OPENAI_SCOPE)).strip() or AZURE_OPENAI_SCOPE

    if not access_token:
        return web.json_response({"error": "access_token is required"}, status=400)
    if expires_on <= int(time.time()) + 60:
        return web.json_response({"error": "access_token is expired"}, status=400)
    if not endpoint:
        return web.json_response({"error": "endpoint is required"}, status=400)
    if not deployment:
        return web.json_response({"error": "deployment is required"}, status=400)
    if key_vault_url:
        try:
            key_vault_url = _validate_key_vault_url(key_vault_url)
        except ValueError as error:
            return web.json_response({"error": str(error)}, status=400)

    try:
        set_frontend_azure_token(
            access_token=access_token,
            expires_on=expires_on,
            scope=scope,
            tenant_id=tenant_id,
            client_id=client_id,
        )
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)

    os.environ["AZURE_OPENAI_ENDPOINT"] = endpoint
    os.environ["AZURE_OPENAI_DEPLOYMENT"] = deployment
    os.environ["AZURE_OPENAI_SLOW_DEPLOYMENT"] = deployment
    if key_vault_url:
        os.environ["AZUL_KEY_VAULT_URL"] = key_vault_url
    else:
        os.environ.pop("AZUL_KEY_VAULT_URL", None)
    if fast_deployment:
        os.environ["AZURE_OPENAI_FAST_DEPLOYMENT"] = fast_deployment
    else:
        os.environ.pop("AZURE_OPENAI_FAST_DEPLOYMENT", None)
    if embedding_deployment:
        os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"] = embedding_deployment
    else:
        os.environ.pop("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", None)

    auth_snapshot = await req.app["azure_auth_state"].ensure_authenticated()
    return web.json_response(
        {
            "mode": auth_snapshot.mode,
            "startup_enabled": auth_snapshot.startup_enabled,
            "status": auth_snapshot.status,
            "detail": auth_snapshot.detail,
            "last_error": auth_snapshot.last_error,
            "last_success_at": auth_snapshot.last_success_at,
            "source": auth_snapshot.source,
            "requires_frontend_login": auth_snapshot.requires_frontend_login,
        }
    )


def _secret_name_from_payload(payload: dict, field: str, default_name: str) -> str:
    return str(payload.get(field, "")).strip() or default_name


async def _key_vault_get_secret(
    *,
    session: aiohttp.ClientSession,
    access_token: str,
    vault_url: str,
    secret_name: str,
) -> str:
    url = f"{vault_url.rstrip('/')}/secrets/{quote(secret_name)}?api-version=7.4"
    async with session.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as response:
        try:
            data = await response.json(content_type=None)
        except ContentTypeError:
            data = {}
        if response.status == 404:
            return ""
        if response.status >= 400:
            detail = data.get("error", {}).get("message") if isinstance(data, dict) else ""
            raise web.HTTPBadRequest(
                text=json.dumps(
                    {"error": detail or f"Key Vault secret request failed ({response.status})"}
                ),
                content_type="application/json",
            )
        if not isinstance(data, dict):
            return ""
        return str(data.get("value", "") or "")


async def desktop_azure_key_vault_hydrate_handler(req: web.Request) -> web.Response:
    """Hydrates startup secrets using a frontend-acquired Key Vault token."""
    payload = await req.json()
    access_token = str(payload.get("access_token", "")).strip()
    expires_on = int(payload.get("expires_on", 0) or 0)
    vault_url = str(payload.get("key_vault_url", "")).strip().rstrip("/")
    if not access_token:
        return web.json_response({"error": "access_token is required"}, status=400)
    if expires_on <= int(time.time()) + 60:
        return web.json_response({"error": "access_token is expired"}, status=400)
    if not vault_url:
        return web.json_response({"error": "key_vault_url is required"}, status=400)
    try:
        vault_url = _validate_key_vault_url(vault_url)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)

    secret_fields = {
        "MicrosoftAppId": _secret_name_from_payload(
            payload, "microsoft_app_id_secret_name", "MicrosoftAppId"
        ),
        "MicrosoftAppPassword": _secret_name_from_payload(
            payload, "microsoft_app_password_secret_name", "MicrosoftAppPassword"
        ),
        "MicrosoftAppTenantId": _secret_name_from_payload(
            payload, "microsoft_app_tenant_id_secret_name", "MicrosoftAppTenantId"
        ),
    }

    current_config = req.app["runtime_config"]
    hydrated: list[str] = []
    missing: list[str] = []
    async with aiohttp.ClientSession() as session:
        for env_key, secret_name in secret_fields.items():
            value = await _key_vault_get_secret(
                session=session,
                access_token=access_token,
                vault_url=vault_url,
                secret_name=secret_name,
            )
            if value:
                os.environ[env_key] = value
                hydrated.append(env_key)
            else:
                missing.append(env_key)

    os.environ["AZUL_KEY_VAULT_URL"] = vault_url

    next_config = replace(
        current_config,
        app_id=os.environ.get("MicrosoftAppId", current_config.app_id),
        app_password=os.environ.get("MicrosoftAppPassword", current_config.app_password),
        tenant_id=os.environ.get("MicrosoftAppTenantId", current_config.tenant_id),
    )
    req.app["runtime_config"] = next_config
    req.app["adapter"] = build_adapter(
        next_config.app_id,
        next_config.app_password,
        next_config.tenant_id,
    )
    servicebus_worker = req.app.get("servicebus_worker")
    if servicebus_worker is not None:
        servicebus_worker.adapter = req.app["adapter"]

    return web.json_response({"hydrated": hydrated, "missing": missing})


def _arm_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


async def _arm_get_json(access_token: str, url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers=_arm_headers(access_token),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                detail = data.get("error", {}).get("message") if isinstance(data, dict) else ""
                raise web.HTTPBadRequest(
                    text=json.dumps({"error": detail or f"Azure request failed ({response.status})"}),
                    content_type="application/json",
                )
            return data if isinstance(data, dict) else {}


def _resource_group_from_id(resource_id: str) -> str:
    parts = [part for part in (resource_id or "").split("/") if part]
    for index, part in enumerate(parts):
        if part.lower() == "resourcegroups" and index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _cognitive_account_endpoint(item: dict) -> str:
    properties = item.get("properties", {}) if isinstance(item.get("properties"), dict) else {}
    endpoints = properties.get("endpoints", {}) if isinstance(properties.get("endpoints"), dict) else {}
    for key in ("openAI", "openai", "OpenAI", "azureOpenAI", "AzureOpenAI"):
        value = str(endpoints.get(key, "")).strip().rstrip("/")
        if value:
            return value

    endpoint = str(properties.get("endpoint", "")).strip().rstrip("/")
    if endpoint:
        return endpoint

    name = str(item.get("name", "")).strip()
    kind = str(item.get("kind", "")).strip().lower()
    if name and kind == "aiservices":
        return f"https://{name}.services.ai.azure.com"
    if name and kind == "openai":
        return f"https://{name}.openai.azure.com"
    return ""


def _key_vault_uri(item: dict) -> str:
    properties = item.get("properties", {}) if isinstance(item.get("properties"), dict) else {}
    uri = str(properties.get("vaultUri", "")).strip().rstrip("/")
    if uri:
        return uri
    name = str(item.get("name", "")).strip()
    if name:
        return f"https://{name}.vault.azure.net"
    return ""


def _is_supported_model_resource(item: dict, endpoint: str) -> bool:
    kind = str(item.get("kind", "")).strip().lower()
    host = endpoint.lower()
    return (
        "openai" in kind
        or kind == "aiservices"
        or ".openai.azure.com" in host
        or ".services.ai.azure.com" in host
    )


def _suggest_deployment_capabilities(item: dict) -> list[str]:
    model = item.get("properties", {}).get("model", {}) if isinstance(item, dict) else {}
    model_name = str(model.get("name", "")).lower()
    capabilities: list[str] = []
    if "embedding" in model_name:
        capabilities.append("embedding")
    else:
        capabilities.append("chat")
        if "mini" in model_name or "nano" in model_name:
            capabilities.append("fast")
        else:
            capabilities.append("main")
    return capabilities


async def desktop_azure_discovery_subscriptions_handler(req: web.Request) -> web.Response:
    payload = await req.json()
    access_token = str(payload.get("access_token", "")).strip()
    if not access_token:
        return web.json_response({"error": "access_token is required"}, status=400)

    data = await _arm_get_json(
        access_token,
        f"{AZURE_ARM_BASE_URL}/subscriptions?api-version={AZURE_SUBSCRIPTIONS_API_VERSION}",
    )
    items = []
    for item in data.get("value", []):
        if not isinstance(item, dict):
            continue
        sub_id = str(item.get("subscriptionId", "")).strip()
        if not sub_id:
            continue
        tenant_id = str(item.get("tenantId", "")).strip()
        items.append(
            {
                "id": sub_id,
                "display_name": str(item.get("displayName", sub_id)).strip() or sub_id,
                "state": str(item.get("state", "")).strip(),
                "tenant_id": tenant_id,
            }
        )
    return web.json_response({"items": items})


async def desktop_azure_discovery_resources_handler(req: web.Request) -> web.Response:
    payload = await req.json()
    access_token = str(payload.get("access_token", "")).strip()
    subscription_id = str(payload.get("subscription_id", "")).strip()
    if not access_token or not subscription_id:
        return web.json_response({"error": "access_token and subscription_id are required"}, status=400)

    data = await _arm_get_json(
        access_token,
        f"{AZURE_ARM_BASE_URL}/subscriptions/{quote(subscription_id)}/providers/Microsoft.CognitiveServices/accounts?api-version={AZURE_COGNITIVE_API_VERSION}",
    )
    items = []
    for item in data.get("value", []):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        endpoint = _cognitive_account_endpoint(item)
        if not _is_supported_model_resource(item, endpoint):
            continue
        resource_id = str(item.get("id", "")).strip()
        items.append(
            {
                "id": resource_id,
                "name": str(item.get("name", "")).strip(),
                "location": str(item.get("location", "")).strip(),
                "resource_group": _resource_group_from_id(resource_id),
                "subscription_id": subscription_id,
                "kind": kind,
                "endpoint": endpoint,
            }
        )
    items.sort(key=lambda item: (item["name"].lower(), item["location"].lower()))
    return web.json_response({"items": items})


async def desktop_azure_discovery_key_vaults_handler(req: web.Request) -> web.Response:
    payload = await req.json()
    access_token = str(payload.get("access_token", "")).strip()
    subscription_id = str(payload.get("subscription_id", "")).strip()
    if not access_token or not subscription_id:
        return web.json_response({"error": "access_token and subscription_id are required"}, status=400)

    data = await _arm_get_json(
        access_token,
        (
            f"{AZURE_ARM_BASE_URL}/subscriptions/{quote(subscription_id)}"
            f"/providers/Microsoft.KeyVault/vaults?api-version={AZURE_KEY_VAULT_API_VERSION}"
        ),
    )
    items = []
    for item in data.get("value", []):
        if not isinstance(item, dict):
            continue
        resource_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        vault_uri = _key_vault_uri(item)
        if not name or not vault_uri:
            continue
        items.append(
            {
                "id": resource_id,
                "name": name,
                "location": str(item.get("location", "")).strip(),
                "resource_group": _resource_group_from_id(resource_id),
                "subscription_id": subscription_id,
                "vault_uri": vault_uri,
            }
        )
    items.sort(key=lambda item: (item["name"].lower(), item["location"].lower()))
    return web.json_response({"items": items})


async def desktop_azure_discovery_key_vault_secrets_handler(req: web.Request) -> web.Response:
    payload = await req.json()
    access_token = str(payload.get("access_token", "")).strip()
    subscription_id = str(payload.get("subscription_id", "")).strip()
    resource_group = str(payload.get("resource_group", "")).strip()
    vault_name = str(payload.get("vault_name", "")).strip()
    if not access_token or not subscription_id or not resource_group or not vault_name:
        return web.json_response(
            {"error": "access_token, subscription_id, resource_group and vault_name are required"},
            status=400,
        )

    data = await _arm_get_json(
        access_token,
        (
            f"{AZURE_ARM_BASE_URL}/subscriptions/{quote(subscription_id)}/resourceGroups/{quote(resource_group)}"
            f"/providers/Microsoft.KeyVault/vaults/{quote(vault_name)}/secrets"
            f"?api-version={AZURE_KEY_VAULT_API_VERSION}"
        ),
    )
    items = []
    for item in data.get("value", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        attributes = item.get("attributes", {}) if isinstance(item.get("attributes"), dict) else {}
        items.append(
            {
                "id": str(item.get("id", "")).strip(),
                "name": name,
                "enabled": attributes.get("enabled", True) is not False,
                "content_type": str(item.get("contentType", "")).strip(),
            }
        )
    items.sort(key=lambda item: item["name"].lower())
    return web.json_response({"items": items})


async def desktop_azure_discovery_deployments_handler(req: web.Request) -> web.Response:
    payload = await req.json()
    access_token = str(payload.get("access_token", "")).strip()
    subscription_id = str(payload.get("subscription_id", "")).strip()
    resource_group = str(payload.get("resource_group", "")).strip()
    account_name = str(payload.get("account_name", "")).strip()
    if not access_token or not subscription_id or not resource_group or not account_name:
        return web.json_response(
            {"error": "access_token, subscription_id, resource_group and account_name are required"},
            status=400,
        )

    data = await _arm_get_json(
        access_token,
        (
            f"{AZURE_ARM_BASE_URL}/subscriptions/{quote(subscription_id)}/resourceGroups/{quote(resource_group)}"
            f"/providers/Microsoft.CognitiveServices/accounts/{quote(account_name)}/deployments"
            f"?api-version={AZURE_COGNITIVE_API_VERSION}"
        ),
    )
    items = []
    for item in data.get("value", []):
        if not isinstance(item, dict):
            continue
        properties = item.get("properties", {}) if isinstance(item.get("properties"), dict) else {}
        model = properties.get("model", {}) if isinstance(properties.get("model"), dict) else {}
        items.append(
            {
                "id": str(item.get("id", "")).strip(),
                "name": str(item.get("name", "")).strip(),
                "model_name": str(model.get("name", "")).strip(),
                "model_version": str(model.get("version", "")).strip(),
                "model_format": str(model.get("format", "")).strip(),
                "sku_name": str((item.get("sku") or {}).get("name", "")).strip() if isinstance(item.get("sku"), dict) else "",
                "provisioning_state": str(properties.get("provisioningState", "")).strip(),
                "capabilities": _suggest_deployment_capabilities(item),
            }
        )
    items.sort(key=lambda item: item["name"].lower())
    return web.json_response({"items": items})


async def desktop_chat_handler(req: web.Request) -> web.Response:
    """Processes a message from the desktop app."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()

    user_id = _desktop_user_id(payload.get("user_id"))
    message = str(payload.get("message", "")).strip()
    conversation_id = str(payload.get("conversation_id", "")).strip() or None

    if not message:
        return web.json_response({"error": "message is required"}, status=400)

    if not _conversation_belongs_to_user(orchestrator.memory, conversation_id, user_id):
        conversation_id, _ = orchestrator.memory.get_or_create_empty_conversation(user_id)
    orchestrator.memory.set_active_conversation(user_id, conversation_id)

    reply = await orchestrator.process_user_message(
        user_id,
        message,
        lane="auto",
        conversation_id=conversation_id,
    )
    history = orchestrator.memory.get_conversation_messages(conversation_id, limit=12)
    conversation_title = orchestrator.memory.get_conversation_title(conversation_id)
    return web.json_response(
        {
            "user_id": user_id,
            "reply": reply.text,
            "history": history,
            "conversation_id": conversation_id,
            "conversation_title": conversation_title or "",
            "runtime": {
                "lane": reply.lane,
                "model_id": reply.model_id,
                "model_label": reply.model_label,
                "process_id": reply.process_id,
                "triage_reason": reply.triage_reason,
            },
        }
    )


async def desktop_chat_stream_handler(req: web.Request) -> web.StreamResponse:
    """Processes a message from the desktop app and emits incremental NDJSON."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()

    user_id = _desktop_user_id(payload.get("user_id"))
    message = str(payload.get("message", "")).strip()
    conversation_id = str(payload.get("conversation_id", "")).strip() or None

    # If no conversation supplied, get or create an empty one so messages are always scoped.
    if not _conversation_belongs_to_user(orchestrator.memory, conversation_id, user_id):
        conversation_id, _ = orchestrator.memory.get_or_create_empty_conversation(user_id)
    orchestrator.memory.set_active_conversation(user_id, conversation_id)

    if not message:
        return web.json_response({"error": "message is required"}, status=400)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson; charset=utf-8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    response.enable_chunked_encoding()
    await response.prepare(req)
    stream_closed = False

    async def write_event(event: dict) -> bool:
        nonlocal stream_closed
        if stream_closed:
            return False
        chunk = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            await response.write(chunk)
            if hasattr(response, "drain"):
                await response.drain()
        except (ClientConnectionResetError, ConnectionResetError):
            stream_closed = True
            return False
        return True

    try:
        await write_event({"type": "start"})

        reply = await orchestrator.process_user_message_stream(
            user_id,
            message,
            lane="auto",
            conversation_id=conversation_id,
            on_delta=lambda text: write_event({"type": "delta", "text": text}),
            on_commentary=lambda text: write_event({"type": "commentary", "text": text}),
            on_progress=lambda progress: write_event({"type": "progress", "progress": progress}),
        )
        history = orchestrator.memory.get_conversation_messages(conversation_id, limit=12)
        conv_title = reply.conversation_title or orchestrator.memory.get_conversation_title(
            conversation_id
        )
        await write_event(
            {
                "type": "done",
                "reply": reply.text,
                "history": history,
                "conversation_id": conversation_id,
                "conversation_title": conv_title or "",
                "runtime": {
                    "lane": reply.lane,
                    "model_id": reply.model_id,
                    "model_label": reply.model_label,
                    "process_id": reply.process_id,
                    "triage_reason": reply.triage_reason,
                },
            }
        )
    except Exception as error:
        if not stream_closed:
            await write_event({"type": "error", "message": str(error)})
    finally:
        if not stream_closed:
            try:
                await response.write_eof()
            except (ClientConnectionResetError, ConnectionResetError):
                pass

    return response


async def desktop_conversations_handler(req: web.Request) -> web.Response:
    """Lists conversations for the desktop user."""
    orchestrator = req.app["orchestrator"]
    user_id = _desktop_user_id(req.query.get("user_id"))
    convs = orchestrator.memory.list_conversations(user_id)
    return web.json_response({"items": convs})


async def desktop_create_conversation_handler(req: web.Request) -> web.Response:
    """Returns an existing empty conversation or creates one (idempotent)."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()
    user_id = _desktop_user_id(payload.get("user_id"))
    conv_id, title = orchestrator.memory.get_or_create_empty_conversation(user_id)
    orchestrator.memory.set_active_conversation(user_id, conv_id)
    return web.json_response({"id": conv_id, "title": title})


async def desktop_conversation_messages_handler(req: web.Request) -> web.Response:
    """Returns messages for a specific conversation."""
    orchestrator = req.app["orchestrator"]
    conv_id = req.match_info.get("conv_id", "").strip()
    user_id = _desktop_user_id(req.query.get("user_id"))
    if not conv_id:
        return web.json_response({"error": "conv_id required"}, status=400)
    if not _conversation_belongs_to_user(orchestrator.memory, conv_id, user_id):
        return web.json_response({"error": "Conversation not found"}, status=404)
    orchestrator.memory.set_active_conversation(user_id, conv_id)
    msgs = orchestrator.memory.get_conversation_messages(conv_id)
    return web.json_response({"messages": msgs})


async def desktop_delete_conversation_handler(req: web.Request) -> web.Response:
    """Deletes a conversation and all its messages."""
    orchestrator = req.app["orchestrator"]
    conv_id = req.match_info.get("conv_id", "").strip()
    if not conv_id:
        return web.json_response({"error": "conv_id required"}, status=400)
    deleted = orchestrator.memory.delete_conversation(conv_id)
    if not deleted:
        return web.json_response({"error": "Conversation not found"}, status=404)
    return web.json_response({"deleted": True, "id": conv_id})


async def desktop_processes_handler(_: web.Request) -> web.Response:
    """Returns the process summary visible to the desktop app."""
    return web.json_response({"items": summarize_processes(_.app["process_registry"])})


async def desktop_memory_handler(req: web.Request) -> web.Response:
    """Returns a summarised memory view for the desktop app."""
    orchestrator = req.app["orchestrator"]
    user_id = _desktop_user_id(req.query.get("user_id"))
    return web.json_response({"items": summarize_memory(orchestrator, user_id)})


async def desktop_memory_delete_handler(req: web.Request) -> web.Response:
    """Deletes a specific memory entry from the vector store."""
    memory_id = req.match_info.get("memory_id", "").strip()
    user_id = _desktop_user_id(req.query.get("user_id"))

    if not memory_id:
        return web.json_response({"error": "memory_id required"}, status=400)

    orchestrator = req.app.get("orchestrator")
    vector_memory = getattr(orchestrator, "vector_memory", None) if orchestrator else None
    if vector_memory is None:
        return web.json_response({"error": "Vector memory unavailable"}, status=503)

    deleted = vector_memory.delete_memory(memory_id, user_id)
    if not deleted:
        return web.json_response({"error": "Memory not found"}, status=404)

    return web.json_response({"deleted": True, "id": memory_id})


async def desktop_memory_settings_get_handler(_: web.Request) -> web.Response:
    """Returns user-editable memory persistence settings."""
    return web.json_response(load_memory_runtime_settings())


async def desktop_memory_settings_put_handler(req: web.Request) -> web.Response:
    """Saves memory persistence settings and reopens local memory stores."""
    payload = await req.json()
    try:
        result = save_memory_runtime_settings(payload)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)

    orchestrator = req.app.get("orchestrator")
    if orchestrator is not None and hasattr(orchestrator, "reload_persistent_memory"):
        try:
            orchestrator.reload_persistent_memory()
        except Exception as error:
            LOGGER.warning("[Memory] reload after settings save failed: %s", error)
            return web.json_response(
                {
                    **result,
                    "reload_ok": False,
                    "reload_error": str(error),
                }
            )
    return web.json_response({**result, "reload_ok": True})


async def desktop_workspace_handler(req: web.Request) -> web.Response:
    """Lists the contents of the sandbox workspace."""
    relative_path = req.query.get("path", ".")
    try:
        listing = list_workspace_entries(relative_path)
    except Exception as error:
        return web.json_response({"error": str(error)}, status=400)

    return web.json_response(listing)


async def desktop_hatching_get_handler(_: web.Request) -> web.Response:
    """Returns the current Hatching profile."""
    return web.json_response(load_hatching_profile())


async def desktop_hatching_put_handler(req: web.Request) -> web.Response:
    """Saves the Hatching profile sent by the desktop app."""
    import asyncio
    payload = await req.json()
    result = save_hatching_profile(payload)
    apply_hatching_azure_runtime_settings()
    orchestrator = req.app.get("orchestrator")
    if orchestrator is not None and hasattr(orchestrator, "reload_persistent_memory"):
        try:
            orchestrator.reload_persistent_memory()
        except Exception as error:
            LOGGER.warning("[Memory] reload after hatching save failed: %s", error)
        # Seed profile facts when the user completes or re-saves onboarding
        if result.get("is_hatched") and hasattr(orchestrator, "seed_profile_facts"):
            asyncio.create_task(orchestrator.seed_profile_facts())
    return web.json_response(result)


async def desktop_data_wipe_handler(req: web.Request) -> web.Response:
    """Clears SQLite memory and resets hatching (requires brain restart)."""
    try:
        payload = await req.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "JSON body required"}, status=400)

    try:
        result = wipe_local_user_data(str(payload.get("confirm", "")))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)

    orchestrator = req.app.get("orchestrator")
    if orchestrator is not None and hasattr(orchestrator, "reload_persistent_memory"):
        try:
            orchestrator.reload_persistent_memory()
        except Exception as error:
            LOGGER.warning("[Memory] reload after data wipe failed: %s", error)

    return web.json_response(result)


async def desktop_runtime_get_handler(req: web.Request) -> web.Response:
    """Returns aggregated status of the local runtime."""
    return web.json_response(
        summarize_runtime(
            req.app["runtime_manager"],
            req.app["scheduler"],
            req.app["process_registry"],
        )
    )


async def desktop_runtime_put_handler(req: web.Request) -> web.Response:
    """Saves editable runtime configuration."""
    payload = await req.json()
    req.app["runtime_manager"].save_settings(payload)
    return web.json_response(
        summarize_runtime(
            req.app["runtime_manager"],
            req.app["scheduler"],
            req.app["process_registry"],
        )
    )




async def desktop_jobs_get_handler(req: web.Request) -> web.Response:
    """Lists scheduled runtime jobs."""
    return web.json_response({"items": summarize_jobs(req.app["runtime_store"])})


async def desktop_jobs_post_handler(req: web.Request) -> web.Response:
    """Creates or updates a scheduled job."""
    payload = await req.json()
    try:
        job = req.app["runtime_store"].upsert_job(payload)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    return web.json_response(job.__dict__)


async def desktop_job_run_handler(req: web.Request) -> web.Response:
    """Runs an existing job manually."""
    job_id = req.match_info.get("job_id", "")
    try:
        result = await req.app["scheduler"].run_job_now(job_id)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=404)
    return web.json_response(result)


async def desktop_job_delete_handler(req: web.Request) -> web.Response:
    """Deletes a job from the local scheduler."""
    job_id = req.match_info.get("job_id", "")
    try:
        req.app["runtime_store"].delete_job(job_id)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    return web.json_response({"deleted": True, "job_id": job_id})


def register_desktop_routes(app: web.Application) -> None:
    """Registers endpoints consumed by the desktop app."""
    app.router.add_get("/api/health", health_handler)
    app.router.add_get("/api/desktop/backend/status", desktop_backend_status_handler)
    app.router.add_post("/api/desktop/backend/auth/ensure", desktop_backend_auth_ensure_handler)
    app.router.add_post("/api/desktop/azure/connect", desktop_azure_connect_handler)
    app.router.add_post("/api/desktop/azure/key-vault/hydrate", desktop_azure_key_vault_hydrate_handler)
    app.router.add_post("/api/desktop/azure/discovery/subscriptions", desktop_azure_discovery_subscriptions_handler)
    app.router.add_post("/api/desktop/azure/discovery/resources", desktop_azure_discovery_resources_handler)
    app.router.add_post("/api/desktop/azure/discovery/key-vaults", desktop_azure_discovery_key_vaults_handler)
    app.router.add_post("/api/desktop/azure/discovery/key-vault-secrets", desktop_azure_discovery_key_vault_secrets_handler)
    app.router.add_post("/api/desktop/azure/discovery/deployments", desktop_azure_discovery_deployments_handler)
    app.router.add_post("/api/desktop/chat", desktop_chat_handler)
    app.router.add_post("/api/desktop/chat/stream", desktop_chat_stream_handler)
    app.router.add_get("/api/desktop/conversations", desktop_conversations_handler)
    app.router.add_post("/api/desktop/conversations", desktop_create_conversation_handler)
    app.router.add_get("/api/desktop/conversations/{conv_id}/messages", desktop_conversation_messages_handler)
    app.router.add_delete("/api/desktop/conversations/{conv_id}", desktop_delete_conversation_handler)
    app.router.add_get("/api/desktop/processes", desktop_processes_handler)
    app.router.add_get("/api/desktop/memory", desktop_memory_handler)
    app.router.add_delete("/api/desktop/memory/{memory_id}", desktop_memory_delete_handler)
    app.router.add_get("/api/desktop/memory/settings", desktop_memory_settings_get_handler)
    app.router.add_put("/api/desktop/memory/settings", desktop_memory_settings_put_handler)
    app.router.add_get("/api/desktop/workspace", desktop_workspace_handler)
    app.router.add_get("/api/desktop/hatching", desktop_hatching_get_handler)
    app.router.add_put("/api/desktop/hatching", desktop_hatching_put_handler)
    app.router.add_post("/api/desktop/data-wipe", desktop_data_wipe_handler)
    app.router.add_get("/api/desktop/runtime", desktop_runtime_get_handler)
    app.router.add_put("/api/desktop/runtime", desktop_runtime_put_handler)
    app.router.add_get("/api/desktop/jobs", desktop_jobs_get_handler)
    app.router.add_post("/api/desktop/jobs", desktop_jobs_post_handler)
    app.router.add_post("/api/desktop/jobs/{job_id}/run", desktop_job_run_handler)
    app.router.add_delete("/api/desktop/jobs/{job_id}", desktop_job_delete_handler)
