"""Persistent store for the Skill Registry API.

Supports two backends:

- local JSON metadata + local artifact files for development
- Azure Table Storage + Blob Storage for deployed environments
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from azul_backend.azul_brain.api.skill_services import SKILL_MANIFEST_FILENAME, validate_skill_manifest

REGISTRY_STATE_FILENAME = "registry_state.json"
CATALOG_FILENAME = "catalog.json"
ARTIFACT_DIR_ENV = "AZUL_SKILL_ARTIFACT_DIR"
REGISTRY_NAME_ENV = "AZUL_SKILL_REGISTRY_NAME"
REGISTRY_METADATA_PATH_ENV = "AZUL_SKILL_REGISTRY_METADATA_PATH"
REGISTRY_STORAGE_MODE_ENV = "AZUL_SKILL_REGISTRY_STORAGE_MODE"
AZURE_STORAGE_CONNECTION_STRING_ENV = "AZUL_SKILL_REGISTRY_AZURE_CONNECTION_STRING"
AZURE_STORAGE_ACCOUNT_URL_ENV = "AZUL_SKILL_REGISTRY_AZURE_ACCOUNT_URL"
AZURE_TABLE_NAME_ENV = "AZUL_SKILL_REGISTRY_AZURE_TABLE_NAME"
AZURE_BLOB_CONTAINER_ENV = "AZUL_SKILL_REGISTRY_AZURE_BLOB_CONTAINER"
VALID_VERSION_STATUSES = {"draft", "approved", "revoked"}


def _utc_now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _registry_root() -> Path:
    return Path(__file__).resolve().parent


def _artifact_root() -> Path:
    configured = os.getenv(ARTIFACT_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (_registry_root() / "artifacts").resolve()


def _metadata_path() -> Path:
    configured = os.getenv(REGISTRY_METADATA_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (_registry_root() / REGISTRY_STATE_FILENAME).resolve()


def _registry_name() -> str:
    return os.getenv(REGISTRY_NAME_ENV, "").strip() or "azulclaw-private"


def _storage_mode() -> str:
    configured = os.getenv(REGISTRY_STORAGE_MODE_ENV, "").strip().lower()
    if configured in {"local", "azure"}:
        return configured
    if os.getenv(AZURE_STORAGE_CONNECTION_STRING_ENV, "").strip() or os.getenv(
        AZURE_STORAGE_ACCOUNT_URL_ENV, ""
    ).strip():
        return "azure"
    return "local"


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def empty_registry_state() -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "registry": _registry_name(),
        "storage_backend": _storage_mode(),
        "updated_at": "",
        "skills": {},
    }


def _safe_artifact_filename(filename: str) -> str:
    name = (filename or "").strip()
    if not name or name != Path(name).name or not name.endswith(".azulskill"):
        raise ValueError("Invalid artifact filename.")
    return name


def artifact_path_for_filename(filename: str) -> Path:
    name = _safe_artifact_filename(filename)
    root = _artifact_root()
    candidate = (root / name).resolve()
    if candidate.parent != root:
        raise ValueError("Invalid artifact path.")
    return candidate


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _bundle_file_count(bundle: zipfile.ZipFile) -> int:
    return sum(1 for item in bundle.infolist() if not item.is_dir())


def _load_manifest_from_bundle_bytes(payload: bytes) -> tuple[dict[str, Any], int]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            manifest_bytes = bundle.read(SKILL_MANIFEST_FILENAME)
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            validate_skill_manifest(manifest)
            return manifest, _bundle_file_count(bundle)
    except KeyError as error:
        raise ValueError(f"Skill bundle must contain {SKILL_MANIFEST_FILENAME}.") from error
    except zipfile.BadZipFile as error:
        raise ValueError("Artifact is not a valid .azulskill bundle.") from error
    except json.JSONDecodeError as error:
        raise ValueError("Skill manifest inside bundle is invalid JSON.") from error


def _normalize_status(value: object, *, default: str = "draft") -> str:
    status = str(value or default).strip().lower() or default
    if status not in VALID_VERSION_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Expected one of: {', '.join(sorted(VALID_VERSION_STATUSES))}."
        )
    return status


def _artifact_record(filename: str, payload: bytes, file_count: int) -> dict[str, Any]:
    return {
        "filename": filename,
        "path": filename,
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "files": file_count,
    }


def _version_record(
    manifest: dict[str, Any],
    artifact: dict[str, Any],
    *,
    status: str,
    publish_source: str,
    published_by: str = "",
    approved_by: str = "",
    revoked_by: str = "",
) -> dict[str, Any]:
    runtime = manifest.get("runtime", {})
    deployment = manifest.get("deployment", {}) if isinstance(manifest.get("deployment", {}), dict) else {}
    now = _utc_now()
    approved = status == "approved"
    record = {
        "id": str(manifest["id"]),
        "name": str(manifest["name"]),
        "version": str(manifest["version"]),
        "publisher": str(manifest["publisher"]),
        "description": str(manifest["description"]),
        "kind": str(manifest["kind"]),
        "runtime_kind": str(runtime.get("kind", "")) if isinstance(runtime, dict) else "",
        "runtime": runtime if isinstance(runtime, dict) else {},
        "categories": manifest.get("categories", []) if isinstance(manifest.get("categories", []), list) else [],
        "tags": manifest.get("tags", []) if isinstance(manifest.get("tags", []), list) else [],
        "presentation": manifest.get("presentation", {}) if isinstance(manifest.get("presentation", {}), dict) else {},
        "config_schema": manifest.get("config_schema", {}) if isinstance(manifest.get("config_schema", {}), dict) else {},
        "secrets": manifest.get("secrets", []) if isinstance(manifest.get("secrets", []), list) else [],
        "permissions": manifest.get("permissions", {}) if isinstance(manifest.get("permissions", {}), dict) else {},
        "capabilities": manifest.get("capabilities", []) if isinstance(manifest.get("capabilities", []), list) else [],
        "compatibility": manifest.get("compatibility", {}) if isinstance(manifest.get("compatibility", {}), dict) else {},
        "activation": manifest.get("activation", {}) if isinstance(manifest.get("activation", {}), dict) else {},
        "deployment": deployment,
        "status": status,
        "approved": approved,
        "artifact": artifact,
        "artifact_name": str(artifact.get("filename", "")),
        "artifact_sha256": str(artifact.get("sha256", "")),
        "published_at": now,
        "published_by": published_by,
        "approved_at": now if approved else "",
        "approved_by": approved_by if approved else "",
        "revoked_at": now if status == "revoked" else "",
        "revoked_by": revoked_by if status == "revoked" else "",
        "updated_at": now,
        "publish_source": publish_source,
        "manifest_snapshot": manifest,
    }
    return record


def _public_catalog_entry(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "version": record["version"],
        "publisher": record["publisher"],
        "description": record["description"],
        "kind": record["kind"],
        "runtime_kind": record.get("runtime_kind", ""),
        "runtime": record.get("runtime", {}),
        "categories": record.get("categories", []),
        "tags": record.get("tags", []),
        "presentation": record.get("presentation", {}),
        "config_schema": record.get("config_schema", {}),
        "secrets": record.get("secrets", []),
        "permissions": record.get("permissions", {}),
        "capabilities": record.get("capabilities", []),
        "compatibility": record.get("compatibility", {}),
        "activation": record.get("activation", {}),
        "deployment": record.get("deployment", {}),
        "status": "approved",
        "approved": True,
        "artifact": record.get("artifact", {}),
    }


def _skill_summary(skill_id: str, versions: list[dict[str, Any]]) -> dict[str, Any]:
    latest = versions[0] if versions else {}
    approved = next((item for item in versions if item.get("status") == "approved"), None)
    drafts = sum(1 for item in versions if item.get("status") == "draft")
    revoked = sum(1 for item in versions if item.get("status") == "revoked")
    return {
        "id": skill_id,
        "name": str(latest.get("name", skill_id)),
        "publisher": str(latest.get("publisher", "")),
        "kind": str(latest.get("kind", "")),
        "latest_version": str(latest.get("version", "")),
        "approved_version": str(approved.get("version", "")) if approved else "",
        "version_count": len(versions),
        "draft_count": drafts,
        "revoked_count": revoked,
        "versions": versions,
    }


def _sorted_version_records(version_map: dict[str, Any]) -> list[dict[str, Any]]:
    records = [item for item in version_map.values() if isinstance(item, dict)]
    return sorted(
        records,
        key=lambda item: (str(item.get("published_at", "")), str(item.get("version", ""))),
        reverse=True,
    )


@dataclass
class LocalRegistryBackend:
    def load_state(self) -> dict[str, Any]:
        path = _metadata_path()
        if not path.exists():
            return empty_registry_state()
        try:
            data = _read_json_file(path)
        except (OSError, json.JSONDecodeError):
            return empty_registry_state()
        if not isinstance(data.get("skills"), dict):
            return empty_registry_state()
        data.setdefault("schema_version", "1.1")
        data.setdefault("registry", _registry_name())
        data.setdefault("storage_backend", "local")
        data.setdefault("updated_at", "")
        return data

    def save_state(self, state: dict[str, Any]) -> dict[str, Any]:
        cleaned = empty_registry_state()
        cleaned["registry"] = str(state.get("registry", _registry_name())).strip() or _registry_name()
        cleaned["skills"] = state.get("skills", {}) if isinstance(state.get("skills"), dict) else {}
        cleaned["updated_at"] = _utc_now()
        cleaned["storage_backend"] = "local"
        _write_json_file(_metadata_path(), cleaned)
        return cleaned

    def write_artifact(self, filename: str, payload: bytes) -> dict[str, Any]:
        target = artifact_path_for_filename(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return {
            "filename": target.name,
            "path": target.name,
            "sha256": _sha256_bytes(payload),
            "size_bytes": len(payload),
        }

    def read_artifact(self, filename: str) -> tuple[str, bytes]:
        path = artifact_path_for_filename(filename)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(filename)
        return path.name, path.read_bytes()


@dataclass
class AzureRegistryBackend:
    """Blob/Table-backed registry storage.

    This remains optional at runtime. If the required SDKs are unavailable, the
    backend raises a clear error when selected.
    """

    def __post_init__(self) -> None:
        try:
            from azure.core.exceptions import ResourceExistsError  # type: ignore
            from azure.data.tables import TableServiceClient  # type: ignore
            from azure.identity import DefaultAzureCredential  # type: ignore
            from azure.storage.blob import BlobServiceClient  # type: ignore
        except ImportError as error:
            raise RuntimeError(
                "Azure registry backend requires azure-data-tables, azure-storage-blob, and azure-identity."
            ) from error

        connection_string = os.getenv(AZURE_STORAGE_CONNECTION_STRING_ENV, "").strip() or os.getenv(
            "AzureWebJobsStorage", ""
        ).strip()
        account_url = os.getenv(AZURE_STORAGE_ACCOUNT_URL_ENV, "").strip()
        if connection_string:
            self._table_service = TableServiceClient.from_connection_string(connection_string)
            self._blob_service = BlobServiceClient.from_connection_string(connection_string)
        elif account_url:
            credential = DefaultAzureCredential()
            self._table_service = TableServiceClient(endpoint=account_url, credential=credential)
            self._blob_service = BlobServiceClient(account_url=account_url, credential=credential)
        else:
            raise RuntimeError(
                "Azure registry backend requires either AZUL_SKILL_REGISTRY_AZURE_CONNECTION_STRING/AzureWebJobsStorage "
                "or AZUL_SKILL_REGISTRY_AZURE_ACCOUNT_URL."
            )

        self._table_name = os.getenv(AZURE_TABLE_NAME_ENV, "").strip() or "skillregistry"
        self._container_name = os.getenv(AZURE_BLOB_CONTAINER_ENV, "").strip() or "skill-artifacts"
        self._table_client = self._table_service.get_table_client(self._table_name)
        self._table_client.create_table_if_not_exists()
        self._container_client = self._blob_service.get_container_client(self._container_name)
        try:
            self._container_client.create_container()
        except ResourceExistsError:
            pass

    def load_state(self) -> dict[str, Any]:
        state = empty_registry_state()
        state["storage_backend"] = "azure"
        skills: dict[str, Any] = {}
        entities = self._table_client.list_entities()
        for entity in entities:
            skill_id = str(entity.get("PartitionKey", "")).strip()
            version = str(entity.get("RowKey", "")).strip()
            if not skill_id or not version:
                continue
            manifest_snapshot = entity.get("ManifestJson", "{}")
            artifact_json = entity.get("ArtifactJson", "{}")
            try:
                manifest = json.loads(manifest_snapshot)
            except json.JSONDecodeError:
                manifest = {}
            try:
                artifact = json.loads(artifact_json)
            except json.JSONDecodeError:
                artifact = {}
            record = {
                "id": skill_id,
                "name": str(entity.get("Name", skill_id)),
                "version": version,
                "publisher": str(entity.get("Publisher", "")),
                "description": str(entity.get("Description", "")),
                "kind": str(entity.get("Kind", "")),
                "runtime_kind": str(entity.get("RuntimeKind", "")),
                "runtime": manifest.get("runtime", {}) if isinstance(manifest, dict) else {},
                "categories": manifest.get("categories", []) if isinstance(manifest, dict) else [],
                "tags": manifest.get("tags", []) if isinstance(manifest, dict) else [],
                "presentation": manifest.get("presentation", {}) if isinstance(manifest, dict) else {},
                "config_schema": manifest.get("config_schema", {}) if isinstance(manifest, dict) else {},
                "secrets": manifest.get("secrets", []) if isinstance(manifest, dict) else [],
                "permissions": manifest.get("permissions", {}) if isinstance(manifest, dict) else {},
                "capabilities": manifest.get("capabilities", []) if isinstance(manifest, dict) else [],
                "compatibility": manifest.get("compatibility", {}) if isinstance(manifest, dict) else {},
                "activation": manifest.get("activation", {}) if isinstance(manifest, dict) else {},
                "deployment": manifest.get("deployment", {}) if isinstance(manifest, dict) else {},
                "status": _normalize_status(entity.get("Status", "draft")),
                "approved": _normalize_status(entity.get("Status", "draft")) == "approved",
                "artifact": artifact if isinstance(artifact, dict) else {},
                "artifact_name": str(entity.get("ArtifactName", "")),
                "artifact_sha256": str(entity.get("ArtifactSha256", "")),
                "published_at": str(entity.get("PublishedAt", "")),
                "published_by": str(entity.get("PublishedBy", "")),
                "approved_at": str(entity.get("ApprovedAt", "")),
                "approved_by": str(entity.get("ApprovedBy", "")),
                "revoked_at": str(entity.get("RevokedAt", "")),
                "revoked_by": str(entity.get("RevokedBy", "")),
                "updated_at": str(entity.get("UpdatedAt", "")),
                "publish_source": str(entity.get("PublishSource", "")),
                "manifest_snapshot": manifest if isinstance(manifest, dict) else {},
            }
            bucket = skills.setdefault(
                skill_id,
                {
                    "id": skill_id,
                    "name": record["name"],
                    "publisher": record["publisher"],
                    "kind": record["kind"],
                    "versions": {},
                },
            )
            bucket["name"] = record["name"]
            bucket["publisher"] = record["publisher"]
            bucket["kind"] = record["kind"]
            bucket.setdefault("versions", {})[version] = record
        state["skills"] = skills
        state["updated_at"] = _utc_now()
        return state

    def save_state(self, state: dict[str, Any]) -> dict[str, Any]:
        for skill_id, skill_data in state.get("skills", {}).items():
            if not isinstance(skill_data, dict):
                continue
            versions = skill_data.get("versions", {})
            if not isinstance(versions, dict):
                continue
            for version, record in versions.items():
                if not isinstance(record, dict):
                    continue
                artifact = record.get("artifact", {}) if isinstance(record.get("artifact", {}), dict) else {}
                entity = {
                    "PartitionKey": skill_id,
                    "RowKey": str(version),
                    "Name": str(record.get("name", skill_id)),
                    "Publisher": str(record.get("publisher", "")),
                    "Description": str(record.get("description", "")),
                    "Kind": str(record.get("kind", "")),
                    "RuntimeKind": str(record.get("runtime_kind", "")),
                    "Status": _normalize_status(record.get("status", "draft")),
                    "ArtifactName": str(artifact.get("filename", "")),
                    "ArtifactSha256": str(artifact.get("sha256", "")),
                    "PublishedAt": str(record.get("published_at", "")),
                    "PublishedBy": str(record.get("published_by", "")),
                    "ApprovedAt": str(record.get("approved_at", "")),
                    "ApprovedBy": str(record.get("approved_by", "")),
                    "RevokedAt": str(record.get("revoked_at", "")),
                    "RevokedBy": str(record.get("revoked_by", "")),
                    "UpdatedAt": str(record.get("updated_at", "")),
                    "PublishSource": str(record.get("publish_source", "")),
                    "ArtifactJson": json.dumps(artifact, ensure_ascii=False),
                    "ManifestJson": json.dumps(record.get("manifest_snapshot", {}), ensure_ascii=False),
                }
                self._table_client.upsert_entity(entity, mode="Replace")
        cleaned = empty_registry_state()
        cleaned["registry"] = str(state.get("registry", _registry_name())).strip() or _registry_name()
        cleaned["skills"] = state.get("skills", {}) if isinstance(state.get("skills"), dict) else {}
        cleaned["updated_at"] = _utc_now()
        cleaned["storage_backend"] = "azure"
        return cleaned

    def write_artifact(self, filename: str, payload: bytes) -> dict[str, Any]:
        safe_name = _safe_artifact_filename(filename)
        blob = self._container_client.get_blob_client(safe_name)
        blob.upload_blob(payload, overwrite=True)
        return {
            "filename": safe_name,
            "path": safe_name,
            "sha256": _sha256_bytes(payload),
            "size_bytes": len(payload),
        }

    def read_artifact(self, filename: str) -> tuple[str, bytes]:
        safe_name = _safe_artifact_filename(filename)
        blob = self._container_client.get_blob_client(safe_name)
        downloader = blob.download_blob()
        return safe_name, downloader.readall()


def _backend():
    mode = _storage_mode()
    if mode == "azure":
        return AzureRegistryBackend()
    return LocalRegistryBackend()


def load_registry_state() -> dict[str, Any]:
    return _backend().load_state()


def save_registry_state(state: dict[str, Any]) -> dict[str, Any]:
    return _backend().save_state(state)


def load_bootstrap_catalog() -> dict[str, Any]:
    raw = os.getenv("AZUL_SKILL_REGISTRY_BOOTSTRAP_CATALOG", "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = []
        if isinstance(data, list):
            return {
                "schema_version": "1.0",
                "registry": _registry_name(),
                "skills": [item for item in data if isinstance(item, dict)],
            }
    catalog_path = _registry_root() / CATALOG_FILENAME
    if not catalog_path.exists():
        return {
            "schema_version": "1.0",
            "registry": _registry_name(),
            "skills": [],
        }
    try:
        data = _read_json_file(catalog_path)
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": "1.0",
            "registry": _registry_name(),
            "skills": [],
        }
    if not isinstance(data.get("skills"), list):
        data["skills"] = []
    data.setdefault("schema_version", "1.0")
    data.setdefault("registry", _registry_name())
    return data


def _versions_for_skill(state: dict[str, Any], skill_id: str) -> dict[str, Any]:
    skill = state.get("skills", {}).get(skill_id, {})
    versions = skill.get("versions", {}) if isinstance(skill, dict) else {}
    return versions if isinstance(versions, dict) else {}


def build_public_catalog() -> dict[str, Any]:
    state = load_registry_state()
    published_entries: list[dict[str, Any]] = []
    if state.get("skills"):
        for skill_id, skill_data in state.get("skills", {}).items():
            if not isinstance(skill_data, dict):
                continue
            versions = _sorted_version_records(_versions_for_skill(state, skill_id))
            approved = [item for item in versions if item.get("status") == "approved"]
            if not approved:
                continue
            published_entries.append(_public_catalog_entry(approved[0]))
        published_entries.sort(key=lambda item: item.get("name", "").lower())
        return {
            "schema_version": "1.0",
            "registry": str(state.get("registry", _registry_name())),
            "skills": published_entries,
        }
    return load_bootstrap_catalog()


def list_registry_skills() -> dict[str, Any]:
    state = load_registry_state()
    items: list[dict[str, Any]] = []
    for skill_id, skill_data in sorted(state.get("skills", {}).items()):
        if not isinstance(skill_data, dict):
            continue
        versions = _sorted_version_records(_versions_for_skill(state, skill_id))
        items.append(_skill_summary(skill_id, versions))
    return {
        "schema_version": "1.0",
        "registry": str(state.get("registry", _registry_name())),
        "storage_backend": str(state.get("storage_backend", _storage_mode())),
        "items": items,
    }


def list_skill_versions(skill_id: str) -> dict[str, Any]:
    state = load_registry_state()
    normalized = str(skill_id or "").strip()
    if not normalized:
        raise ValueError("Skill id is required.")
    skill_data = state.get("skills", {}).get(normalized)
    if not isinstance(skill_data, dict):
        raise ValueError(f"Skill '{normalized}' was not found.")
    versions = _sorted_version_records(_versions_for_skill(state, normalized))
    return {
        "schema_version": "1.0",
        "registry": str(state.get("registry", _registry_name())),
        "storage_backend": str(state.get("storage_backend", _storage_mode())),
        "skill": {
            "id": normalized,
            "name": str(skill_data.get("name", normalized)),
            "publisher": str(skill_data.get("publisher", "")),
            "kind": str(skill_data.get("kind", "")),
        },
        "versions": versions,
    }


def build_admin_overview() -> dict[str, Any]:
    listing = list_registry_skills()
    items = listing["items"]
    drafts = sum(item.get("draft_count", 0) for item in items)
    revoked = sum(item.get("revoked_count", 0) for item in items)
    approved = sum(1 for item in items if item.get("approved_version"))
    versions = sum(item.get("version_count", 0) for item in items)
    latest_records = []
    for item in items:
        latest = item.get("versions", [])[0] if item.get("versions") else None
        if isinstance(latest, dict):
            latest_records.append(latest)
    latest_records.sort(key=lambda entry: str(entry.get("published_at", "")), reverse=True)
    return {
        "schema_version": "1.0",
        "registry": listing["registry"],
        "storage_backend": listing.get("storage_backend", _storage_mode()),
        "totals": {
            "skills": len(items),
            "versions": versions,
            "approved_skills": approved,
            "draft_versions": drafts,
            "revoked_versions": revoked,
        },
        "recent_versions": latest_records[:8],
        "items": items,
    }


def _persist_artifact_bytes(filename: str, payload: bytes) -> dict[str, Any]:
    return _backend().write_artifact(filename, payload)


def artifact_bytes_for_filename(filename: str) -> tuple[str, bytes]:
    return _backend().read_artifact(filename)


def publish_bundle_bytes(
    payload: bytes,
    *,
    filename: str,
    publish_source: str = "upload",
    status: str = "draft",
    published_by: str = "",
) -> dict[str, Any]:
    safe_name = _safe_artifact_filename(filename)
    normalized_status = _normalize_status(status)
    manifest, file_count = _load_manifest_from_bundle_bytes(payload)
    persisted_artifact = _persist_artifact_bytes(safe_name, payload)
    artifact = {
        **persisted_artifact,
        "files": file_count,
    }

    state = load_registry_state()
    skill_id = str(manifest["id"])
    version = str(manifest["version"])
    skills = state.setdefault("skills", {})
    skill_bucket = skills.setdefault(
        skill_id,
        {
            "id": skill_id,
            "name": str(manifest["name"]),
            "publisher": str(manifest["publisher"]),
            "kind": str(manifest["kind"]),
            "versions": {},
        },
    )
    skill_bucket["name"] = str(manifest["name"])
    skill_bucket["publisher"] = str(manifest["publisher"])
    skill_bucket["kind"] = str(manifest["kind"])
    versions = skill_bucket.setdefault("versions", {})
    versions[version] = _version_record(
        manifest,
        artifact,
        status=normalized_status,
        publish_source=publish_source,
        published_by=published_by,
        approved_by=published_by if normalized_status == "approved" else "",
        revoked_by=published_by if normalized_status == "revoked" else "",
    )
    if normalized_status == "approved":
        _demote_other_approved_versions(versions, version)
    save_registry_state(state)
    return versions[version]


def publish_bundle_from_base64(
    content_base64: str,
    *,
    filename: str,
    publish_source: str = "base64",
    status: str = "draft",
    published_by: str = "",
) -> dict[str, Any]:
    try:
        payload = base64.b64decode(content_base64.encode("utf-8"), validate=True)
    except ValueError as error:
        raise ValueError("content_base64 is not valid base64.") from error
    return publish_bundle_bytes(
        payload,
        filename=filename,
        publish_source=publish_source,
        status=status,
        published_by=published_by,
    )


def publish_bundle_from_path(
    bundle_path: str,
    *,
    status: str = "draft",
    published_by: str = "",
) -> dict[str, Any]:
    path = Path(str(bundle_path or "").strip()).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"Bundle path was not found: {path}")
    return publish_bundle_bytes(
        path.read_bytes(),
        filename=path.name,
        publish_source=str(path),
        status=status,
        published_by=published_by,
    )


def _demote_other_approved_versions(versions: dict[str, Any], keep_version: str) -> None:
    for version, record in versions.items():
        if version == keep_version or not isinstance(record, dict):
            continue
        if record.get("status") != "approved":
            continue
        record["status"] = "revoked"
        record["approved"] = False
        record["approved_at"] = str(record.get("approved_at", ""))
        record["updated_at"] = _utc_now()


def set_skill_version_status(skill_id: str, version: str, status: str, actor: str = "") -> dict[str, Any]:
    state = load_registry_state()
    normalized_skill_id = str(skill_id or "").strip()
    normalized_version = str(version or "").strip()
    normalized_status = _normalize_status(status)
    versions = _versions_for_skill(state, normalized_skill_id)
    record = versions.get(normalized_version)
    if not isinstance(record, dict):
        raise ValueError(f"Skill version '{normalized_skill_id}@{normalized_version}' was not found.")

    now = _utc_now()
    record["status"] = normalized_status
    record["approved"] = normalized_status == "approved"
    record["updated_at"] = now
    if normalized_status == "approved":
        record["approved_at"] = now
        record["approved_by"] = actor
        record["revoked_at"] = ""
        record["revoked_by"] = ""
        _demote_other_approved_versions(versions, normalized_version)
    elif normalized_status == "revoked":
        record["revoked_at"] = now
        record["revoked_by"] = actor
    save_registry_state(state)
    return record


def set_skill_version_approval(skill_id: str, version: str, approved: bool, actor: str = "") -> dict[str, Any]:
    return set_skill_version_status(skill_id, version, "approved" if approved else "revoked", actor=actor)
