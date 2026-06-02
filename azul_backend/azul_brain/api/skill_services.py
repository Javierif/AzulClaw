"""Skill marketplace and installation state helpers."""

from __future__ import annotations

import json
import os
import hashlib
import mimetypes
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..channels.access_control import parse_csv_allowlist


SKILL_MANIFEST_FILENAME = "azul.skill.json"
SKILL_BUNDLE_SUFFIX = ".azulskill"
SKILL_STATE_FILENAME = "installed_skills.json"
SKILL_PACKAGES_DIR_NAME = "packages"
SKILL_DOWNLOADS_DIR_NAME = "downloads"
SKILL_SETTINGS_FILENAME = "settings.json"
SKILL_REGISTRY_CACHE_FILENAME = "registry_catalog.json"
SECRET_REDACTION = "<configured>"
VALID_SKILL_KINDS = {
    "local_mcp",
    "remote_agent",
    "knowledge",
    "workflow",
    "channel_connector",
}
VALID_RUNTIME_KINDS = {"none", "mcp", "remote_agent"}
VALID_WORKFLOW_MODES = {"built_in_template", "isolated_process"}
VALID_REGISTRY_AUTH_MODES = {"none", "function_key"}
VALID_PRESENTATION_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
EXPECTED_RUNTIME_BY_KIND = {
    "local_mcp": {"mcp"},
    "remote_agent": {"remote_agent"},
    "knowledge": {"none"},
    "workflow": {"none"},
    "channel_connector": {"none"},
}


@dataclass(frozen=True)
class SkillManifestRef:
    """A loaded skill manifest and its source directory."""

    path: Path
    manifest: dict[str, Any]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _skills_root() -> Path:
    override = os.environ.get("AZUL_SKILLS_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return _project_root() / "skills"


def _runtime_root() -> Path:
    override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return _project_root() / "memory"


def _skills_state_dir() -> Path:
    return _runtime_root() / "skills"


def _skill_packages_dir() -> Path:
    return _skills_state_dir() / SKILL_PACKAGES_DIR_NAME


def _skill_downloads_dir() -> Path:
    return _skills_state_dir() / SKILL_DOWNLOADS_DIR_NAME


def _installed_state_path() -> Path:
    return _skills_state_dir() / SKILL_STATE_FILENAME


def _registry_cache_path() -> Path:
    return _skills_state_dir() / SKILL_REGISTRY_CACHE_FILENAME


def _skill_settings_path() -> Path:
    return _skills_state_dir() / SKILL_SETTINGS_FILENAME


def _utc_now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_env_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").upper()
    return normalized or "VALUE"


def _validate_relative_skill_path(source: str, label: str, value: object) -> str:
    candidate = str(value or "").strip().replace("\\", "/")
    if not candidate:
        raise ValueError(f"{source} workflow.{label} must be a non-empty relative path.")
    path = PurePosixPath(candidate)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{source} workflow.{label} must stay inside the skill directory.")
    return candidate


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _empty_state() -> dict[str, Any]:
    return {"schema_version": "1.0", "skills": {}}


def load_installed_skill_state() -> dict[str, Any]:
    """Loads installed skill state from the local runtime directory."""
    path = _installed_state_path()
    if not path.exists():
        return _empty_state()
    try:
        data = _read_json_file(path)
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data.get("skills"), dict):
        return _empty_state()
    return data


def save_installed_skill_state(state: dict[str, Any]) -> dict[str, Any]:
    """Persists installed skill state."""
    cleaned = {
        "schema_version": "1.0",
        "skills": state.get("skills", {}) if isinstance(state.get("skills"), dict) else {},
    }
    _write_json_file(_installed_state_path(), cleaned)
    return cleaned


def _normalize_registry_url(value: object) -> str:
    registry_url = str(value or "").strip()
    if not registry_url:
        return ""
    parsed = urlparse(registry_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Skill Registry URL must be an absolute HTTP or HTTPS URL.")
    return registry_url.rstrip("/")


def _normalize_registry_auth_mode(value: object) -> str:
    auth_mode = str(value or "none").strip()
    if auth_mode not in VALID_REGISTRY_AUTH_MODES:
        raise ValueError("Skill Registry auth mode must be 'none' or 'function_key'.")
    return auth_mode


def _default_private_marketplace_settings() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "registry_url": "",
        "registry_auth_mode": "none",
        "registry_consumer_key": "",
        "registry_admin_key": "",
    }


