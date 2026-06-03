"""HTTP routes for the desktop app."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
import time
from dataclasses import asdict, replace
from urllib.parse import quote, urlparse

import aiohttp
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError, ContentTypeError
from aiohttp.web_exceptions import HTTPRequestEntityTooLarge

from ..attachments import MAX_ATTACHMENTS_PER_TURN, MAX_ATTACHMENT_SIZE_BYTES
from ..runtime.approval_protocol import parse_approval_block
from ..runtime.approval_service import ApprovalService, default_approval_lifecycle_path
from ..runtime.skill_workflow_runtime import HumanApprovalResponse, SkillWorkflowRuntime

from ..azure_auth import AZURE_OPENAI_SCOPE, set_frontend_azure_token
from ..bootstrap import build_adapter
from ..channels.servicebus_worker import ServiceBusWorker
from ..config import (
    apply_hatching_azure_runtime_settings,
    derive_fast_azure_openai_endpoint,
    load_runtime_config,
    normalize_azure_openai_endpoint,
    normalize_azure_openai_profile_endpoint,
)
from .services import (
    list_workspace_entries,
    load_hatching_profile,
    load_memory_runtime_settings,
    invalidate_runtime_model_caches,
    save_hatching_profile,
    save_memory_runtime_settings,
    summarize_job,
    summarize_jobs,
    summarize_memory,
    summarize_processes,
    summarize_runtime,
    sync_runtime_models_from_azure_profile,
    wipe_local_user_data,
)
from .skill_services import (
    approve_registry_skill_version,
    list_channel_connector_runtime_status,
    get_registry_admin_overview,
    get_registry_admin_skill_versions,
    inspect_skill_bundle,
    configure_skill,
    install_skill,
    install_skill_bundle,
    list_registry_admin_skills,
    list_remote_agent_runtime_status,
    list_enabled_workflow_runtime_specs,
    list_installed_skills,
    list_marketplace_skills,
    load_skill_marketplace_settings,
    publish_registry_bundle,
    probe_skill_registry,
    refresh_marketplace_catalog,
    resolve_skill_asset_path,
    revoke_registry_skill_version,
    save_skill_marketplace_settings,
    uninstall_skill,
    update_skill_enabled,
)

LOGGER = logging.getLogger(__name__)
AZURE_ARM_BASE_URL = "https://management.azure.com"
AZURE_SUBSCRIPTIONS_API_VERSION = "2022-12-01"
AZURE_COGNITIVE_API_VERSION = "2024-10-01"
AZURE_KEY_VAULT_API_VERSION = "2023-07-01"
AZURE_WEB_API_VERSION = "2023-12-01"
AZURE_ARM_HOST = "management.azure.com"
MAX_AZURE_DISCOVERY_PAGES = 50
KEY_VAULT_HOST_SUFFIXES = (
    "vault.azure.net",
    "vault.azure.cn",
    "vault.usgovcloudapi.net",
    "vault.microsoftazure.de",
)
_APPROVAL_STATUS_LABELS = {
    "pending": "Awaiting approval",
    "approved": "Approved",
    "rejected": "Rejected",
    "superseded": "Superseded",
    "expired": "Expired",
    "running": "Running",
    "completed": "Completed",
    "failed": "Failed",
}
_STALE_APPROVAL_ERROR = (
    "That approval is no longer available. Please regenerate the reviewed plan before applying changes."
)


def _desktop_user_id(value: object = "desktop-user") -> str:
    return str(value or "desktop-user").strip() or "desktop-user"


def _coerce_mcp_tool_result(result: object) -> object:
    content = getattr(result, "content", None)
    if isinstance(content, list) and content:
        text = getattr(content[0], "text", None)
        if isinstance(text, str):
            stripped = text.strip()
            if stripped.startswith("{"):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    return {"text": stripped}
            return {"text": stripped}
    value = getattr(result, "value", None)
    if value is not None:
        return value
    return result


def _enrich_history_with_approval_status(
    messages: list[dict],
    *,
    orchestrator=None,
    user_id: str = "",
    conversation_id: str | None = None,
) -> list[dict]:
    """Attaches approval lifecycle metadata to persisted chat messages."""

    if not messages:
        return messages
    service = ApprovalService(default_approval_lifecycle_path())
    records_by_action_id = {record.action_id: record for record in service.load()}
    heartbeat_pending_id = ""
    sensitive_pending_id = ""
    safe_user_id = str(user_id or "").strip()
    safe_conversation_id = str(conversation_id or "").strip()
    if orchestrator is not None and safe_user_id:
        heartbeat_service = getattr(orchestrator, "heartbeat_intents", None)
        heartbeat_pending = (
            heartbeat_service.pending_store.get_for_context(safe_user_id, safe_conversation_id or None)
            if heartbeat_service is not None and getattr(heartbeat_service, "pending_store", None) is not None
            else None
        )
        heartbeat_pending_id = str(getattr(heartbeat_pending, "id", "")).strip()
        sensitive_service = getattr(orchestrator, "pending_sensitive_actions", None)
        sensitive_pending = (
            sensitive_service.get_pending_action_for_context(safe_user_id, safe_conversation_id or None)
            if sensitive_service is not None
            else None
        )
        sensitive_pending_id = str(getattr(sensitive_pending, "id", "")).strip()
    enriched: list[dict] = []
    for item in messages:
        message = dict(item)
        fields = parse_approval_block(str(message.get("content", "")))
        action_id = str(fields.get("ActionId", "")).strip()
        if action_id:
            record = records_by_action_id.get(action_id)
            action_kind = str(fields.get("ActionKind", "")).strip().lower()
            live_pending_id = heartbeat_pending_id if action_kind == "heartbeat_create" else sensitive_pending_id
            if record is not None:
                effective_status = record.status
                if record.status == "pending" and safe_user_id and record.user_id == safe_user_id:
                    if live_pending_id and live_pending_id != action_id:
                        service.mark_superseded(action_id, superseded_by=live_pending_id)
                        effective_status = "superseded"
                    elif not live_pending_id:
                        service.mark_expired(action_id)
                        effective_status = "expired"
                message["approval_action_id"] = action_id
                message["approval_status"] = effective_status
                message["approval_status_label"] = _APPROVAL_STATUS_LABELS.get(
                    effective_status,
                    effective_status.title(),
                )
            else:
                effective_status = "pending" if live_pending_id == action_id else "expired"
                message["approval_action_id"] = action_id
                message["approval_status"] = effective_status
                message["approval_status_label"] = _APPROVAL_STATUS_LABELS.get(
                    effective_status,
                    effective_status.title(),
                )
        enriched.append(message)
    return enriched


def _conversation_belongs_to_user(memory, conversation_id: str | None, user_id: str) -> bool:
    if not conversation_id:
        return False
    checker = getattr(memory, "conversation_belongs_to_user", None)
    if callable(checker):
        return bool(checker(conversation_id, user_id))
    return bool(memory.conversation_exists(conversation_id))


def _attachment_ids_from_payload(payload: dict) -> list[str]:
    raw_ids = payload.get("attachment_ids", [])
    if raw_ids is None:
        return []
    if not isinstance(raw_ids, list):
        raise ValueError("attachment_ids must be an array.")
    attachment_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
    if len(attachment_ids) > MAX_ATTACHMENTS_PER_TURN:
        raise ValueError(f"At most {MAX_ATTACHMENTS_PER_TURN} attachments are allowed per turn.")
    return attachment_ids


def _pending_action_from_payload(payload: dict) -> tuple[str | None, str | None]:
    action_id = str(payload.get("pending_action_id", "")).strip() or None
    decision = str(payload.get("pending_action_decision", "")).strip().lower() or None
    if decision is not None and decision not in {"approve", "reject"}:
        raise ValueError("pending_action_decision must be 'approve' or 'reject'.")
    if (action_id is None) != (decision is None):
        raise ValueError("pending_action_id and pending_action_decision must be provided together.")
    return action_id, decision


def _effective_desktop_message(message: str, attachment_ids: list[str]) -> str:
    normalized = (message or "").strip()
    if normalized:
        return normalized
    if attachment_ids:
        return "Please analyze the attached files."
    return ""


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


def _default_runtime_dir() -> Path:
    override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "memory"


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
    runtime_dir = _default_runtime_dir()
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
            "runtime_dir": str(runtime_dir),
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

    auth_mode = str(payload.get("auth_mode", "entra")).strip().lower() or "entra"
    access_token = str(payload.get("access_token", "")).strip()
    expires_on = int(payload.get("expires_on", 0) or 0)
    api_key = str(payload.get("api_key", "")).strip()
    tenant_id = str(payload.get("tenant_id", "")).strip()
    client_id = str(payload.get("client_id", "")).strip()
    endpoint = str(payload.get("endpoint", "")).strip().rstrip("/")
    deployment = str(payload.get("deployment", "")).strip()
    fast_deployment = str(payload.get("fast_deployment", "")).strip()
    embedding_deployment = str(payload.get("embedding_deployment", "")).strip()
    key_vault_url = str(payload.get("key_vault_url", "")).strip().rstrip("/")
    scope = str(payload.get("scope", AZURE_OPENAI_SCOPE)).strip() or AZURE_OPENAI_SCOPE

    if auth_mode not in {"entra", "api_key"}:
        return web.json_response({"error": "auth_mode must be 'entra' or 'api_key'"}, status=400)
    if auth_mode == "entra":
        if not access_token:
            return web.json_response({"error": "access_token is required"}, status=400)
        if expires_on <= int(time.time()) + 60:
            return web.json_response({"error": "access_token is expired"}, status=400)
    elif not api_key:
        return web.json_response({"error": "api_key is required"}, status=400)
    if not endpoint:
        return web.json_response({"error": "endpoint is required"}, status=400)
    try:
        profile_endpoint = normalize_azure_openai_profile_endpoint(endpoint)
        endpoint = normalize_azure_openai_endpoint(profile_endpoint)
        fast_endpoint = derive_fast_azure_openai_endpoint(profile_endpoint)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    if not deployment:
        return web.json_response({"error": "deployment is required"}, status=400)
    if key_vault_url and auth_mode == "entra":
        try:
            key_vault_url = _validate_key_vault_url(key_vault_url)
        except ValueError as error:
            return web.json_response({"error": str(error)}, status=400)

    if auth_mode == "entra":
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
    os.environ["AZURE_OPENAI_FAST_ENDPOINT"] = fast_endpoint
    os.environ["AZURE_OPENAI_DEPLOYMENT"] = deployment
    os.environ["AZURE_OPENAI_SLOW_DEPLOYMENT"] = deployment
    if auth_mode == "api_key":
        os.environ["AZURE_OPENAI_API_KEY"] = api_key
        os.environ["AZUL_AZURE_OPENAI_AUTH_MODE"] = "api_key"
    else:
        os.environ.pop("AZURE_OPENAI_API_KEY", None)
        os.environ["AZUL_AZURE_OPENAI_AUTH_MODE"] = "entra"
    if key_vault_url and auth_mode == "entra":
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

    invalidate_runtime_model_caches(req.app.get("runtime_manager"))
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


def _is_allowed_next_link(url: str, allowed_hosts: set[str]) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host in allowed_hosts


def _combine_page_data(pages: list[dict]) -> dict:
    if not pages:
        return {}
    combined = dict(pages[0])
    values: list = []
    has_value = False
    for page in pages:
        value = page.get("value")
        if isinstance(value, list):
            has_value = True
            values.extend(value)
    if has_value:
        combined["value"] = values
        combined.pop("nextLink", None)
    return combined


async def _get_paginated_json(
    *,
    session: aiohttp.ClientSession,
    url: str,
    headers: dict[str, str],
    allowed_next_link_hosts: set[str],
    error_prefix: str,
) -> dict:
    pages: list[dict] = []
    next_url = url
    for _ in range(MAX_AZURE_DISCOVERY_PAGES):
        async with session.get(
            next_url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                detail = data.get("error", {}).get("message") if isinstance(data, dict) else ""
                raise web.HTTPBadRequest(
                    text=json.dumps({"error": detail or f"{error_prefix} ({response.status})"}),
                    content_type="application/json",
                )
            page = data if isinstance(data, dict) else {}
            pages.append(page)
            next_link = str(page.get("nextLink", "")).strip()
            if not next_link:
                return _combine_page_data(pages)
            if not _is_allowed_next_link(next_link, allowed_next_link_hosts):
                raise web.HTTPBadRequest(
                    text=json.dumps({"error": f"{error_prefix}: unsupported nextLink host"}),
                    content_type="application/json",
                )
            next_url = next_link
    raise web.HTTPBadRequest(
        text=json.dumps({"error": f"{error_prefix}: too many pages"}),
        content_type="application/json",
    )


async def _arm_get_json(access_token: str, url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        return await _get_paginated_json(
            session=session,
            url=url,
            headers=_arm_headers(access_token),
            allowed_next_link_hosts={AZURE_ARM_HOST},
            error_prefix="Azure request failed",
        )


async def _arm_post_json(access_token: str, url: str, payload: dict | None = None) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers=_arm_headers(access_token),
            json=payload or {},
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


def _map_telegram_function_app_settings(properties: dict[str, object]) -> dict[str, str]:
    mapping = {
        "SERVICE_BUS_CONNECTION_STRING": "serviceBusConnectionString",
        "SERVICE_BUS_INBOUND_QUEUE": "serviceBusInboundQueue",
        "SERVICE_BUS_OUTBOUND_QUEUE": "serviceBusOutboundQueue",
        "SERVICE_BUS_USE_SESSIONS": "serviceBusUseSessions",
        "BOT_SYNC_REPLY_TIMEOUT_SECONDS": "botSyncReplyTimeoutSeconds",
        "MicrosoftAppId": "microsoftAppId",
        "MicrosoftAppPassword": "microsoftAppPassword",
        "MicrosoftAppTenantId": "microsoftAppTenantId",
        "BOT_RELAY_REQUIRE_AUTH": "botRelayRequireAuth",
        "TELEGRAM_ALLOWED_USER_IDS": "allowedUserIds",
        "TELEGRAM_ALLOWED_CHAT_IDS": "allowedChatIds",
    }
    config: dict[str, str] = {}
    for env_key, config_key in mapping.items():
        value = str(properties.get(env_key, "")).strip()
        if value:
            config[config_key] = value
    return config


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


def _normalize_deployment_capability(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")


def _extend_unique_capabilities(target: list[str], values: list[str]) -> None:
    seen = set(target)
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        target.append(value)


def _extract_arm_deployment_capabilities(item: dict) -> list[str]:
    properties = item.get("properties", {}) if isinstance(item, dict) else {}
    raw_capabilities = properties.get("capabilities") if isinstance(properties, dict) else None
    extracted: list[str] = []
    falsey = {"", "0", "false", "no", "none", "null", "disabled"}

    if isinstance(raw_capabilities, dict):
        for key, value in raw_capabilities.items():
            if isinstance(value, bool):
                if not value:
                    continue
            elif str(value).strip().lower() in falsey:
                continue
            token = _normalize_deployment_capability(key)
            if token:
                extracted.append(token)
    elif isinstance(raw_capabilities, list):
        for value in raw_capabilities:
            token = _normalize_deployment_capability(value)
            if token:
                extracted.append(token)

    return extracted


def _suggest_deployment_capabilities(item: dict) -> list[str]:
    model = item.get("properties", {}).get("model", {}) if isinstance(item, dict) else {}
    model_name = str(model.get("name", "")).lower()
    capabilities: list[str] = []
    _extend_unique_capabilities(capabilities, _extract_arm_deployment_capabilities(item))
    if "embedding" in model_name:
        _extend_unique_capabilities(capabilities, ["embedding"])
    else:
        _extend_unique_capabilities(capabilities, ["chat"])
        if "mini" in model_name or "nano" in model_name:
            _extend_unique_capabilities(capabilities, ["fast"])
        else:
            _extend_unique_capabilities(capabilities, ["main"])
    if "vision" in model_name:
        _extend_unique_capabilities(capabilities, ["vision", "image-input", "multimodal"])
    return capabilities


def _key_vault_secret_display_name(item: dict) -> str:
    name = str(item.get("name", "")).strip()
    if name:
        return name
    parsed = urlparse(str(item.get("id", "")).strip())
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[-2:-1] == ["secrets"]:
        return parts[-1]
    return ""


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


async def desktop_azure_discovery_function_apps_handler(req: web.Request) -> web.Response:
    payload = await req.json()
    access_token = str(payload.get("access_token", "")).strip()
    subscription_id = str(payload.get("subscription_id", "")).strip()
    if not access_token or not subscription_id:
        return web.json_response({"error": "access_token and subscription_id are required"}, status=400)

    data = await _arm_get_json(
        access_token,
        (
            f"{AZURE_ARM_BASE_URL}/subscriptions/{quote(subscription_id)}"
            f"/providers/Microsoft.Web/sites?api-version={AZURE_WEB_API_VERSION}"
        ),
    )
    items = []
    for item in data.get("value", []):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        if "functionapp" not in kind.lower():
            continue
        resource_id = str(item.get("id", "")).strip()
        properties = item.get("properties", {}) if isinstance(item.get("properties"), dict) else {}
        name = str(item.get("name", "")).strip()
        if not resource_id or not name:
            continue
        items.append(
            {
                "id": resource_id,
                "name": name,
                "location": str(item.get("location", "")).strip(),
                "resource_group": _resource_group_from_id(resource_id),
                "subscription_id": subscription_id,
                "kind": kind,
                "state": str(properties.get("state", "")).strip(),
                "default_hostname": str(properties.get("defaultHostName", "")).strip(),
            }
        )
    items.sort(key=lambda item: (item["name"].lower(), item["resource_group"].lower()))
    return web.json_response({"items": items})


async def desktop_azure_discovery_function_app_settings_handler(req: web.Request) -> web.Response:
    payload = await req.json()
    access_token = str(payload.get("access_token", "")).strip()
    subscription_id = str(payload.get("subscription_id", "")).strip()
    resource_group = str(payload.get("resource_group", "")).strip()
    function_app_name = str(payload.get("function_app_name", "")).strip()
    if not access_token or not subscription_id or not resource_group or not function_app_name:
        return web.json_response(
            {"error": "access_token, subscription_id, resource_group and function_app_name are required"},
            status=400,
        )

    data = await _arm_post_json(
        access_token,
        (
            f"{AZURE_ARM_BASE_URL}/subscriptions/{quote(subscription_id)}"
            f"/resourceGroups/{quote(resource_group)}"
            f"/providers/Microsoft.Web/sites/{quote(function_app_name)}"
            f"/config/appsettings/list?api-version={AZURE_WEB_API_VERSION}"
        ),
    )
    properties = data.get("properties", {}) if isinstance(data.get("properties"), dict) else {}
    config = _map_telegram_function_app_settings(properties)
    required = {"serviceBusConnectionString", "microsoftAppId", "microsoftAppPassword"}
    return web.json_response(
        {
            "config": config,
            "missing": sorted(required - set(config)),
            "imported": sorted(config),
        }
    )


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
    vault_url = str(payload.get("vault_url", "")).strip().rstrip("/")
    if not access_token or not vault_url:
        return web.json_response(
            {"error": "access_token and vault_url are required"},
            status=400,
        )
    try:
        vault_url = _validate_key_vault_url(vault_url)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)

    async with aiohttp.ClientSession() as session:
        data = await _get_paginated_json(
            session=session,
            url=f"{vault_url}/secrets?api-version=7.4",
            headers={"Authorization": f"Bearer {access_token}"},
            allowed_next_link_hosts={(urlparse(vault_url).hostname or "").lower()},
            error_prefix="Key Vault secret discovery failed",
        )
    items = []
    for item in data.get("value", []):
        if not isinstance(item, dict):
            continue
        name = _key_vault_secret_display_name(item)
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


async def desktop_attachments_post_handler(req: web.Request) -> web.Response:
    """Stores draft attachments uploaded from the desktop composer."""
    orchestrator = req.app["orchestrator"]
    user_id = _desktop_user_id(req.query.get("user_id") or req.headers.get("X-Azul-User-Id"))
    conversation_id = str(req.query.get("conversation_id", "")).strip() or None
    if conversation_id and not _conversation_belongs_to_user(orchestrator.memory, conversation_id, user_id):
        return web.json_response({"error": "Conversation not found"}, status=404)
    try:
        reader = await req.multipart()
    except HTTPRequestEntityTooLarge:
        return web.json_response({"error": "Attachment exceeds the 20 MB size limit."}, status=413)
    created: list[dict] = []
    file_count = 0

    def _cleanup_created_drafts() -> None:
        deleter = getattr(orchestrator.memory, "delete_draft_attachment", None)
        if not callable(deleter):
            return
        for item in created:
            attachment_id = str(item.get("id", "")).strip()
            if attachment_id:
                deleter(attachment_id, user_id)

    async for part in reader:
        if getattr(part, "filename", None) is None:
            continue
        file_count += 1
        if file_count > MAX_ATTACHMENTS_PER_TURN:
            _cleanup_created_drafts()
            return web.json_response(
                {"error": f"At most {MAX_ATTACHMENTS_PER_TURN} attachments are allowed per turn."},
                status=400,
            )
        filename = str(part.filename or "").strip() or "attachment"
        data = bytearray()
        try:
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_ATTACHMENT_SIZE_BYTES:
                    _cleanup_created_drafts()
                    return web.json_response({"error": "Attachment exceeds the 20 MB size limit."}, status=413)
        except HTTPRequestEntityTooLarge:
            _cleanup_created_drafts()
            return web.json_response({"error": "Attachment exceeds the 20 MB size limit."}, status=413)
        try:
            created.append(
                orchestrator.memory.create_attachment_draft(
                    user_id=user_id,
                    filename=filename,
                    data=bytes(data),
                    conversation_id=conversation_id,
                )
            )
        except ValueError as error:
            _cleanup_created_drafts()
            return web.json_response({"error": str(error)}, status=400)

    if not created:
        return web.json_response({"error": "No files were uploaded."}, status=400)
    return web.json_response({"items": created})


async def desktop_attachment_delete_handler(req: web.Request) -> web.Response:
    """Deletes a draft attachment before it is sent."""
    orchestrator = req.app["orchestrator"]
    user_id = _desktop_user_id(req.query.get("user_id"))
    attachment_id = req.match_info.get("attachment_id", "").strip()
    if not attachment_id:
        return web.json_response({"error": "attachment_id required"}, status=400)
    deleted = orchestrator.memory.delete_draft_attachment(attachment_id, user_id)
    if not deleted:
        return web.json_response({"error": "Attachment not found"}, status=404)
    return web.json_response({"deleted": True, "id": attachment_id})


async def desktop_chat_handler(req: web.Request) -> web.Response:
    """Processes a message from the desktop app."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()

    user_id = _desktop_user_id(payload.get("user_id"))
    message = str(payload.get("message", "")).strip()
    conversation_id = str(payload.get("conversation_id", "")).strip() or None
    try:
        attachment_ids = _attachment_ids_from_payload(payload)
        pending_action_id, pending_action_decision = _pending_action_from_payload(payload)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    message = _effective_desktop_message(message, attachment_ids)

    if not message and pending_action_id is None:
        return web.json_response({"error": "message is required"}, status=400)

    if not _conversation_belongs_to_user(orchestrator.memory, conversation_id, user_id):
        conversation_id, _ = orchestrator.memory.get_or_create_empty_conversation(user_id)
    orchestrator.memory.set_active_conversation(user_id, conversation_id)

    if pending_action_id is not None and pending_action_decision is not None:
        reply = await orchestrator._try_handle_pending_action_decision(
            user_id,
            pending_action_id,
            pending_action_decision,
            conversation_id=conversation_id,
        )
        if reply is None:
            return web.json_response({"error": _STALE_APPROVAL_ERROR}, status=404)
    else:
        reply = await orchestrator.process_user_message(
            user_id,
            message,
            lane="auto",
            conversation_id=conversation_id,
            attachment_ids=attachment_ids,
        )
    marker = getattr(orchestrator.memory, "mark_conversation_viewed", None)
    if callable(marker):
        marker(user_id, conversation_id)
    history_loader = getattr(orchestrator.memory, "get_conversation_message_records", None)
    if callable(history_loader):
        history = history_loader(conversation_id, limit=12)
    else:
        history = orchestrator.memory.get_conversation_messages(conversation_id, limit=12)
    history = _enrich_history_with_approval_status(
        history,
        orchestrator=orchestrator,
        user_id=user_id,
        conversation_id=conversation_id,
    )
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
                "attempt_count": reply.attempt_count,
                "skipped_models": reply.skipped_models or [],
                "failed_attempts": reply.failed_attempts or [],
                "triage_reason": reply.triage_reason,
                "turn_status": reply.turn_status,
                "workflow_events": getattr(reply, "workflow_events", None) or [],
            },
        }
    )


