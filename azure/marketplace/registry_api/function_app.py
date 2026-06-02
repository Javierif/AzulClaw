import json
import os
from email.parser import BytesParser
from email.policy import default
from typing import Any

import azure.functions as func

from registry_store import (
    artifact_bytes_for_filename,
    build_admin_overview,
    build_public_catalog,
    list_registry_skills,
    list_skill_versions,
    publish_bundle_from_base64,
    publish_bundle_from_path,
    publish_bundle_bytes,
    set_skill_version_approval,
    set_skill_version_status,
)


CONSUMER_KEY_ENV = "AZUL_SKILL_REGISTRY_CONSUMER_KEY"
ADMIN_KEY_ENV = "AZUL_SKILL_REGISTRY_ADMIN_KEY"

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _json_response(body: dict[str, Any], status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body),
        status_code=status_code,
        mimetype="application/json",
    )


def _read_json_body(req: func.HttpRequest) -> dict[str, Any]:
    try:
        payload = req.get_json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _configured_key(name: str) -> str:
    return os.getenv(name, "").strip()


def _request_key(req: func.HttpRequest) -> str:
    header_key = req.headers.get("x-functions-key", "").strip()
    if header_key:
        return header_key
    return req.params.get("code", "").strip()


def _authorize(req: func.HttpRequest, *, admin: bool) -> func.HttpResponse | None:
    consumer_key = _configured_key(CONSUMER_KEY_ENV)
    admin_key = _configured_key(ADMIN_KEY_ENV)
    if not consumer_key and not admin_key:
        return None

    provided = _request_key(req)
    if not provided:
        return _json_response({"error": "Function key required."}, status_code=401)

    if admin:
        if admin_key and provided == admin_key:
            return None
        return _json_response({"error": "Admin function key required."}, status_code=403)

    valid_keys = {key for key in (consumer_key, admin_key) if key}
    if provided in valid_keys:
        return None
    return _json_response({"error": "Invalid function key."}, status_code=403)


def _multipart_file_payload(req: func.HttpRequest) -> tuple[str, bytes] | None:
    content_type = req.headers.get("content-type", "").strip()
    if "multipart/form-data" not in content_type.lower():
        return None
    raw_body = req.get_body()
    if not raw_body:
        return None

    parser = BytesParser(policy=default)
    message = parser.parsebytes(f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw_body)
    if not message.is_multipart():
        return None

    for part in message.iter_parts():
        disposition = part.get_content_disposition()
        if disposition != "form-data":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        return filename, payload
    return None


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=False)
    if auth_error:
        return auth_error
    return _json_response({"status": "ok"})


@app.route(route="catalog", methods=["GET"])
def catalog(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=False)
    if auth_error:
        return auth_error
    return _json_response(build_public_catalog())


@app.route(route="admin/overview", methods=["GET"])
def admin_overview(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=True)
    if auth_error:
        return auth_error
    return _json_response(build_admin_overview())


@app.route(route="skills", methods=["GET"])
def skills(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=True)
    if auth_error:
        return auth_error
    return _json_response(list_registry_skills())


@app.route(route="skills/{skill_id}/versions", methods=["GET"])
def skill_versions(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=True)
    if auth_error:
        return auth_error
    skill_id = req.route_params.get("skill_id", "")
    try:
        return _json_response(list_skill_versions(skill_id))
    except ValueError as error:
        return _json_response({"error": str(error)}, status_code=404)


@app.route(route="skills/publish", methods=["POST"])
def publish(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=True)
    if auth_error:
        return auth_error
    payload = _read_json_body(req)
    status = str(payload.get("status", "draft")).strip() or "draft"
    published_by = str(payload.get("published_by", "")).strip()
    try:
        multipart = _multipart_file_payload(req)
        if multipart is not None:
            filename, bundle_payload = multipart
            result = publish_bundle_bytes(
                bundle_payload,
                filename=filename,
                status=status,
                publish_source="multipart",
                published_by=published_by,
            )
        elif str(payload.get("content_base64", "")).strip():
            result = publish_bundle_from_base64(
                str(payload.get("content_base64", "")),
                filename=str(payload.get("filename", "")),
                status=status,
                published_by=published_by,
            )
        else:
            result = publish_bundle_from_path(
                str(payload.get("bundle_path", "")),
                status=status,
                published_by=published_by,
            )
    except ValueError as error:
        return _json_response({"error": str(error)}, status_code=400)
    return _json_response(result, status_code=201)


@app.route(route="skills/{skill_id}/versions/{version}/approval", methods=["POST"])
def approval(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=True)
    if auth_error:
        return auth_error
    payload = _read_json_body(req)
    skill_id = req.route_params.get("skill_id", "")
    version = req.route_params.get("version", "")
    actor = str(payload.get("actor", "")).strip()
    try:
        result = set_skill_version_approval(skill_id, version, bool(payload.get("approved", True)), actor=actor)
    except ValueError as error:
        return _json_response({"error": str(error)}, status_code=404)
    return _json_response(result)


@app.route(route="skills/{skill_id}/versions/{version}/approve", methods=["POST"])
def approve(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=True)
    if auth_error:
        return auth_error
    payload = _read_json_body(req)
    skill_id = req.route_params.get("skill_id", "")
    version = req.route_params.get("version", "")
    actor = str(payload.get("actor", "")).strip()
    try:
        result = set_skill_version_status(skill_id, version, "approved", actor=actor)
    except ValueError as error:
        return _json_response({"error": str(error)}, status_code=404)
    return _json_response(result)


@app.route(route="skills/{skill_id}/versions/{version}/revoke", methods=["POST"])
def revoke(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=True)
    if auth_error:
        return auth_error
    payload = _read_json_body(req)
    skill_id = req.route_params.get("skill_id", "")
    version = req.route_params.get("version", "")
    actor = str(payload.get("actor", "")).strip()
    try:
        result = set_skill_version_status(skill_id, version, "revoked", actor=actor)
    except ValueError as error:
        return _json_response({"error": str(error)}, status_code=404)
    return _json_response(result)


@app.route(route="artifacts/{filename}", methods=["GET"])
def artifact(req: func.HttpRequest) -> func.HttpResponse:
    auth_error = _authorize(req, admin=False)
    if auth_error:
        return auth_error
    filename = req.route_params.get("filename", "")
    try:
        artifact_name, payload = artifact_bytes_for_filename(filename)
    except ValueError as error:
        return _json_response({"error": str(error)}, status_code=400)
    except FileNotFoundError:
        return _json_response({"error": "Artifact not found."}, status_code=404)
    return func.HttpResponse(
        body=payload,
        status_code=200,
        mimetype="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact_name}"',
            "X-Azul-Skill-Artifact": artifact_name,
        },
    )
