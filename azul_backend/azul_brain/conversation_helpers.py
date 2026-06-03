"""Stateless helper functions shared by the conversation orchestrator.

These functions depend only on their arguments (plus module-level constants),
not on orchestrator runtime state, so they live apart from ``conversation.py``
to keep that module focused on orchestration. They are imported back into
``conversation`` where the orchestrator methods use them.
"""

import json
import random
from datetime import datetime, timezone

from .runtime.approval_protocol import contains_pending_action_block, strip_pending_action_block

PROGRESS_UPDATE_MIN_SECONDS = 10.0
PROGRESS_UPDATE_MAX_SECONDS = 20.0

_PLACEHOLDER_TITLES = frozenset(
    {
        "",
        "new conversation",
        "new chat",
        "main conversation",
    }
)

_MARKDOWN_EMPHASIS_TABLE = str.maketrans("", "", "*_`#")


def _random_progress_delay_seconds() -> float:
    return random.uniform(PROGRESS_UPDATE_MIN_SECONDS, PROGRESS_UPDATE_MAX_SECONDS)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _map_verdict_to_turn_status(status: str) -> str:
    normalized = str(status or "").strip()
    if normalized == "blocking_question":
        return "blocking_question"
    if normalized == "action_pending":
        return "approval_required"
    if normalized == "tool_failure":
        return "tool_failure"
    if normalized == "in_progress":
        return "in_progress"
    return "final_answer"


def extract_result_text(result) -> str:
    """Normalises the agent adapter response to serialisable text."""
    value = getattr(result, "value", None)
    if isinstance(value, str):
        return value
    return str(result)


def _extract_first_tool_text(result) -> str:
    content = getattr(result, "content", None)
    if not content:
        return ""
    first = content[0]
    text = getattr(first, "text", None)
    return text if isinstance(text, str) else str(first)


def _format_folder_organizer_preview_text(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text.startswith("{"):
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text
    relative_path = str(payload.get("relative_path", "")).strip()
    summary = str(payload.get("summary", "")).strip()
    workflow_hint = str(payload.get("workflow_hint", "")).strip()
    parts: list[str] = []
    if relative_path:
        parts.append(f"Folder Organizer preview for `{relative_path}`.")
    if summary:
        parts.append(summary)
    if workflow_hint:
        parts.append(workflow_hint)
    return "\n".join(parts) if parts else text


def _folder_organizer_payload_has_planned_moves(payload: dict[str, object]) -> bool:
    moves = payload.get("moves")
    if isinstance(moves, list):
        return any(isinstance(item, dict) and item.get("status") == "planned" for item in moves)
    return False


def _coerce_semantic_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1"}
    return False


def _folder_organizer_conceptual_taxonomy(*, reply_language: str, semantic_enabled: bool) -> str:
    if reply_language == "es":
        lines = [
            "Plan conceptual recomendado:",
            "- Proyectos Activos: carpetas por iniciativa o cliente, por ejemplo `Proyectos Activos/Cliente - Proyecto`.",
            "- Facturas y Recibos: comprobantes, presupuestos, impuestos y pagos, idealmente separados por anio.",
            "- Clientes y Proveedores: contratos, briefings, comunicaciones exportadas y entregables por entidad.",
            "- Documentos Personales: identificacion, tramites, certificados y documentos administrativos.",
            "- Recursos de Trabajo: plantillas, logos, referencias, capturas y material reutilizable.",
            "- Instaladores y Paquetes: ejecutables, zips, herramientas descargadas y versiones antiguas.",
            "- Archivo Cerrado: proyectos finalizados o material historico que ya no debe mezclarse con trabajo activo.",
        ]
        if not semantic_enabled:
            lines.append(
                "Nota: ahora mismo la ejecucion automatica personalizada depende de que la categorizacion semantica "
                "este activada en la configuracion de la skill; si no, el movimiento ejecutable usa categorias por extension."
            )
        return "\n".join(lines)

    lines = [
        "Recommended conceptual plan:",
        "- Active Projects: one folder per initiative or client, for example `Active Projects/Client - Project`.",
        "- Invoices and Receipts: invoices, quotes, taxes, payments, and proof of purchase grouped by year.",
        "- Clients and Vendors: contracts, briefs, exported communication, and deliverables by organization.",
        "- Personal Documents: IDs, paperwork, certificates, and administrative documents.",
        "- Work Resources: templates, logos, references, screenshots, and reusable material.",
        "- Installers and Packages: executables, zip files, downloaded tools, and old versions.",
        "- Closed Archive: completed projects or historical material that should not mix with active work.",
    ]
    if not semantic_enabled:
        lines.append(
            "Note: applying custom destination names automatically requires semantic categorization to be enabled in the skill settings; "
            "otherwise executable moves use extension-based categories."
        )
    return "\n".join(lines)


def _strip_machine_pending_blocks(text: str) -> str:
    stripped = text or ""
    while contains_pending_action_block(stripped):
        next_value = strip_pending_action_block(stripped)
        if next_value == stripped:
            break
        stripped = next_value
    return stripped.strip()


def _is_placeholder_conversation_title(title: str | None) -> bool:
    """True when the row still has a generic default title (not user-meaningful)."""
    t = (title or "").strip().lower()
    return t in _PLACEHOLDER_TITLES


def _strip_markdown_emphasis(text: str) -> str:
    """Removes Markdown emphasis markers for surfaces that render plain text."""
    return (text or "").translate(_MARKDOWN_EMPHASIS_TABLE).strip()


def should_skip_vectorization(text: str) -> bool:
    """Avoids indexing potentially sensitive text in local vector memory."""
    low = (text or "").lower()
    return (
        "api_key" in low
        or "apikey" in low
        or "token" in low
        or "password" in low
        or "contraseÃ±a" in low
        or "secret" in low
        or "bearer " in low
        or "authorization:" in low
    )