async def desktop_chat_stream_handler(req: web.Request) -> web.StreamResponse:
    """Processes a message from the desktop app and emits incremental NDJSON."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()
    
    class _StreamClientDisconnected(Exception):
        """Raised when the client disconnects mid-stream."""

    user_id = _desktop_user_id(payload.get("user_id"))
    message = str(payload.get("message", "")).strip()
    conversation_id = str(payload.get("conversation_id", "")).strip() or None
    try:
        attachment_ids = _attachment_ids_from_payload(payload)
        pending_action_id, pending_action_decision = _pending_action_from_payload(payload)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    message = _effective_desktop_message(message, attachment_ids)

    # If no conversation supplied, get or create an empty one so messages are always scoped.
    if not _conversation_belongs_to_user(orchestrator.memory, conversation_id, user_id):
        conversation_id, _ = orchestrator.memory.get_or_create_empty_conversation(user_id)
    orchestrator.memory.set_active_conversation(user_id, conversation_id)

    if not message and pending_action_id is None:
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

    async def push_event(event: dict) -> None:
        if not await write_event(event):
            raise _StreamClientDisconnected()

    try:
        await push_event({"type": "start"})
        if pending_action_id is not None and pending_action_decision is not None:
            await push_event({"type": "commentary", "text": "Processing approval now."})
            await push_event(
                {
                    "type": "progress-init",
                    "progress": {
                        "title": "Pending action approval",
                        "summary": "Processing approval now.",
                        "badge": "Fast brain",
                        "lane": "fast",
                        "lane_label": "Fast brain",
                        "triage_reason": "pending-action",
                        "reason_label": "Pending action approval",
                        "current_step_label": "Processing approval",
                        "active_count": 1,
                        "phases": [],
                    },
                }
            )
            reply = await orchestrator._try_handle_pending_action_decision(
                user_id,
                pending_action_id,
                pending_action_decision,
                conversation_id=conversation_id,
            )
            if reply is None:
                raise ValueError(_STALE_APPROVAL_ERROR)
            await push_event({"type": "delta", "text": reply.text})
        else:
            reply = await orchestrator.process_user_message_stream(
                user_id,
                message,
                lane="auto",
                conversation_id=conversation_id,
                attachment_ids=attachment_ids,
                on_delta=lambda text: push_event({"type": "delta", "text": text}),
                on_commentary=lambda text: push_event({"type": "commentary", "text": text}),
                on_progress=lambda event_type, progress: push_event({"type": event_type, "progress": progress}),
            )
        history_loader = getattr(orchestrator.memory, "get_conversation_message_records", None)
        if callable(history_loader):
            history = history_loader(conversation_id, limit=12)
        else:
            history = orchestrator.memory.get_conversation_messages(conversation_id, limit=12)
        history = _enrich_history_with_approval_status(
            history,
            orchestrator=orchestrator,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        marker = getattr(orchestrator.memory, "mark_conversation_viewed", None)
        if callable(marker):
            marker(user_id, conversation_id)
        conv_title = reply.conversation_title or orchestrator.memory.get_conversation_title(
            conversation_id
        )
        await push_event(
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
                    "attempt_count": reply.attempt_count,
                    "skipped_models": reply.skipped_models or [],
                    "failed_attempts": reply.failed_attempts or [],
                    "triage_reason": reply.triage_reason,
                    "turn_status": reply.turn_status,
                    "workflow_events": getattr(reply, "workflow_events", None) or [],
                },
            }
        )
    except _StreamClientDisconnected:
        pass
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


def _render_workflow_decision_reply(
    *,
    approved: bool,
    workflow_name: str,
    events: list[dict],
) -> str:
    name = str(workflow_name or "Skill workflow").strip() or "Skill workflow"
    if not approved:
        return f"{name} approval was cancelled. No workflow changes were applied."
    completed_event = next((event for event in events if event.get("type") == "completed"), None)
    if completed_event is not None:
        data = completed_event.get("data") if isinstance(completed_event.get("data"), dict) else {}
        status = str(data.get("status", "")).strip()
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        summary = str(result.get("summary", "") or data.get("summary", "") or "").strip()
        if summary:
            return f"{name} finished applying the approved plan.\n\n{summary}"
        if status:
            return f"{name} finished applying the approved plan.\n\nStatus: {status}"
        return f"{name} finished applying the approved plan."
    failed_event = next((event for event in events if event.get("type") == "failed"), None)
    if failed_event is not None:
        data = failed_event.get("data") if isinstance(failed_event.get("data"), dict) else {}
        error = str(data.get("error", failed_event.get("error", "Workflow failed."))).strip()
        return f"{name} could not apply the approved plan.\n\n{error}"
    return f"{name} approval was recorded."


async def desktop_workflow_request_decision_handler(req: web.Request) -> web.Response:
    """Resolves a human-in-the-loop request emitted by a skill workflow."""

    runtime = req.app.get("skill_workflow_runtime")
    if not isinstance(runtime, SkillWorkflowRuntime):
        return web.json_response({"error": "Skill workflow runtime unavailable."}, status=503)
    run_id = req.match_info.get("run_id", "").strip()
    request_id = req.match_info.get("request_id", "").strip()
    if not run_id or not request_id:
        return web.json_response({"error": "run_id and request_id are required."}, status=400)
    try:
        payload = await req.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "JSON body required."}, status=400)
    decision = str(payload.get("decision", "")).strip().lower()
    if decision not in {"approve", "reject"}:
        return web.json_response({"error": "decision must be approve or reject."}, status=400)
    user_id = _desktop_user_id(payload.get("user_id"))
    try:
        response = HumanApprovalResponse(
            approved=decision == "approve",
            user_id=user_id,
            reason=str(payload.get("reason", "")).strip(),
        )
        run = runtime.store.get_run(run_id)
        workflow_spec = None
        if decision == "approve" and run is not None:
            workflow_spec = next(
                (
                    item
                    for item in list_enabled_workflow_runtime_specs()
                    if str(item.get("skill_id", "")).strip() == run.skill_id
                ),
                None,
            )
        mcp_client = req.app.get("mcp_client")
        if decision == "approve" and (run is None or workflow_spec is None or mcp_client is None):
            return web.json_response(
                {
                    "error": (
                        "The workflow approval could not be executed because the installed workflow runtime "
                        "or MCP bridge is unavailable."
                    )
                },
                status=503,
            )
        if workflow_spec is not None and mcp_client is not None:
            async def _tool_invoker(tool_name: str, arguments: dict) -> object:
                tool_result = await mcp_client.call_tool(
                    tool_name,
                    arguments,
                    skill_id=run.skill_id if run is not None else "",
                )
                return _coerce_mcp_tool_result(tool_result)

            resumed_run, events = await runtime.resume_isolated_workflow(
                spec=workflow_spec,
                run_id=run_id,
                request_id=request_id,
                response=response,
                tool_invoker=_tool_invoker,
            )
            pending = runtime.store.get_request(request_id)
            lifecycle = runtime.approval_service.get_by_action_id(request_id)
            skill_name = resumed_run.workflow_name or run.skill_id if run is not None else "Skill workflow"
            reply_text = _render_workflow_decision_reply(
                approved=True,
                workflow_name=skill_name,
                events=[asdict(event) for event in events],
            )
            orchestrator = req.app.get("orchestrator")
            if reply_text and orchestrator is not None and resumed_run.conversation_id:
                localizer = getattr(orchestrator, "localize_workflow_message", None)
                if callable(localizer):
                    reply_text = await localizer(
                        user_id=user_id,
                        conversation_id=resumed_run.conversation_id,
                        source_text=reply_text,
                        phase="executed",
                        skill_name=str(skill_name),
                    )
                persist = getattr(orchestrator, "persist_with_vector_memory", None)
                if callable(persist):
                    await persist(user_id, "assistant", reply_text, conversation_id=resumed_run.conversation_id)
            return web.json_response(
                {
                    "run_id": run_id,
                    "request_id": request_id,
                    "status": lifecycle.status if lifecycle is not None else pending.status if pending is not None else "approved",
                    "approved": True,
                    "workflow_status": resumed_run.status,
                    "events": [asdict(event) for event in events],
                    "reply": reply_text,
                    "conversation_id": resumed_run.conversation_id,
                }
            )

        pending = runtime.resolve_human_approval(
            run_id=run_id,
            request_id=request_id,
            response=response,
        )
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=404)
    lifecycle = runtime.approval_service.get_by_action_id(request_id)
    run = runtime.store.get_run(run_id)
    reply_text = _render_workflow_decision_reply(
        approved=bool(pending.response and pending.response.approved),
        workflow_name=run.workflow_name if run is not None else "Skill workflow",
        events=[],
    )
    orchestrator = req.app.get("orchestrator")
    if reply_text and orchestrator is not None and run is not None and run.conversation_id:
        persist = getattr(orchestrator, "persist_with_vector_memory", None)
        if callable(persist):
            await persist(user_id, "assistant", reply_text, conversation_id=run.conversation_id)
    return web.json_response(
        {
            "run_id": run_id,
            "request_id": request_id,
            "status": lifecycle.status if lifecycle is not None else pending.status,
            "approved": bool(pending.response and pending.response.approved),
            "workflow_status": (runtime.store.get_run(run_id).status if runtime.store.get_run(run_id) else ""),
            "reply": reply_text,
            "conversation_id": run.conversation_id if run is not None else "",
        }
    )


async def desktop_conversations_handler(req: web.Request) -> web.Response:
    """Lists conversations for the desktop user."""
    orchestrator = req.app["orchestrator"]
    user_id = _desktop_user_id(req.query.get("user_id"))
    query = str(req.query.get("q", "")).strip()
    convs = orchestrator.memory.list_conversations(user_id, query=query)
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
    marker = getattr(orchestrator.memory, "mark_conversation_viewed", None)
    if callable(marker):
        marker(user_id, conv_id)
    loader = getattr(orchestrator.memory, "get_conversation_message_records", None)
    if callable(loader):
        msgs = loader(conv_id)
    else:
        msgs = orchestrator.memory.get_conversation_messages(conv_id)
    return web.json_response(
        {
            "messages": _enrich_history_with_approval_status(
                msgs,
                orchestrator=orchestrator,
                user_id=user_id,
                conversation_id=conv_id,
            )
        }
    )


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
    skill_configs = payload.get("skill_configs", {})
    azure_config = skill_configs.get("Azure", {}) if isinstance(skill_configs, dict) else {}
    if isinstance(azure_config, dict):
        endpoint = str(azure_config.get("endpoint", "")).strip()
        if endpoint:
            try:
                azure_config["endpoint"] = normalize_azure_openai_profile_endpoint(endpoint)
            except ValueError as error:
                return web.json_response({"error": str(error)}, status=400)
    result = save_hatching_profile(payload)
    apply_hatching_azure_runtime_settings()
    try:
        sync_profile = dict(result)
        if isinstance(skill_configs, dict) and "Azure" in skill_configs:
            sync_skill_configs = dict(result.get("skill_configs", {}))
            sync_skill_configs["Azure"] = azure_config
            sync_profile["skill_configs"] = sync_skill_configs
        sync_runtime_models_from_azure_profile(req.app.get("runtime_manager"), sync_profile)
        invalidate_runtime_model_caches(req.app.get("runtime_manager"))
    except Exception as error:
        LOGGER.warning("[Runtime] model sync after hatching save failed: %s", error)
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
    return web.json_response(summarize_job(job))


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


async def desktop_skills_marketplace_handler(_: web.Request) -> web.Response:
    """Returns marketplace skills with local installation state."""
    try:
        return web.json_response(list_marketplace_skills())
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=500)


async def _reload_skill_runtimes(app: web.Application) -> None:
    """Refreshes connected Marketplace runtimes after skill state changes."""
    mcp_client = app.get("mcp_client")
    if mcp_client is not None and hasattr(mcp_client, "reload_skill_clients"):
        try:
            await mcp_client.reload_skill_clients()
        except Exception as error:
            LOGGER.warning("[Skills] MCP skill runtime reload failed: %s", error)
    invalidate_runtime_model_caches(app.get("runtime_manager"))
    await _reload_channel_connector_runtime(app)


async def _reload_channel_connector_runtime(app: web.Application) -> None:
    """Rebuilds Bot Framework adapter and Service Bus worker from enabled channel skills."""
    try:
        runtime_config = load_runtime_config(Path(__file__).resolve().parents[1])
    except Exception as error:
        LOGGER.warning("[Skills] Channel connector config reload failed: %s", error)
        return

    adapter = build_adapter(
        runtime_config.app_id,
        runtime_config.app_password,
        runtime_config.tenant_id,
    )
    app["runtime_config"] = runtime_config
    app["adapter"] = adapter

    existing_worker = app.pop("servicebus_worker", None)
    if existing_worker is not None:
        try:
            await existing_worker.stop()
        except Exception as error:
            LOGGER.warning("[Skills] Existing Service Bus worker stop failed: %s", error)

    if not runtime_config.service_bus_connection_string:
        LOGGER.info("[Skills] Channel connector runtime disabled; no Service Bus connection string configured.")
        return

    orchestrator = app.get("orchestrator")
    if orchestrator is None:
        LOGGER.warning("[Skills] Channel connector runtime unavailable; orchestrator missing.")
        return

    worker = ServiceBusWorker(
        orchestrator=orchestrator,
        adapter=adapter,
        connection_str=runtime_config.service_bus_connection_string,
        inbound_queue=runtime_config.service_bus_inbound_queue,
        outbound_queue=runtime_config.service_bus_outbound_queue,
        use_sessions=runtime_config.service_bus_use_sessions,
        sync_reply_timeout_seconds=runtime_config.bot_sync_reply_timeout_seconds,
        channel_connector_policies=runtime_config.channel_connector_policies or {},
        telegram_allowed_user_ids=runtime_config.telegram_allowed_user_ids,
        telegram_allowed_chat_ids=runtime_config.telegram_allowed_chat_ids,
    )
    await worker.start()
    app["servicebus_worker"] = worker


async def desktop_skills_marketplace_refresh_handler(_: web.Request) -> web.Response:
    """Refreshes the local marketplace catalog from the configured registry."""
    try:
        return web.json_response(refresh_marketplace_catalog())
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=500)
    except OSError as error:
        return web.json_response({"error": str(error)}, status=502)


async def desktop_skills_installed_handler(_: web.Request) -> web.Response:
    """Returns locally installed skills."""
    return web.json_response(list_installed_skills())


async def desktop_skill_runtime_status_handler(req: web.Request) -> web.Response:
    """Returns runtime readiness for enabled Marketplace skills."""
    items = list_remote_agent_runtime_status()
    items.extend(list_channel_connector_runtime_status())
    mcp_client = req.app.get("mcp_client")
    if mcp_client is not None and hasattr(mcp_client, "get_skill_runtime_status"):
        items.extend(mcp_client.get_skill_runtime_status())
    return web.json_response({"items": items})


async def desktop_skill_settings_get_handler(_: web.Request) -> web.Response:
    """Returns user-editable Marketplace settings."""
    return web.json_response(load_skill_marketplace_settings())


async def desktop_skill_settings_put_handler(req: web.Request) -> web.Response:
    """Saves user-editable Marketplace settings."""
    payload = await req.json()
    try:
        return web.json_response(save_skill_marketplace_settings(payload))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)


async def desktop_skill_settings_test_handler(req: web.Request) -> web.Response:
    """Validates the current or draft Skill Registry configuration."""
    payload = {}
    if req.can_read_body:
        try:
            payload = await req.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "JSON body required"}, status=400)
    try:
        return web.json_response(probe_skill_registry(payload))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)


async def desktop_registry_admin_overview_handler(_: web.Request) -> web.Response:
    """Returns Skill Registry admin overview for the configured company registry."""
    try:
        return web.json_response(get_registry_admin_overview())
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)


async def desktop_registry_admin_skills_handler(_: web.Request) -> web.Response:
    """Returns registered skills and latest version state from the company registry."""
    try:
        return web.json_response(list_registry_admin_skills())
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)


async def desktop_registry_admin_skill_versions_handler(req: web.Request) -> web.Response:
    """Returns all known versions for one registry skill."""
    skill_id = req.match_info.get("skill_id", "")
    try:
        return web.json_response(get_registry_admin_skill_versions(skill_id))
    except ValueError as error:
        status = 404 if "not found" in str(error).lower() else 400
        return web.json_response({"error": str(error)}, status=status)


async def desktop_registry_admin_bundle_inspect_handler(req: web.Request) -> web.Response:
    """Loads bundle metadata and manifest preview before publishing it."""
    payload = await req.json()
    try:
        return web.json_response(inspect_skill_bundle(str(payload.get("bundle_path", "")).strip()))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)


async def desktop_registry_admin_publish_handler(req: web.Request) -> web.Response:
    """Publishes a local .azulskill bundle to the configured company registry as draft."""
    payload = await req.json()
    try:
        return web.json_response(
            publish_registry_bundle(
                str(payload.get("bundle_path", "")).strip(),
                published_by=str(payload.get("published_by", "AzulClaw Desktop")).strip() or "AzulClaw Desktop",
            ),
            status=201,
        )
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)


async def desktop_registry_admin_approve_handler(req: web.Request) -> web.Response:
    """Approves one registry skill version."""
    try:
        payload = await req.json() if req.can_read_body else {}
    except json.JSONDecodeError:
        payload = {}
    skill_id = req.match_info.get("skill_id", "")
    version = req.match_info.get("version", "")
    try:
        return web.json_response(
            approve_registry_skill_version(
                skill_id,
                version,
                actor=str(payload.get("actor", "AzulClaw Desktop")).strip() or "AzulClaw Desktop",
            )
        )
    except ValueError as error:
        status = 404 if "not found" in str(error).lower() else 400
        return web.json_response({"error": str(error)}, status=status)


async def desktop_registry_admin_revoke_handler(req: web.Request) -> web.Response:
    """Revokes one registry skill version."""
    try:
        payload = await req.json() if req.can_read_body else {}
    except json.JSONDecodeError:
        payload = {}
    skill_id = req.match_info.get("skill_id", "")
    version = req.match_info.get("version", "")
    try:
        return web.json_response(
            revoke_registry_skill_version(
                skill_id,
                version,
                actor=str(payload.get("actor", "AzulClaw Desktop")).strip() or "AzulClaw Desktop",
            )
        )
    except ValueError as error:
        status = 404 if "not found" in str(error).lower() else 400
        return web.json_response({"error": str(error)}, status=status)


async def desktop_skill_install_handler(req: web.Request) -> web.Response:
    """Installs a marketplace skill into the local runtime state."""
    payload = await req.json()
    try:
        result = install_skill(str(payload.get("skill_id", "")).strip())
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    await _reload_skill_runtimes(req.app)
    return web.json_response(result)


async def desktop_skill_install_bundle_handler(req: web.Request) -> web.Response:
    """Installs a local .azulskill bundle into the local runtime state."""
    payload = await req.json()
    try:
        result = install_skill_bundle(
            Path(str(payload.get("bundle_path", "")).strip()),
            str(payload.get("sha256", "")).strip(),
        )
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    await _reload_skill_runtimes(req.app)
    return web.json_response(result)


async def desktop_skill_asset_handler(req: web.Request) -> web.Response:
    """Serves safe presentation assets declared by a local or installed skill."""
    skill_id = req.match_info.get("skill_id", "")
    asset_path = req.match_info.get("asset_path", "")
    try:
        resolved, content_type = resolve_skill_asset_path(skill_id, asset_path)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=404)
    return web.FileResponse(resolved, headers={"Content-Type": content_type})


async def desktop_skill_config_handler(req: web.Request) -> web.Response:
    """Stores configuration for an installed skill."""
    payload = await req.json()
    skill_id = req.match_info.get("skill_id", "")
    try:
        result = configure_skill(skill_id, payload.get("config", payload))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    await _reload_skill_runtimes(req.app)
    return web.json_response(result)


async def desktop_skill_enabled_handler(req: web.Request) -> web.Response:
    """Enables or disables an installed skill."""
    payload = await req.json()
    skill_id = req.match_info.get("skill_id", "")
    try:
        result = update_skill_enabled(skill_id, bool(payload.get("enabled", False)))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    await _reload_skill_runtimes(req.app)
    return web.json_response(result)


async def desktop_skill_delete_handler(req: web.Request) -> web.Response:
    """Uninstalls a skill from the local runtime state."""
    skill_id = req.match_info.get("skill_id", "")
    try:
        result = uninstall_skill(skill_id)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    await _reload_skill_runtimes(req.app)
    return web.json_response(result)


def register_desktop_routes(app: web.Application) -> None:
    """Registers endpoints consumed by the desktop app."""
    app.router.add_get("/api/health", health_handler)
    app.router.add_get("/api/desktop/backend/status", desktop_backend_status_handler)
    app.router.add_post("/api/desktop/backend/auth/ensure", desktop_backend_auth_ensure_handler)
    app.router.add_post("/api/desktop/azure/connect", desktop_azure_connect_handler)
    app.router.add_post("/api/desktop/azure/key-vault/hydrate", desktop_azure_key_vault_hydrate_handler)
    app.router.add_post("/api/desktop/azure/discovery/subscriptions", desktop_azure_discovery_subscriptions_handler)
    app.router.add_post("/api/desktop/azure/discovery/resources", desktop_azure_discovery_resources_handler)
    app.router.add_post("/api/desktop/azure/discovery/function-apps", desktop_azure_discovery_function_apps_handler)
    app.router.add_post("/api/desktop/azure/discovery/function-app-settings", desktop_azure_discovery_function_app_settings_handler)
    app.router.add_post("/api/desktop/azure/discovery/key-vaults", desktop_azure_discovery_key_vaults_handler)
    app.router.add_post("/api/desktop/azure/discovery/key-vault-secrets", desktop_azure_discovery_key_vault_secrets_handler)
    app.router.add_post("/api/desktop/azure/discovery/deployments", desktop_azure_discovery_deployments_handler)
    app.router.add_post("/api/desktop/attachments", desktop_attachments_post_handler)
    app.router.add_delete("/api/desktop/attachments/{attachment_id}", desktop_attachment_delete_handler)
    app.router.add_post("/api/desktop/chat", desktop_chat_handler)
    app.router.add_post("/api/desktop/chat/stream", desktop_chat_stream_handler)
    app.router.add_post(
        "/api/desktop/workflows/{run_id}/requests/{request_id}/decision",
        desktop_workflow_request_decision_handler,
    )
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
    app.router.add_get("/api/desktop/skills/marketplace", desktop_skills_marketplace_handler)
    app.router.add_post("/api/desktop/skills/marketplace/refresh", desktop_skills_marketplace_refresh_handler)
    app.router.add_get("/api/desktop/skills/installed", desktop_skills_installed_handler)
    app.router.add_get("/api/desktop/skills/runtime", desktop_skill_runtime_status_handler)
    app.router.add_get("/api/desktop/skills/settings", desktop_skill_settings_get_handler)
    app.router.add_put("/api/desktop/skills/settings", desktop_skill_settings_put_handler)
    app.router.add_post("/api/desktop/skills/settings/test", desktop_skill_settings_test_handler)
    app.router.add_get("/api/desktop/registry/overview", desktop_registry_admin_overview_handler)
    app.router.add_get("/api/desktop/registry/skills", desktop_registry_admin_skills_handler)
    app.router.add_get("/api/desktop/registry/skills/{skill_id}/versions", desktop_registry_admin_skill_versions_handler)
    app.router.add_post("/api/desktop/registry/bundles/inspect", desktop_registry_admin_bundle_inspect_handler)
    app.router.add_post("/api/desktop/registry/publish", desktop_registry_admin_publish_handler)
    app.router.add_post("/api/desktop/registry/skills/{skill_id}/versions/{version}/approve", desktop_registry_admin_approve_handler)
    app.router.add_post("/api/desktop/registry/skills/{skill_id}/versions/{version}/revoke", desktop_registry_admin_revoke_handler)
    app.router.add_post("/api/desktop/skills/install", desktop_skill_install_handler)
    app.router.add_post("/api/desktop/skills/install-bundle", desktop_skill_install_bundle_handler)
    app.router.add_get("/api/desktop/skills/{skill_id}/assets/{asset_path:.*}", desktop_skill_asset_handler)
    app.router.add_put("/api/desktop/skills/{skill_id}/config", desktop_skill_config_handler)
    app.router.add_put("/api/desktop/skills/{skill_id}/enabled", desktop_skill_enabled_handler)
    app.router.add_delete("/api/desktop/skills/{skill_id}", desktop_skill_delete_handler)