def _resolve_marketplace_settings(
    payload: dict[str, Any] | None = None,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalizes editable registry settings and merges saved secrets when needed."""
    base = dict(current or _default_private_marketplace_settings())
    source = payload if isinstance(payload, dict) else {}

    if payload is None:
        registry_url = _normalize_registry_url(base.get("registry_url", ""))
        auth_mode = _normalize_registry_auth_mode(base.get("registry_auth_mode", "none"))
        consumer_key = str(
            base.get("registry_consumer_key", base.get("registry_function_key", ""))
        ).strip()
        admin_key = str(base.get("registry_admin_key", base.get("registry_function_key", ""))).strip()
    else:
        registry_url = _normalize_registry_url(source.get("registry_url", base.get("registry_url", "")))
        auth_mode = _normalize_registry_auth_mode(source.get("registry_auth_mode", base.get("registry_auth_mode", "none")))
        consumer_key = str(source.get("registry_consumer_key", source.get("registry_function_key", ""))).strip()
        admin_key = str(source.get("registry_admin_key", "")).strip()
        if auth_mode == "function_key" and not consumer_key:
            consumer_key = str(
                base.get("registry_consumer_key", base.get("registry_function_key", ""))
            ).strip()
        if auth_mode == "function_key" and not admin_key:
            admin_key = str(base.get("registry_admin_key", base.get("registry_function_key", ""))).strip()

    if not registry_url:
        auth_mode = "none"
        consumer_key = ""
        admin_key = ""
    elif auth_mode == "none":
        consumer_key = ""
        admin_key = ""

    return {
        "schema_version": "1.0",
        "registry_url": registry_url,
        "registry_auth_mode": auth_mode,
        "registry_consumer_key": consumer_key,
        "registry_admin_key": admin_key,
    }


def load_skill_marketplace_settings() -> dict[str, Any]:
    """Loads user-editable Marketplace settings."""
    path = _skill_settings_path()
    if not path.exists():
        return {
            "schema_version": "1.0",
            "registry_url": "",
            "registry_auth_mode": "none",
            "registry_consumer_key_configured": False,
            "registry_admin_key_configured": False,
        }
    try:
        data = _read_json_file(path)
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": "1.0",
            "registry_url": "",
            "registry_auth_mode": "none",
            "registry_consumer_key_configured": False,
            "registry_admin_key_configured": False,
        }
    auth_mode = _normalize_registry_auth_mode(data.get("registry_auth_mode", "none"))
    consumer_key = str(data.get("registry_consumer_key", data.get("registry_function_key", ""))).strip()
    admin_key = str(data.get("registry_admin_key", "")).strip()
    return {
        "schema_version": "1.0",
        "registry_url": _normalize_registry_url(data.get("registry_url", "")),
        "registry_auth_mode": auth_mode,
        "registry_consumer_key_configured": auth_mode == "function_key" and bool(consumer_key),
        "registry_admin_key_configured": auth_mode == "function_key" and bool(admin_key),
        "updated_at": str(data.get("updated_at", "")),
    }


def _load_private_skill_marketplace_settings() -> dict[str, Any]:
    """Loads Marketplace settings including locally stored secrets."""
    path = _skill_settings_path()
    if not path.exists():
        return _default_private_marketplace_settings()
    try:
        data = _read_json_file(path)
    except (OSError, json.JSONDecodeError):
        return _default_private_marketplace_settings()
    settings = _resolve_marketplace_settings(
        data if isinstance(data, dict) else {},
        current=_default_private_marketplace_settings(),
    )
    settings["updated_at"] = str(data.get("updated_at", "")) if isinstance(data, dict) else ""
    return settings


def save_skill_marketplace_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Persists user-editable Marketplace settings."""
    settings = _resolve_marketplace_settings(payload, current=_load_private_skill_marketplace_settings())
    settings["updated_at"] = _utc_now()
    _write_json_file(_skill_settings_path(), settings)
    return load_skill_marketplace_settings()


def _registry_key(settings: dict[str, Any], role: str) -> str:
    if settings.get("registry_auth_mode") != "function_key":
        return ""
    consumer = str(settings.get("registry_consumer_key", settings.get("registry_function_key", ""))).strip()
    admin = str(settings.get("registry_admin_key", "")).strip()
    if role == "admin":
        return admin or str(settings.get("registry_function_key", "")).strip()
    return consumer or admin or str(settings.get("registry_function_key", "")).strip()


def _registry_request(
    url: str,
    settings: dict[str, Any] | None = None,
    *,
    role: str = "consumer",
) -> str | Request:
    settings = settings or _load_private_skill_marketplace_settings()
    function_key = _registry_key(settings, role)
    if not function_key:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return url
    return Request(url, headers={"x-functions-key": function_key})


def probe_skill_registry(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Checks whether the configured Skill Registry can be reached and parsed."""
    settings = (
        _load_private_skill_marketplace_settings()
        if payload is None
        else _resolve_marketplace_settings(payload, current=_load_private_skill_marketplace_settings())
    )
    registry_url = str(settings.get("registry_url", "")).strip()
    auth_mode = str(settings.get("registry_auth_mode", "none")).strip()
    consumer_key = _registry_key(settings, "consumer")
    admin_key = _registry_key(settings, "admin")
    result = {
        "status": "local_only",
        "registry_url": registry_url,
        "registry_auth_mode": auth_mode,
        "registry_consumer_key_configured": bool(consumer_key),
        "registry_admin_key_configured": bool(admin_key),
        "health_ok": False,
        "catalog_ok": False,
        "checked_at": _utc_now(),
    }

    if not registry_url:
        result["message"] = "No Skill Registry URL configured. AzulClaw will show local official skills only."
        return result

    if auth_mode == "function_key" and not consumer_key:
        result["status"] = "error"
        result["message"] = "Skill Registry auth requires a consumer key, but no key is configured."
        return result

    try:
        health_url = urljoin(registry_url.rstrip("/") + "/", "api/health")
        with urlopen(_registry_request(health_url, settings, role="consumer"), timeout=10) as response:
            response.read()
        result["health_ok"] = True

        catalog_url = urljoin(registry_url.rstrip("/") + "/", "api/catalog")
        with urlopen(_registry_request(catalog_url, settings, role="consumer"), timeout=15) as response:
            payload_bytes = response.read()
        catalog = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(catalog, dict) or not isinstance(catalog.get("skills"), list):
            raise ValueError("Skill registry returned an invalid catalog.")
        result["catalog_ok"] = True
        result["status"] = "ok"
        result["registry_name"] = str(catalog.get("registry", "")).strip()
        result["skill_count"] = len(catalog.get("skills", []))
        result["message"] = f"Registry reachable. Catalog exposes {result['skill_count']} skill(s)."
        return result
    except HTTPError as error:
        result["status"] = "error"
        result["message"] = f"Skill Registry request failed with HTTP {error.code}."
        result["error"] = str(error)
        return result
    except (URLError, OSError) as error:
        result["status"] = "error"
        result["message"] = f"Could not reach Skill Registry: {error}"
        result["error"] = str(error)
        return result


def load_cached_registry_catalog() -> dict[str, Any]:
    """Loads the cached registry catalog used by the marketplace."""
    path = _registry_cache_path()
    if not path.exists():
        return {"schema_version": "1.0", "registry": "", "skills": []}
    try:
        data = _read_json_file(path)
    except (OSError, json.JSONDecodeError):
        return {"schema_version": "1.0", "registry": "", "skills": []}
    if not isinstance(data.get("skills"), list):
        return {"schema_version": "1.0", "registry": "", "skills": []}
    return data


def save_registry_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Persists a registry catalog snapshot."""
    skills = catalog.get("skills", [])
    cleaned = {
        "schema_version": str(catalog.get("schema_version", "1.0")),
        "registry": str(catalog.get("registry", "")),
        "skills": [item for item in skills if isinstance(item, dict)] if isinstance(skills, list) else [],
        "refreshed_at": _utc_now(),
    }
    _write_json_file(_registry_cache_path(), cleaned)
    return cleaned


def validate_skill_manifest(manifest: dict[str, Any], path: Path | None = None) -> None:
    """Validates the AzulClaw skill manifest contract.

    This intentionally uses only the standard library so the backend and CI
    validation script do not need an extra JSON Schema dependency.
    """
    source = str(path or SKILL_MANIFEST_FILENAME)
    required = (
        "schema_version",
        "id",
        "name",
        "version",
        "publisher",
        "description",
        "kind",
        "runtime",
        "compatibility",
        "permissions",
    "capabilities",
)
    missing = [field for field in required if field not in manifest]
    if missing:
        raise ValueError(f"{source} is missing required field(s): {', '.join(missing)}")
    if manifest.get("schema_version") != "1.0":
        raise ValueError(f"{source} uses unsupported schema_version.")

    skill_id = str(manifest.get("id", "")).strip()
    if not skill_id or any(char.isupper() for char in skill_id) or " " in skill_id:
        raise ValueError(f"{source} has an invalid id.")

    for field in ("name", "version", "publisher", "description"):
        if not str(manifest.get(field, "")).strip():
            raise ValueError(f"{source} must declare a non-empty {field}.")

    kind = str(manifest.get("kind", "")).strip()
    if kind not in VALID_SKILL_KINDS:
        raise ValueError(f"{source} has unsupported kind '{kind}'.")

    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or not runtime.get("kind"):
        raise ValueError(f"{source} must declare runtime.kind.")
    runtime_kind = str(runtime.get("kind", "")).strip()
    if runtime_kind not in VALID_RUNTIME_KINDS:
        raise ValueError(f"{source} has unsupported runtime.kind '{runtime_kind}'.")
    expected_runtimes = EXPECTED_RUNTIME_BY_KIND.get(kind, set())
    if expected_runtimes and runtime_kind not in expected_runtimes:
        raise ValueError(
            f"{source} kind '{kind}' must use runtime.kind "
            f"{', '.join(sorted(expected_runtimes))}."
        )
    if runtime_kind == "mcp":
        if not str(runtime.get("command", "")).strip():
            raise ValueError(f"{source} MCP runtime must declare command.")
        if "args" in runtime and not isinstance(runtime.get("args"), list):
            raise ValueError(f"{source} MCP runtime args must be an array.")
    if runtime_kind == "remote_agent":
        endpoint = str(runtime.get("endpoint", "")).strip()
        if not endpoint.startswith("https://"):
            raise ValueError(f"{source} remote_agent runtime must declare an HTTPS endpoint.")

    compatibility = manifest.get("compatibility")
    if not isinstance(compatibility, dict) or not str(compatibility.get("azulclaw_min_version", "")).strip():
        raise ValueError(f"{source} must declare compatibility.azulclaw_min_version.")

    config_schema = manifest.get("config_schema", {})
    if config_schema is not None:
        if not isinstance(config_schema, dict):
            raise ValueError(f"{source} config_schema must be an object.")
        properties = config_schema.get("properties", {})
        if properties and not isinstance(properties, dict):
            raise ValueError(f"{source} config_schema.properties must be an object.")
        required_fields = config_schema.get("required", [])
        if required_fields and not isinstance(required_fields, list):
            raise ValueError(f"{source} config_schema.required must be an array.")
        if isinstance(required_fields, list) and isinstance(properties, dict):
            missing_required_properties = [
                str(field)
                for field in required_fields
                if str(field) not in properties
            ]
            if missing_required_properties:
                raise ValueError(
                    f"{source} config_schema.required references undefined field(s): "
                    f"{', '.join(missing_required_properties)}"
                )

    secrets = manifest.get("secrets", [])
    if secrets and not isinstance(secrets, list):
        raise ValueError(f"{source} secrets must be an array.")
    for secret in secrets:
        if not isinstance(secret, dict) or not str(secret.get("name", "")).strip():
            raise ValueError(f"{source} secrets entries must declare name.")

    permissions = manifest.get("permissions")
    if not isinstance(permissions, dict):
        raise ValueError(f"{source} permissions must be an object.")
    sensitive_actions = permissions.get("sensitive_actions", [])
    if sensitive_actions and not isinstance(sensitive_actions, list):
        raise ValueError(f"{source} permissions.sensitive_actions must be an array.")

    workflow = manifest.get("workflow")
    if workflow is not None:
        if not isinstance(workflow, dict):
            raise ValueError(f"{source} workflow must be an object.")
        mode = str(workflow.get("mode", "")).strip()
        if mode not in VALID_WORKFLOW_MODES:
            raise ValueError(f"{source} workflow.mode must be built_in_template or isolated_process.")
        if not str(workflow.get("protocol_version", "")).strip():
            raise ValueError(f"{source} workflow.protocol_version is required.")

        workflow_sensitive_actions = workflow.get("sensitive_actions", [])
        if workflow_sensitive_actions and not isinstance(workflow_sensitive_actions, list):
            raise ValueError(f"{source} workflow.sensitive_actions must be an array.")
        declared_sensitive_actions = {str(item).strip() for item in sensitive_actions if str(item).strip()}
        unknown_sensitive_actions = sorted(
            str(item).strip()
            for item in workflow_sensitive_actions
            if str(item).strip() and str(item).strip() not in declared_sensitive_actions
        )
        if unknown_sensitive_actions:
            raise ValueError(
                f"{source} workflow.sensitive_actions must be declared in permissions.sensitive_actions: "
                f"{', '.join(unknown_sensitive_actions)}"
            )

        tools = workflow.get("tools", {})
        if tools and not isinstance(tools, dict):
            raise ValueError(f"{source} workflow.tools must be an object.")
        if isinstance(tools, dict):
            for tool_label, tool_name in tools.items():
                if not str(tool_label).strip() or not str(tool_name).strip():
                    raise ValueError(f"{source} workflow.tools entries must map non-empty names.")

        tool_policies = workflow.get("tool_policies", {})
        if tool_policies and not isinstance(tool_policies, dict):
            raise ValueError(f"{source} workflow.tool_policies must be an object.")
        workflow_sensitive_action_names = {str(item).strip() for item in workflow_sensitive_actions if str(item).strip()}
        if isinstance(tool_policies, dict):
            tool_labels = {str(key).strip() for key in tools} if isinstance(tools, dict) else set()
            for tool_label, policy in tool_policies.items():
                normalized_label = str(tool_label).strip()
                if not normalized_label:
                    raise ValueError(f"{source} workflow.tool_policies entries must have non-empty keys.")
                if tool_labels and normalized_label not in tool_labels:
                    raise ValueError(f"{source} workflow.tool_policies references unknown tool '{normalized_label}'.")
                if not isinstance(policy, dict):
                    raise ValueError(f"{source} workflow.tool_policies.{normalized_label} must be an object.")
                sensitive_action = str(policy.get("sensitive_action", "")).strip()
                if sensitive_action and sensitive_action not in workflow_sensitive_action_names:
                    raise ValueError(
                        f"{source} workflow.tool_policies.{normalized_label}.sensitive_action "
                        "must be declared in workflow.sensitive_actions."
                    )
                if bool(policy.get("requires_approval", False)) and not sensitive_action:
                    raise ValueError(
                        f"{source} workflow.tool_policies.{normalized_label} requires sensitive_action "
                        "when requires_approval=true."
                    )

        capability_prompt = str(workflow.get("capability_prompt", "")).strip()
        if capability_prompt:
            _validate_relative_skill_path(source, "capability_prompt", capability_prompt)

        schemas = workflow.get("schemas", {})
        if schemas and not isinstance(schemas, dict):
            raise ValueError(f"{source} workflow.schemas must be an object.")
        if isinstance(schemas, dict):
            for schema_label, schema_path in schemas.items():
                if not str(schema_label).strip():
                    raise ValueError(f"{source} workflow.schemas entries must have non-empty keys.")
                _validate_relative_skill_path(source, f"schemas.{schema_label}", schema_path)

        if mode == "isolated_process":
            if not bool(permissions.get("process", False)):
                raise ValueError(f"{source} workflow.mode isolated_process requires permissions.process=true.")
            entrypoint = workflow.get("entrypoint")
            if not isinstance(entrypoint, dict):
                raise ValueError(f"{source} workflow.entrypoint must be an object for isolated_process.")
            if not str(entrypoint.get("command", "")).strip():
                raise ValueError(f"{source} workflow.entrypoint.command is required.")
            args = entrypoint.get("args", [])
            if args and not isinstance(args, list):
                raise ValueError(f"{source} workflow.entrypoint.args must be an array.")
            if isinstance(args, list):
                for arg in args:
                    candidate = str(arg).strip()
                    if candidate.endswith(".py") or "/" in candidate or "\\" in candidate:
                        _validate_relative_skill_path(source, "entrypoint.args", candidate)

    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError(f"{source} capabilities must be a non-empty array.")
    for capability in capabilities:
        if not isinstance(capability, dict):
            raise ValueError(f"{source} capability entries must be objects.")
        if not str(capability.get("id", "")).strip() or not str(capability.get("description", "")).strip():
            raise ValueError(f"{source} capability entries must declare id and description.")

    presentation = manifest.get("presentation", {})
    if presentation:
        if not isinstance(presentation, dict):
            raise ValueError(f"{source} presentation must be an object.")
        banner = presentation.get("banner", {})
        if banner and not isinstance(banner, dict):
            raise ValueError(f"{source} presentation.banner must be an object.")
        if isinstance(banner, dict):
            variant = str(banner.get("variant", "")).strip()
            if variant and variant not in {"default", "desktop", "gemini", "telegram", "blueprint", "agent", "channel"}:
                raise ValueError(f"{source} presentation.banner.variant is unsupported.")
            accent = str(banner.get("accent", "")).strip()
            if accent and (len(accent) != 7 or not accent.startswith("#")):
                raise ValueError(f"{source} presentation.banner.accent must be a hex color.")


def validate_skill_manifest_path(manifest_path: Path) -> dict[str, Any]:
    """Loads and validates a skill manifest from disk."""
    manifest = _read_json_file(manifest_path)
    validate_skill_manifest(manifest, manifest_path)
    return manifest


def load_official_skill_manifests(skills_root: Path | None = None) -> list[SkillManifestRef]:
    """Loads first-party skill manifests from ``skills/official``."""
    root = skills_root or _skills_root()
    official_root = root / "official"
    if not official_root.exists():
        return []

    manifests: list[SkillManifestRef] = []
    for manifest_path in sorted(official_root.glob(f"*/{SKILL_MANIFEST_FILENAME}")):
        manifest = validate_skill_manifest_path(manifest_path)
        manifests.append(SkillManifestRef(path=manifest_path.parent, manifest=manifest))
    return manifests


def load_installed_package_manifests() -> list[SkillManifestRef]:
    """Loads manifests extracted from installed .azulskill bundles."""
    packages_root = _skill_packages_dir()
    if not packages_root.exists():
        return []

    manifests: list[SkillManifestRef] = []
    for manifest_path in sorted(packages_root.glob(f"*/*/{SKILL_MANIFEST_FILENAME}")):
        manifest = validate_skill_manifest_path(manifest_path)
        manifests.append(SkillManifestRef(path=manifest_path.parent, manifest=manifest))
    return manifests


def load_available_skill_manifests() -> list[SkillManifestRef]:
    """Loads official and locally packaged manifests."""
    official = load_official_skill_manifests()
    packages = load_installed_package_manifests()
    return official + [
        package_ref
        for package_ref in packages
        if not any(ref.manifest["id"] == package_ref.manifest["id"] for ref in official)
    ]


def _manifest_by_id(skill_id: str) -> SkillManifestRef | None:
    normalized = skill_id.strip()
    for manifest_ref in load_available_skill_manifests():
        if str(manifest_ref.manifest.get("id", "")).strip() == normalized:
            return manifest_ref
    return None


def _required_config_fields(manifest: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    config_schema = manifest.get("config_schema")
    if not isinstance(config_schema, dict):
        config_schema = {}
    required = config_schema.get("required", [])
    if isinstance(required, list):
        fields.extend(str(item).strip() for item in required if str(item).strip())
    for secret in _secret_descriptors(manifest):
        if secret.get("required") and str(secret.get("field", "")).strip():
            fields.append(str(secret["field"]).strip())
    deduped: list[str] = []
    for field in fields:
        if field and field not in deduped:
            deduped.append(field)
    return deduped


def _secret_descriptors(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    properties = {}
    config_schema = manifest.get("config_schema")
    if isinstance(config_schema, dict) and isinstance(config_schema.get("properties"), dict):
        properties = config_schema.get("properties", {})
    for secret in manifest.get("secrets", []):
        if not isinstance(secret, dict):
            continue
        name = str(secret.get("name", "")).strip()
        if not name:
            continue
        field = str(secret.get("config_field", "")).strip() or name
        if field in seen_fields:
            continue
        property_schema = properties.get(field, {}) if isinstance(properties, dict) else {}
        title = str(secret.get("title", "")).strip() or (
            str(property_schema.get("title", "")).strip() if isinstance(property_schema, dict) else ""
        )
        descriptors.append(
            {
                "name": name,
                "field": field,
                "title": title,
                "description": str(secret.get("description", "")).strip(),
                "required": bool(secret.get("required", False)),
            }
        )
        seen_fields.add(field)
    return descriptors


def _secret_field_map(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("field", "")).strip(): str(item.get("name", "")).strip()
        for item in _secret_descriptors(manifest)
        if str(item.get("field", "")).strip() and str(item.get("name", "")).strip()
    }


def _stored_secret_values(config: object) -> dict[str, str]:
    if not isinstance(config, dict):
        return {}
    values = config.get("_secret_values", {})
    if isinstance(values, dict):
        cleaned = {
            str(key).strip(): str(value)
            for key, value in values.items()
            if str(key).strip() and str(value).strip()
        }
        if cleaned:
            return cleaned
    legacy_refs = config.get("_secret_refs", {})
    if isinstance(legacy_refs, dict):
        return {
            str(key).strip(): SECRET_REDACTION
            for key, value in legacy_refs.items()
            if value and str(key).strip()
        }
    return {}


def _secret_config_fields(manifest: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    config_schema = manifest.get("config_schema")
    if isinstance(config_schema, dict):
        properties = config_schema.get("properties", {})
        if isinstance(properties, dict):
            for key, value in properties.items():
                if isinstance(value, dict) and str(value.get("format", "")).lower() == "password":
                    fields.add(str(key))
    for secret in _secret_descriptors(manifest):
        if str(secret.get("field", "")).strip():
            fields.add(str(secret["field"]).strip())
    return {field for field in fields if field}


def _sanitize_config_for_storage(
    manifest: dict[str, Any],
    raw_config: object,
    existing_config: object | None = None,
) -> dict[str, Any]:
    if not isinstance(raw_config, dict):
        return {}
    secret_fields = _secret_config_fields(manifest)
    secret_field_map = _secret_field_map(manifest)
    config: dict[str, Any] = {}
    secret_values = _stored_secret_values(existing_config)
    for key, value in raw_config.items():
        field = str(key).strip()
        if not field:
            continue
        text = str(value).strip()
        if field in secret_fields:
            if text:
                secret_name = secret_field_map.get(field, field)
                secret_values[secret_name] = text
            continue
        if text:
            config[field] = text
    if secret_values:
        config["_secret_values"] = secret_values
    return config


def _redact_config(config: object, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    redacted = {
        str(key): value
        for key, value in config.items()
        if key not in {"_secret_refs", "_secret_values"}
    }
    secret_values = _stored_secret_values(config)
    if manifest is not None:
        secret_field_map = _secret_field_map(manifest)
        for field, secret_name in secret_field_map.items():
            if secret_name in secret_values or field in secret_values:
                redacted[field] = SECRET_REDACTION
    else:
        for key in secret_values:
            redacted[str(key)] = SECRET_REDACTION
    return redacted


def _missing_required_fields(manifest: dict[str, Any], installed: dict[str, Any] | None) -> list[str]:
    if installed is None:
        return _required_config_fields(manifest)
    config = installed.get("config", {}) if isinstance(installed, dict) else {}
    secret_values = _stored_secret_values(config)
    secret_field_map = _secret_field_map(manifest)
    missing: list[str] = []
    for field in _required_config_fields(manifest):
        has_plain = isinstance(config, dict) and bool(str(config.get(field, "")).strip())
        secret_name = secret_field_map.get(field, field)
        has_secret = bool(str(secret_values.get(secret_name, "")).strip())
        if not has_plain and not has_secret:
            missing.append(field)
    return missing


def _summarize_secrets(manifest: dict[str, Any], installed_config: object) -> list[dict[str, Any]]:
    secret_values = _stored_secret_values(installed_config)
    items: list[dict[str, Any]] = []
    for secret in _secret_descriptors(manifest):
        secret_name = str(secret.get("name", "")).strip()
        field = str(secret.get("field", "")).strip()
        items.append(
            {
                "name": secret_name,
                "field": field,
                "title": str(secret.get("title", "")).strip(),
                "description": str(secret.get("description", "")).strip(),
                "required": bool(secret.get("required", False)),
                "configured": bool(str(secret_values.get(secret_name, "")).strip()),
            }
        )
    return items


def _install_manifest_ref(
    ref: SkillManifestRef,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = load_installed_skill_state()
    installed_by_id = state.setdefault("skills", {})
    skill_id = str(ref.manifest["id"])
    now = _utc_now()
    current = installed_by_id.get(skill_id, {})
    if not isinstance(current, dict):
        current = {}
    next_installed = {
        "version": ref.manifest["version"],
        "enabled": bool(current.get("enabled", False)),
        "config": current.get("config", {}) if isinstance(current.get("config", {}), dict) else {},
        "grants": current.get("grants", {}) if isinstance(current.get("grants", {}), dict) else {},
        "installed_at": current.get("installed_at", now),
        "updated_at": now,
    }
    if source:
        next_installed["source"] = source
    installed_by_id[skill_id] = next_installed
    save_installed_skill_state(state)
    return _summarize_skill(ref, next_installed)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(_project_root()))
    except ValueError:
        return str(path)


def _runtime_resource_path(manifest_ref: SkillManifestRef) -> str:
    manifest = manifest_ref.manifest
    activation = manifest.get("activation", {})
    if isinstance(activation, dict):
        relay_path = str(activation.get("relay_function_path", "")).strip()
        if relay_path:
            candidate = (manifest_ref.path / relay_path).resolve()
            if candidate.exists():
                return _display_path(candidate)
    runtime = manifest.get("runtime", {})
    if not isinstance(runtime, dict):
        return ""
    candidates: list[str] = []
    command = str(runtime.get("command", "")).strip()
    if command:
        candidates.append(command)
    args = runtime.get("args", [])
    if isinstance(args, list):
        candidates.extend(str(item).strip() for item in args if str(item).strip())
    for item in candidates:
        candidate = (manifest_ref.path / item).resolve()
        if candidate.exists():
            return _display_path(candidate.parent if candidate.is_file() else candidate)
    return ""


def _deployment_summary(manifest_ref: SkillManifestRef) -> dict[str, Any]:
    skill_root = manifest_ref.path.resolve()
    readme_path = skill_root / "README.md"
    docs_path = skill_root / "docs"
    infra_path = skill_root / "infra" / "terraform"
    runtime_path = _runtime_resource_path(manifest_ref)
    summary: dict[str, Any] = {
        "skill_root_path": _display_path(skill_root),
    }
    if readme_path.exists():
        summary["readme_path"] = _display_path(readme_path)
    if docs_path.exists():
        summary["docs_path"] = _display_path(docs_path)
    if infra_path.exists():
        summary["infra_path"] = _display_path(infra_path)
    if runtime_path:
        summary["runtime_path"] = runtime_path
    return summary


def _requires_external_deployment(manifest: dict[str, Any]) -> bool:
    activation = manifest.get("activation", {})
    if isinstance(activation, dict) and activation.get("requires_azure_relay") is True:
        return True
    kind = str(manifest.get("kind", "")).strip()
    return kind in {"remote_agent", "channel_connector"}


def _local_skill_status(installed: dict[str, Any] | None, missing: list[str]) -> str:
    if not isinstance(installed, dict):
        return "available"
    if bool(installed.get("enabled", False)):
        return "enabled"
    if missing:
        return "installed"
    return "configured"


def _summarize_skill(manifest_ref: SkillManifestRef, installed: dict[str, Any] | None) -> dict[str, Any]:
    manifest = manifest_ref.manifest
    runtime = manifest.get("runtime", {})
    missing = _missing_required_fields(manifest, installed)
    installed_config = installed.get("config", {}) if isinstance(installed, dict) else {}
    source_kind = "package" if manifest_ref.path.is_relative_to(_skill_packages_dir()) else "official"
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "publisher": manifest["publisher"],
        "description": manifest["description"],
        "kind": manifest["kind"],
        "runtime_kind": runtime.get("kind", ""),
        "categories": manifest.get("categories", []),
        "tags": manifest.get("tags", []),
        "config_schema": manifest.get("config_schema", {}),
        "secrets": _summarize_secrets(manifest, installed_config),
        "activation": manifest.get("activation", {}) if isinstance(manifest.get("activation", {}), dict) else {},
        "workflow": manifest.get("workflow", {}) if isinstance(manifest.get("workflow", {}), dict) else {},
        "deployment": _deployment_summary(manifest_ref),
        "presentation": manifest.get("presentation", {}),
        "permissions": manifest.get("permissions", {}),
        "capabilities": manifest.get("capabilities", []),
        "source": {
            "kind": source_kind,
            "path": str(manifest_ref.path.relative_to(_project_root()))
            if manifest_ref.path.is_relative_to(_project_root())
            else str(manifest_ref.path),
        },
        "registry_status": "official",
        "local_status": _local_skill_status(installed, missing),
        "external_deployment_required": _requires_external_deployment(manifest),
        "installed": installed is not None,
        "enabled": bool(installed.get("enabled", False)) if isinstance(installed, dict) else False,
        "configured": not missing,
        "missing_required_fields": missing,
        "config": _redact_config(installed_config, manifest),
        "installed_at": str(installed.get("installed_at", "")) if isinstance(installed, dict) else "",
        "updated_at": str(installed.get("updated_at", "")) if isinstance(installed, dict) else "",
    }


def _catalog_entry_runtime_kind(entry: dict[str, Any]) -> str:
    runtime = entry.get("runtime", {})
    if isinstance(runtime, dict) and runtime.get("kind"):
        return str(runtime.get("kind", ""))
    return str(entry.get("runtime_kind", ""))


def _summarize_registry_skill(entry: dict[str, Any], installed: dict[str, Any] | None) -> dict[str, Any]:
    manifest_like = {
        "config_schema": entry.get("config_schema", {}),
        "secrets": entry.get("secrets", []),
    }
    missing = _missing_required_fields(manifest_like, installed)
    installed_config = installed.get("config", {}) if isinstance(installed, dict) else {}
    artifact = entry.get("artifact", {}) if isinstance(entry.get("artifact", {}), dict) else {}
    return {
        "id": str(entry.get("id", "")),
        "name": str(entry.get("name", "")),
        "version": str(entry.get("version", "")),
        "publisher": str(entry.get("publisher", "")),
        "description": str(entry.get("description", "")),
        "kind": str(entry.get("kind", "unknown")),
        "runtime_kind": _catalog_entry_runtime_kind(entry) or "unknown",
        "categories": entry.get("categories", []) if isinstance(entry.get("categories", []), list) else [],
        "tags": entry.get("tags", []) if isinstance(entry.get("tags", []), list) else [],
        "config_schema": entry.get("config_schema", {}) if isinstance(entry.get("config_schema", {}), dict) else {},
        "secrets": _summarize_secrets(manifest_like, installed_config),
        "activation": entry.get("activation", {}) if isinstance(entry.get("activation", {}), dict) else {},
        "workflow": entry.get("workflow", {}) if isinstance(entry.get("workflow", {}), dict) else {},
        "deployment": entry.get("deployment", {}) if isinstance(entry.get("deployment", {}), dict) else {},
        "presentation": entry.get("presentation", {}) if isinstance(entry.get("presentation", {}), dict) else {},
        "permissions": entry.get("permissions", {}) if isinstance(entry.get("permissions", {}), dict) else {},
        "capabilities": entry.get("capabilities", []) if isinstance(entry.get("capabilities", []), list) else [],
        "source": {
            "kind": "registry",
            "registry": str(entry.get("registry", "")),
            "artifact": artifact,
        },
        "registry_status": str(entry.get("status", "approved")).strip() or "approved",
        "local_status": _local_skill_status(installed, missing),
        "external_deployment_required": _requires_external_deployment(entry),
        "installed": installed is not None,
        "enabled": bool(installed.get("enabled", False)) if isinstance(installed, dict) else False,
        "configured": not missing,
        "missing_required_fields": missing,
        "config": _redact_config(installed_config, manifest_like),
        "installed_at": str(installed.get("installed_at", "")) if isinstance(installed, dict) else "",
        "updated_at": str(installed.get("updated_at", "")) if isinstance(installed, dict) else "",
    }


def _registry_entries() -> list[dict[str, Any]]:
    catalog = load_cached_registry_catalog()
    registry_name = str(catalog.get("registry", ""))
    entries: list[dict[str, Any]] = []
    for item in catalog.get("skills", []):
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            continue
        status = str(item.get("status", "")).strip().lower()
        if status and status != "approved":
            continue
        if not status and item.get("approved") is False:
            continue
        entry = dict(item)
        entry.setdefault("status", "approved")
        entry.setdefault("registry", registry_name)
        entries.append(entry)
    return entries


def _registry_entry_by_id(skill_id: str) -> dict[str, Any] | None:
    normalized = skill_id.strip()
    for entry in _registry_entries():
        if str(entry.get("id", "")).strip() == normalized:
            return entry
    return None


def list_marketplace_skills() -> dict[str, Any]:
    """Returns official marketplace skills with local installation state."""
    state = load_installed_skill_state()
    installed_by_id = state.get("skills", {})
    items = [
        _summarize_skill(ref, installed_by_id.get(ref.manifest["id"]))
        for ref in load_official_skill_manifests()
    ]
    existing_ids = {item["id"] for item in items}
    for entry in _registry_entries():
        skill_id = str(entry.get("id", ""))
        if skill_id in existing_ids:
            continue
        items.append(_summarize_registry_skill(entry, installed_by_id.get(skill_id)))
    return {"items": items}


def list_installed_skills() -> dict[str, Any]:
    """Returns installed skill summaries."""
    state = load_installed_skill_state()
    installed_by_id = state.get("skills", {})
    manifests = {ref.manifest["id"]: ref for ref in load_available_skill_manifests()}
    items = []
    for skill_id, installed in sorted(installed_by_id.items()):
        ref = manifests.get(skill_id)
        if ref is None:
            items.append(
                {
                    "id": skill_id,
                    "name": skill_id,
                    "version": str(installed.get("version", "")) if isinstance(installed, dict) else "",
                    "publisher": "",
                    "description": "Installed skill manifest is no longer available.",
                    "kind": "unknown",
                    "runtime_kind": "unknown",
                    "installed": True,
                    "enabled": bool(installed.get("enabled", False)) if isinstance(installed, dict) else False,
                    "configured": False,
                    "missing_required_fields": [],
                    "config": _redact_config(installed.get("config", {}) if isinstance(installed, dict) else {}),
                }
            )
            continue
        items.append(_summarize_skill(ref, installed if isinstance(installed, dict) else {}))
    return {"items": items}


def _validate_bundle_member_name(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ValueError(f"Skill bundle contains unsafe path '{name}'.")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"Skill bundle contains unsafe path '{name}'.")
    return relative


def resolve_skill_asset_path(skill_id: str, asset_path: str) -> tuple[Path, str]:
    """Resolves a safe presentation asset path for an available skill."""
    ref = _manifest_by_id(skill_id)
    if ref is None:
        raise ValueError(f"Skill '{skill_id}' was not found.")

    raw_path = asset_path.strip()
    if not raw_path or "\\" in raw_path:
        raise ValueError("Invalid skill asset path.")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Invalid skill asset path.")
    suffix = Path(relative.name).suffix.lower()
    if suffix not in VALID_PRESENTATION_ASSET_SUFFIXES:
        raise ValueError("Unsupported skill asset type.")

    root = ref.path.resolve()
    resolved = (ref.path / Path(*relative.parts)).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("Skill asset was not found.")
    content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return resolved, content_type


def _resolve_skill_declared_file(ref: SkillManifestRef, relative_path: str, label: str) -> Path:
    safe_path = _validate_relative_skill_path(str(ref.path / SKILL_MANIFEST_FILENAME), label, relative_path)
    relative = PurePosixPath(safe_path)
    root = ref.path.resolve()
    resolved = (ref.path / Path(*relative.parts)).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"Skill declared {label} was not found.")
    return resolved


def get_skill_workflow_capability_prompt(skill_id: str) -> str:
    """Loads the workflow capability prompt declared by a skill manifest."""
    ref = _manifest_by_id(skill_id)
    if ref is None:
        return ""
    workflow = ref.manifest.get("workflow", {})
    if not isinstance(workflow, dict):
        return ""
    prompt_path = str(workflow.get("capability_prompt", "")).strip()
    if not prompt_path:
        return ""
    resolved = _resolve_skill_declared_file(ref, prompt_path, "capability_prompt")
    try:
        return resolved.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _safe_remove_package_dir(path: Path) -> None:
    packages_root = _skill_packages_dir().resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(packages_root):
        raise ValueError(f"Refusing to remove package directory outside {packages_root}.")
    if resolved.exists():
        shutil.rmtree(resolved)


def _extract_skill_bundle(bundle_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    with zipfile.ZipFile(bundle_path) as bundle:
        names = [info.filename for info in bundle.infolist() if not info.is_dir()]
        if SKILL_MANIFEST_FILENAME not in names:
            raise ValueError(f"Skill bundle must contain {SKILL_MANIFEST_FILENAME} at its root.")
        for info in bundle.infolist():
            if info.is_dir():
                _validate_bundle_member_name(info.filename.rstrip("/"))
                continue
            relative = _validate_bundle_member_name(info.filename)
            target = destination / Path(*relative.parts)
            resolved_target = target.resolve()
            if not resolved_target.is_relative_to(destination_root):
                raise ValueError(f"Skill bundle contains unsafe path '{info.filename}'.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def refresh_marketplace_catalog(registry_url: str | None = None) -> dict[str, Any]:
    """Fetches the configured Skill Registry catalog and updates the local cache."""
    settings = _load_private_skill_marketplace_settings()
    base_url = _normalize_registry_url(registry_url if registry_url is not None else settings.get("registry_url", ""))
    if not base_url:
        return list_marketplace_skills()

    catalog_url = urljoin(base_url.rstrip("/") + "/", "api/catalog")
    with urlopen(_registry_request(catalog_url, settings, role="consumer"), timeout=15) as response:
        payload = response.read()
    catalog = json.loads(payload.decode("utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog.get("skills"), list):
        raise ValueError("Skill registry returned an invalid catalog.")
    save_registry_catalog(catalog)
    return list_marketplace_skills()


def _require_registry_url(settings: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    resolved = settings or _load_private_skill_marketplace_settings()
    base_url = _normalize_registry_url(resolved.get("registry_url", ""))
    if not base_url:
        raise ValueError("Skill Registry URL is not configured.")
    return resolved, base_url


def _require_registry_admin_access(settings: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    resolved, base_url = _require_registry_url(settings)
    if resolved.get("registry_auth_mode") == "function_key" and not _registry_key(resolved, "admin"):
        raise ValueError("Skill Registry admin key is not configured.")
    return resolved, base_url


def _decode_json_response(payload: bytes, *, error_message: str) -> dict[str, Any]:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(error_message) from error
    if not isinstance(data, dict):
        raise ValueError(error_message)
    return data


def _registry_api_json(
    relative_path: str,
    *,
    role: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings, base_url = _require_registry_admin_access() if role == "admin" else _require_registry_url()
    url = urljoin(base_url.rstrip("/") + "/", relative_path.lstrip("/"))
    body: bytes | None = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    wrapped = _registry_request(url, settings, role=role)
    if isinstance(wrapped, Request):
        request = Request(url, data=body, method=method)
        for key, value in dict(wrapped.header_items()).items():
            request.add_header(key, value)
        for key, value in headers.items():
            request.add_header(key, value)
    else:
        request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return _decode_json_response(response.read(), error_message="Skill Registry returned invalid JSON.")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, dict) and parsed.get("error"):
                raise ValueError(str(parsed["error"])) from error
        except json.JSONDecodeError:
            pass
        raise ValueError(f"Skill Registry request failed with HTTP {error.code}.") from error
    except URLError as error:
        raise ValueError(f"Could not reach Skill Registry: {error}") from error


def inspect_skill_bundle(bundle_path: str | Path) -> dict[str, Any]:
    path = Path(bundle_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"Bundle path was not found: {path}")
    if path.suffix.lower() != SKILL_BUNDLE_SUFFIX:
        raise ValueError(f"Bundle must use the {SKILL_BUNDLE_SUFFIX} extension.")
    with zipfile.ZipFile(path) as bundle:
        try:
            manifest_bytes = bundle.read(SKILL_MANIFEST_FILENAME)
        except KeyError as error:
            raise ValueError(f"Skill bundle must contain {SKILL_MANIFEST_FILENAME}.") from error
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("Skill manifest inside bundle is invalid JSON.") from error
        validate_skill_manifest(manifest, path)
        file_count = sum(1 for item in bundle.infolist() if not item.is_dir())
    return {
        "filename": path.name,
        "bundle_path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "files": file_count,
        "manifest": manifest,
    }


def _multipart_registry_request(
    relative_path: str,
    *,
    filename: str,
    file_bytes: bytes,
    fields: dict[str, str],
) -> dict[str, Any]:
    settings, base_url = _require_registry_admin_access()
    url = urljoin(base_url.rstrip("/") + "/", relative_path.lstrip("/"))
    boundary = f"azulskill-{hashlib.sha256((filename + _utc_now()).encode('utf-8')).hexdigest()[:20]}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="bundle"; filename="{filename}"\r\n'.encode("utf-8"),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    body = b"".join(chunks)
    headers = {
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    wrapped = _registry_request(url, settings, role="admin")
    request = Request(url, data=body, method="POST")
    if isinstance(wrapped, Request):
        for key, value in dict(wrapped.header_items()).items():
            request.add_header(key, value)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urlopen(request, timeout=60) as response:
            return _decode_json_response(response.read(), error_message="Skill Registry returned invalid JSON.")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, dict) and parsed.get("error"):
                raise ValueError(str(parsed["error"])) from error
        except json.JSONDecodeError:
            pass
        raise ValueError(f"Skill Registry publish failed with HTTP {error.code}.") from error
    except URLError as error:
        raise ValueError(f"Could not reach Skill Registry: {error}") from error


def get_registry_admin_overview() -> dict[str, Any]:
    return _registry_api_json("api/admin/overview", role="admin")


def list_registry_admin_skills() -> dict[str, Any]:
    return _registry_api_json("api/skills", role="admin")


def get_registry_admin_skill_versions(skill_id: str) -> dict[str, Any]:
    normalized = str(skill_id or "").strip()
    if not normalized:
        raise ValueError("Skill id is required.")
    return _registry_api_json(f"api/skills/{quote(normalized)}/versions", role="admin")


def publish_registry_bundle(bundle_path: str | Path, *, published_by: str = "AzulClaw Desktop") -> dict[str, Any]:
    inspection = inspect_skill_bundle(bundle_path)
    path = Path(str(inspection["bundle_path"]))
    return _multipart_registry_request(
        "api/skills/publish",
        filename=str(inspection["filename"]),
        file_bytes=path.read_bytes(),
        fields={
            "status": "draft",
            "published_by": published_by,
        },
    )


def approve_registry_skill_version(
    skill_id: str,
    version: str,
    *,
    actor: str = "AzulClaw Desktop",
) -> dict[str, Any]:
    normalized_skill_id = str(skill_id or "").strip()
    normalized_version = str(version or "").strip()
    if not normalized_skill_id or not normalized_version:
        raise ValueError("Skill id and version are required.")
    return _registry_api_json(
        f"api/skills/{quote(normalized_skill_id)}/versions/{quote(normalized_version)}/approve",
        role="admin",
        method="POST",
        payload={"actor": actor},
    )


def revoke_registry_skill_version(
    skill_id: str,
    version: str,
    *,
    actor: str = "AzulClaw Desktop",
) -> dict[str, Any]:
    normalized_skill_id = str(skill_id or "").strip()
    normalized_version = str(version or "").strip()
    if not normalized_skill_id or not normalized_version:
        raise ValueError("Skill id and version are required.")
    return _registry_api_json(
        f"api/skills/{quote(normalized_skill_id)}/versions/{quote(normalized_version)}/revoke",
        role="admin",
        method="POST",
        payload={"actor": actor},
    )


def _artifact_filename(entry: dict[str, Any]) -> str:
    artifact = entry.get("artifact", {})
    if not isinstance(artifact, dict):
        raise ValueError("Registry skill is missing artifact metadata.")
    filename = str(artifact.get("filename", "")).strip()
    if not filename or filename != Path(filename).name or not filename.endswith(SKILL_BUNDLE_SUFFIX):
        raise ValueError("Registry skill artifact has an invalid filename.")
    return filename


def _prefer_current_python_interpreter(candidate: str) -> str:
    launcher = candidate.strip().lower()
    if launcher not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return candidate
    current = str(sys.executable or "").strip()
    if not current:
        return candidate
    return current


def _resolve_runtime_string_path(skill_root: Path, value: str, *, prefer_python_interpreter: bool = False) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return candidate
    path = Path(candidate)
    if path.is_absolute():
        return str(path)
    resolved = (skill_root / path).resolve()
    if resolved.exists():
        return str(resolved)
    if prefer_python_interpreter and len(path.parts) == 1:
        return _prefer_current_python_interpreter(candidate)
    return candidate


def _skill_runtime_env(skill_id: str, skill_name: str, skill_root: Path, installed: dict[str, Any]) -> dict[str, str]:
    env = {
        "AZUL_SKILL_ID": skill_id,
        "AZUL_SKILL_NAME": skill_name,
        "AZUL_SKILL_ROOT": str(skill_root.resolve()),
    }
    config = installed.get("config", {}) if isinstance(installed, dict) else {}
    if isinstance(config, dict):
        for key, value in config.items():
            if key in {"_secret_refs", "_secret_values"}:
                continue
            if value == SECRET_REDACTION:
                continue
            if isinstance(value, bool):
                env[f"AZUL_SKILL_CONFIG_{_normalize_env_name(key)}"] = "true" if value else "false"
            elif isinstance(value, (int, float, str)):
                env[f"AZUL_SKILL_CONFIG_{_normalize_env_name(key)}"] = str(value)
        for key, value in _stored_secret_values(config).items():
            if str(value).strip() and value != SECRET_REDACTION:
                env[f"AZUL_SKILL_SECRET_{_normalize_env_name(str(key))}"] = str(value)
            env[f"AZUL_SKILL_SECRET_{_normalize_env_name(str(key))}_CONFIGURED"] = "true"
    return env


def _resolve_remote_agent_endpoint(manifest: dict[str, Any], installed: dict[str, Any]) -> str:
    config = installed.get("config", {}) if isinstance(installed, dict) else {}
    configured = str(config.get("endpoint", "")).strip() if isinstance(config, dict) else ""
    if configured:
        return configured
    runtime = manifest.get("runtime", {})
    if not isinstance(runtime, dict):
        return ""
    return str(runtime.get("endpoint", "")).strip()


def _remote_agent_auth_headers(
    manifest: dict[str, Any],
    installed: dict[str, Any],
) -> tuple[dict[str, str], str | None]:
    runtime = manifest.get("runtime", {})
    if not isinstance(runtime, dict):
        return {}, None
    auth = runtime.get("auth", {})
    if not isinstance(auth, dict):
        return {}, None
    auth_type = str(auth.get("type", "none")).strip().lower() or "none"
    if auth_type == "none":
        return {}, None
    if auth_type != "api_key":
        return {}, f"Remote agent auth type '{auth_type}' is not supported yet."
    secret_name = str(auth.get("secret_name", "")).strip()
    if not secret_name:
        descriptors = _secret_descriptors(manifest)
        if descriptors:
            secret_name = str(descriptors[0].get("name", "")).strip()
    secret_value = _stored_secret_values(installed.get("config", {})).get(secret_name, "")
    if not str(secret_value).strip() or secret_value == SECRET_REDACTION:
        return {}, f"Missing configured secret for remote agent auth: {secret_name or 'api key'}."
    header_name = str(auth.get("header", "")).strip() or "x-api-key"
    if header_name.lower() == "authorization":
        prefix = str(auth.get("prefix", "")).strip() or "Bearer"
        return {header_name: f"{prefix} {secret_value}"}, None
    return {header_name: secret_value}, None


def list_enabled_remote_agent_runtime_specs() -> list[dict[str, Any]]:
    """Returns enabled remote-agent skills resolved into callable HTTP specs."""
    state = load_installed_skill_state()
    installed_by_id = state.get("skills", {})
    specs: list[dict[str, Any]] = []
    for ref in load_available_skill_manifests():
        skill_id = str(ref.manifest.get("id", "")).strip()
        installed = installed_by_id.get(skill_id)
        if not isinstance(installed, dict) or not installed.get("enabled", False):
            continue
        runtime = ref.manifest.get("runtime", {})
        if not isinstance(runtime, dict) or str(runtime.get("kind", "")).strip() != "remote_agent":
            continue
        endpoint = _resolve_remote_agent_endpoint(ref.manifest, installed)
        headers, auth_error = _remote_agent_auth_headers(ref.manifest, installed)
        message = ""
        status = "connected"
        if not endpoint.startswith("https://"):
            status = "error"
            message = "Remote agent endpoint must be configured with HTTPS."
        elif auth_error:
            status = "error"
            message = auth_error
        else:
            message = f"Ready to call {endpoint}."
        specs.append(
            {
                "skill_id": skill_id,
                "skill_name": str(ref.manifest.get("name", skill_id)).strip() or skill_id,
                "endpoint": endpoint,
                "headers": headers,
                "status": status,
                "message": message,
                "description": str(ref.manifest.get("description", "")).strip(),
            }
        )
    return specs


def list_remote_agent_runtime_status() -> list[dict[str, Any]]:
    """Summarizes enabled remote-agent readiness for the Marketplace UI."""
    return [
        {
            "skill_id": item["skill_id"],
            "skill_name": item["skill_name"],
            "status": item["status"],
            "tool_count": 1,
            "message": item["message"],
        }
        for item in list_enabled_remote_agent_runtime_specs()
    ]


def list_enabled_channel_connector_runtime_specs() -> list[dict[str, Any]]:
    """Returns enabled channel-connector skills resolved into runtime-facing specs."""
    state = load_installed_skill_state()
    installed_by_id = state.get("skills", {})
    specs: list[dict[str, Any]] = []
    for ref in load_available_skill_manifests():
        skill_id = str(ref.manifest.get("id", "")).strip()
        installed = installed_by_id.get(skill_id)
        if not isinstance(installed, dict) or not installed.get("enabled", False):
            continue
        if str(ref.manifest.get("kind", "")).strip() != "channel_connector":
            continue
        permissions = ref.manifest.get("permissions", {})
        channels = permissions.get("channels", []) if isinstance(permissions, dict) else []
        activation = ref.manifest.get("activation", {})
        config = installed.get("config", {}) if isinstance(installed, dict) else {}
        message_parts: list[str] = []
        if isinstance(activation, dict) and activation.get("requires_azure_relay"):
            relay_path = str(activation.get("relay_function_path", "")).strip()
            if relay_path:
                message_parts.append(f"Azure relay required at {relay_path}.")
            else:
                message_parts.append("Azure relay deployment required.")
        if skill_id == "dev.azulclaw.telegram":
            user_count = len(parse_csv_allowlist(str(config.get("allowedUserIds", ""))))
            chat_count = len(parse_csv_allowlist(str(config.get("allowedChatIds", ""))))
            if user_count or chat_count:
                message_parts.append(f"Allowlists configured: {user_count} users, {chat_count} chats.")
            else:
                message_parts.append("No Telegram allowlists configured.")
        if not message_parts:
            message_parts.append("Channel connector configured and ready.")
        specs.append(
            {
                "skill_id": skill_id,
                "skill_name": str(ref.manifest.get("name", skill_id)).strip() or skill_id,
                "channels": channels if isinstance(channels, list) else [],
                "config": config if isinstance(config, dict) else {},
                "activation": activation if isinstance(activation, dict) else {},
                "status": "connected",
                "message": " ".join(message_parts),
            }
        )
    return specs


def list_channel_connector_runtime_status() -> list[dict[str, Any]]:
    """Summarizes enabled channel connector readiness for the Marketplace UI."""
    return [
        {
            "skill_id": item["skill_id"],
            "skill_name": item["skill_name"],
            "status": item["status"],
            "tool_count": len(item.get("channels", [])) or 1,
            "message": item["message"],
        }
        for item in list_enabled_channel_connector_runtime_specs()
    ]


async def invoke_remote_agent(skill_id: str, prompt: str, context: dict[str, Any] | None = None) -> str:
    """Calls an enabled Marketplace remote agent skill through its HTTPS endpoint."""
    import aiohttp

    normalized_skill_id = skill_id.strip()
    if not normalized_skill_id:
        raise ValueError("Remote agent skill id is required.")
    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        raise ValueError("Remote agent prompt is required.")
    spec = next(
        (item for item in list_enabled_remote_agent_runtime_specs() if item["skill_id"] == normalized_skill_id),
        None,
    )
    if spec is None:
        raise ValueError(f"Remote agent skill '{normalized_skill_id}' is not enabled.")
    if spec.get("status") != "connected":
        raise ValueError(str(spec.get("message", "Remote agent is not ready.")))

    payload = {
        "skill_id": spec["skill_id"],
        "skill_name": spec["skill_name"],
        "prompt": prompt_text,
        "context": context if isinstance(context, dict) else {},
    }
    headers = {
        "Accept": "application/json, text/plain;q=0.9",
        "Content-Type": "application/json",
        "User-Agent": "AzulClaw/Marketplace",
        **(spec.get("headers", {}) if isinstance(spec.get("headers", {}), dict) else {}),
    }
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(str(spec["endpoint"]), json=payload, headers=headers) as response:
            body = await response.text()
            if response.status >= 400:
                detail = body.strip()[:300] or f"HTTP {response.status}"
                raise ValueError(f"Remote agent '{normalized_skill_id}' returned HTTP {response.status}: {detail}")
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type.lower():
                return body.strip() or f"Remote agent '{normalized_skill_id}' returned an empty response."
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body.strip() or f"Remote agent '{normalized_skill_id}' returned an empty response."
    if isinstance(data, dict):
        for key in ("reply", "output", "response", "text", "content", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(data, ensure_ascii=False)
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False)


def list_enabled_local_mcp_runtime_specs() -> list[dict[str, Any]]:
    """Returns enabled local MCP skills resolved into launchable stdio specs."""
    state = load_installed_skill_state()
    installed_by_id = state.get("skills", {})
    specs: list[dict[str, Any]] = []
    for ref in load_available_skill_manifests():
        skill_id = str(ref.manifest.get("id", "")).strip()
        installed = installed_by_id.get(skill_id)
        if not isinstance(installed, dict) or not installed.get("enabled", False):
            continue
        runtime = ref.manifest.get("runtime", {})
        if not isinstance(runtime, dict) or str(runtime.get("kind", "")).strip() != "mcp":
            continue
        if str(runtime.get("transport", "stdio")).strip() != "stdio":
            continue
        skill_root = ref.path.resolve()
        command = _resolve_runtime_string_path(
            skill_root,
            str(runtime.get("command", "")).strip(),
            prefer_python_interpreter=True,
        )
        args = [
            _resolve_runtime_string_path(skill_root, str(arg).strip())
            for arg in runtime.get("args", [])
            if str(arg).strip()
        ] if isinstance(runtime.get("args", []), list) else []
        specs.append({
            "skill_id": skill_id,
            "skill_name": str(ref.manifest.get("name", skill_id)).strip() or skill_id,
            "command": command,
            "args": args,
            "cwd": str(skill_root),
            "env": _skill_runtime_env(skill_id, str(ref.manifest.get("name", skill_id)), skill_root, installed),
            "source_path": str(skill_root),
        })
    return specs


def list_enabled_workflow_runtime_specs() -> list[dict[str, Any]]:
    """Returns enabled marketplace workflows resolved into isolated launch specs."""
    state = load_installed_skill_state()
    installed_by_id = state.get("skills", {})
    specs: list[dict[str, Any]] = []
    for ref in load_available_skill_manifests():
        skill_id = str(ref.manifest.get("id", "")).strip()
        installed = installed_by_id.get(skill_id)
        if not isinstance(installed, dict) or not installed.get("enabled", False):
            continue
        workflow = ref.manifest.get("workflow", {})
        if not isinstance(workflow, dict):
            continue
        mode = str(workflow.get("mode", "")).strip()
        if mode != "isolated_process":
            continue
        entrypoint = workflow.get("entrypoint", {})
        if not isinstance(entrypoint, dict):
            continue
        skill_root = ref.path.resolve()
        command = _resolve_runtime_string_path(
            skill_root,
            str(entrypoint.get("command", "")).strip(),
            prefer_python_interpreter=True,
        )
        args = [
            _resolve_runtime_string_path(skill_root, str(arg).strip())
            for arg in entrypoint.get("args", [])
            if str(arg).strip()
        ] if isinstance(entrypoint.get("args", []), list) else []
        spec = {
            "skill_id": skill_id,
            "skill_name": str(ref.manifest.get("name", skill_id)).strip() or skill_id,
            "description": str(ref.manifest.get("description", "")).strip(),
            "mode": mode,
            "protocol_version": str(workflow.get("protocol_version", "")).strip(),
            "command": command,
            "args": args,
            "cwd": str(skill_root),
            "env": _skill_runtime_env(skill_id, str(ref.manifest.get("name", skill_id)), skill_root, installed),
            "source_path": str(skill_root),
            "capabilities": ref.manifest.get("capabilities", [])
            if isinstance(ref.manifest.get("capabilities", []), list)
            else [],
            "activation": ref.manifest.get("activation", {})
            if isinstance(ref.manifest.get("activation", {}), dict)
            else {},
            "tools": workflow.get("tools", {}) if isinstance(workflow.get("tools", {}), dict) else {},
            "tool_policies": workflow.get("tool_policies", {})
            if isinstance(workflow.get("tool_policies", {}), dict)
            else {},
            "input_defaults": workflow.get("input_defaults", {})
            if isinstance(workflow.get("input_defaults", {}), dict)
            else {},
            "semantic_grouping": workflow.get("semantic_grouping", {})
            if isinstance(workflow.get("semantic_grouping", {}), dict)
            else {},
            "sensitive_actions": workflow.get("sensitive_actions", [])
            if isinstance(workflow.get("sensitive_actions", []), list)
            else [],
            "schemas": workflow.get("schemas", {}) if isinstance(workflow.get("schemas", {}), dict) else {},
            "capability_prompt": str(workflow.get("capability_prompt", "")).strip(),
            "checkpoint_policy": str(workflow.get("checkpoint_policy", "optional")).strip() or "optional",
        }
        specs.append(spec)
    return specs


def _artifact_sha256(entry: dict[str, Any]) -> str:
    artifact = entry.get("artifact", {})
    if not isinstance(artifact, dict):
        return ""
    return str(artifact.get("sha256", "")).strip()


def _artifact_download_url(entry: dict[str, Any]) -> str:
    artifact = entry.get("artifact", {})
    if not isinstance(artifact, dict):
        raise ValueError("Registry skill is missing artifact metadata.")
    explicit = str(artifact.get("download_url", "")).strip()
    if explicit:
        return explicit
    base_url = str(_load_private_skill_marketplace_settings().get("registry_url", "")).strip()
    if not base_url:
        raise ValueError("Skill Registry URL is not configured.")
    return urljoin(base_url.rstrip("/") + "/", f"api/artifacts/{quote(_artifact_filename(entry))}")


def _download_registry_artifact(entry: dict[str, Any]) -> Path:
    filename = _artifact_filename(entry)
    downloads_dir = _skill_downloads_dir()
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target = (downloads_dir / filename).resolve()
    if target.parent != downloads_dir.resolve():
        raise ValueError("Registry skill artifact has an invalid download target.")
    with urlopen(_registry_request(_artifact_download_url(entry), role="consumer"), timeout=60) as response:
        with target.open("wb") as output:
            shutil.copyfileobj(response, output)
    return target


def install_skill_bundle(bundle_path: Path, expected_sha256: str = "") -> dict[str, Any]:
    """Installs a .azulskill bundle into the local runtime packages directory."""
    bundle_path = bundle_path.expanduser().resolve()
    if not bundle_path.is_file():
        raise ValueError(f"Skill bundle '{bundle_path}' was not found.")
    if bundle_path.suffix != SKILL_BUNDLE_SUFFIX:
        raise ValueError(f"Skill bundle must use the {SKILL_BUNDLE_SUFFIX} extension.")

    expected = expected_sha256.strip().lower()
    actual_sha256 = _sha256_file(bundle_path)
    if expected and actual_sha256.lower() != expected:
        raise ValueError("Skill bundle sha256 does not match expected value.")

    packages_root = _skill_packages_dir()
    staging_root = packages_root / ".staging"
    staging_dir = staging_root / f"{bundle_path.stem}-{_utc_now().replace(':', '').replace('.', '')}"
    _safe_remove_package_dir(staging_dir)
    try:
        _extract_skill_bundle(bundle_path, staging_dir)
        manifest_path = staging_dir / SKILL_MANIFEST_FILENAME
        manifest = validate_skill_manifest_path(manifest_path)
        skill_id = str(manifest["id"])
        version = str(manifest["version"])
        destination = packages_root / skill_id / version
        _safe_remove_package_dir(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging_dir), str(destination))
    finally:
        if staging_dir.exists():
            _safe_remove_package_dir(staging_dir)

    ref = SkillManifestRef(path=destination, manifest=manifest)
    return _install_manifest_ref(
        ref,
        {
            "kind": "package",
            "bundle_path": str(bundle_path),
            "sha256": actual_sha256,
        },
    )


def install_registry_skill(skill_id: str) -> dict[str, Any]:
    """Downloads and installs a skill from the cached registry catalog."""
    entry = _registry_entry_by_id(skill_id)
    if entry is None:
        raise ValueError(f"Skill '{skill_id}' was not found.")
    bundle_path = _download_registry_artifact(entry)
    return install_skill_bundle(bundle_path, _artifact_sha256(entry))


def install_skill(skill_id: str) -> dict[str, Any]:
    """Installs an official skill into local runtime state."""
    ref = _manifest_by_id(skill_id)
    if ref is None:
        return install_registry_skill(skill_id)
    return _install_manifest_ref(ref)


def configure_skill(skill_id: str, raw_config: object) -> dict[str, Any]:
    """Stores non-secret config and secret-reference markers for an installed skill."""
    ref = _manifest_by_id(skill_id)
    if ref is None:
        raise ValueError(f"Skill '{skill_id}' was not found.")
    state = load_installed_skill_state()
    installed_by_id = state.setdefault("skills", {})
    installed = installed_by_id.get(skill_id)
    if not isinstance(installed, dict):
        raise ValueError(f"Skill '{skill_id}' is not installed.")
    installed["config"] = _sanitize_config_for_storage(
        ref.manifest,
        raw_config,
        installed.get("config", {}),
    )
    installed["updated_at"] = _utc_now()
    save_installed_skill_state(state)
    return _summarize_skill(ref, installed)


def update_skill_enabled(skill_id: str, enabled: bool) -> dict[str, Any]:
    """Enables or disables an installed skill."""
    ref = _manifest_by_id(skill_id)
    if ref is None:
        raise ValueError(f"Skill '{skill_id}' was not found.")
    state = load_installed_skill_state()
    installed_by_id = state.setdefault("skills", {})
    installed = installed_by_id.get(skill_id)
    if not isinstance(installed, dict):
        raise ValueError(f"Skill '{skill_id}' is not installed.")
    missing = _missing_required_fields(ref.manifest, installed)
    if enabled and missing:
        raise ValueError(f"Skill '{skill_id}' is missing required config: {', '.join(missing)}")
    installed["enabled"] = bool(enabled)
    installed["updated_at"] = _utc_now()
    save_installed_skill_state(state)
    return _summarize_skill(ref, installed)


def uninstall_skill(skill_id: str) -> dict[str, Any]:
    """Removes an installed skill from local runtime state."""
    state = load_installed_skill_state()
    installed_by_id = state.setdefault("skills", {})
    if skill_id not in installed_by_id:
        raise ValueError(f"Skill '{skill_id}' is not installed.")
    installed_by_id.pop(skill_id, None)
    save_installed_skill_state(state)
    return {"deleted": True, "skill_id": skill_id}
