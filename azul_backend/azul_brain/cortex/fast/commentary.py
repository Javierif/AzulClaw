"""Comentarista rapido y progreso visible para el usuario."""

from __future__ import annotations

import json


def build_commentary(user_message: str, *, reason: str, lane: str) -> str:
    """Fallback heuristico si el cerebro rapido no puede narrar."""
    normalized = " ".join((user_message or "").strip().lower().split())

    if lane == "fast":
        if any(token in normalized for token in ("historia", "cuento", "inventa", "imagina")):
            return "Dejame que lo imagine un segundo. Ya te la voy contando."
        if normalized.endswith("?"):
            return "A ver, voy a responderte al vuelo."
        return "Estoy con ello. Te lo voy soltando sobre la marcha."

    if any(token in normalized for token in ("archivo", "workspace", "carpeta", "documento", "pdf")):
        return "Estoy revisando el contexto y leyendo lo necesario. Enseguida te doy una respuesta completa."
    if any(token in normalized for token in ("codigo", "code", "bug", "error", "traceback", "stacktrace")):
        return "Estoy inspeccionando el problema con mas calma. Primero ordeno el contexto y luego te doy una respuesta cerrada."
    if any(token in normalized for token in ("analiza", "arquitectura", "plan", "estrategia", "detall")):
        return "Estoy pasando esto al cerebro lento para pensarlo bien. Te voy contando y luego te doy la respuesta completa."
    if reason == "long-request":
        return "Estoy desgranando la peticion paso a paso. Enseguida vuelvo con una respuesta completa."
    return "Estoy pensando esto con mas calma. Ahora te doy una respuesta completa."


def prompt_for_fast_visible_commentary(
    user_message: str,
    *,
    reason: str,
    lane: str,
) -> list[dict[str, str]]:
    """Prompt para que el cerebro rapido redacte la primera burbuja visible."""
    lane_label = "cerebro rapido" if lane == "fast" else "cerebro lento"
    return [
        {
            "role": "system",
            "text": (
                "Eres el cerebro rapido visible de AzulClaw. "
                "Devuelve solo JSON valido, sin markdown ni texto extra. "
                'Usa este esquema exacto: {"commentary":""}. '
                "La commentary debe ser una sola frase breve, natural y en espanol. "
                "No des la respuesta final. "
                "No expongas cadena de pensamiento. "
                "Suena como una primera reaccion util del agente mientras trabaja."
            ),
        },
        {
            "role": "user",
            "text": (
                "Genera la primera narracion visible para esta peticion.\n"
                f"Ruta activa: {lane_label}\n"
                f"Motivo de triage: {reason}\n"
                f"Peticion: {user_message}"
            ),
        },
    ]


def build_progress_snapshot(
    user_message: str,
    *,
    reason: str,
    lane: str,
    stage: str,
    tick: int = 0,
    summary: str = "",
    blueprint: dict | None = None,
) -> dict:
    """Construye una vista segura y resumida del progreso interno."""
    selected_blueprint = blueprint or _select_blueprint(user_message, reason=reason, lane=lane)
    phases = _materialize_phases(selected_blueprint["phases"], stage=stage, tick=tick)
    active_count = sum(
        1
        for phase in phases
        for step in phase["steps"]
        if step["status"] != "done"
    )
    return {
        "title": selected_blueprint["title"],
        "summary": summary or selected_blueprint["summary"].get(stage, selected_blueprint["summary"]["thinking"]),
        "badge": selected_blueprint["badge"],
        "active_count": active_count,
        "phases": phases,
    }


def prompt_for_fast_visible_plan(user_message: str, *, reason: str) -> list[dict[str, str]]:
    """Prompt para que el cerebro rapido genere la primera respuesta visible."""
    return [
        {
            "role": "system",
            "text": (
                "Eres el cerebro rapido visible de AzulClaw. "
                "El cerebro lento trabajara en segundo plano. "
                "Devuelve solo JSON valido, sin markdown ni texto extra. "
                "No expongas cadena de pensamiento. "
                "Responde con este esquema exacto: "
                '{"commentary":"",'
                '"title":"",'
                '"badge":"Cerebro lento",'
                '"phases":['
                '{"id":"phase-1","label":"","steps":["",""]},'
                '{"id":"phase-2","label":"","steps":["",""]},'
                '{"id":"phase-3","label":"","steps":["",""]}'
                "]}"
            ),
        },
        {
            "role": "user",
            "text": (
                "Genera la primera narracion visible y un plan resumido para esta peticion.\n"
                f"Motivo de triage: {reason}\n"
                f"Peticion: {user_message}"
            ),
        },
    ]


def normalize_fast_visible_plan(raw_text: str, *, user_message: str, reason: str) -> tuple[str, dict]:
    """Normaliza la salida del cerebro rapido y la mezcla con un fallback seguro."""
    fallback_blueprint = _select_blueprint(user_message, reason=reason, lane="slow")
    fallback_commentary = build_commentary(user_message, reason=reason, lane="slow")
    payload = _extract_json_payload(raw_text)

    commentary = str(payload.get("commentary", "")).strip() if isinstance(payload, dict) else ""
    title = str(payload.get("title", "")).strip() if isinstance(payload, dict) else ""
    badge = str(payload.get("badge", "")).strip() if isinstance(payload, dict) else ""
    phases = payload.get("phases", []) if isinstance(payload, dict) else []

    normalized_blueprint = {
        "title": title or fallback_blueprint["title"],
        "badge": badge or fallback_blueprint["badge"],
        "summary": dict(fallback_blueprint["summary"]),
        "phases": _normalize_phase_defs(phases, fallback_blueprint["phases"]),
    }
    return commentary or fallback_commentary, normalized_blueprint


def normalize_fast_visible_commentary(
    raw_text: str,
    *,
    user_message: str,
    reason: str,
    lane: str,
) -> str:
    """Normaliza la primera burbuja visible generada por el cerebro rapido."""
    fallback_commentary = build_commentary(user_message, reason=reason, lane=lane)
    payload = _extract_json_payload(raw_text)

    if isinstance(payload, dict):
        commentary = str(payload.get("commentary", "")).strip()
        if commentary:
            return commentary

    plain_text = _extract_plain_text(raw_text)
    return plain_text or fallback_commentary


def _extract_json_payload(raw_text: str) -> dict:
    text = (raw_text or "").strip()
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_plain_text(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return ""

    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    cleaned = lines[0].strip().strip('"').strip("'")
    if not cleaned.startswith("{"):
        return cleaned
    return ""


def _normalize_phase_defs(phases: object, fallback_phases: list[dict]) -> list[dict]:
    if not isinstance(phases, list):
        return fallback_phases

    normalized: list[dict] = []
    for index, item in enumerate(phases[:3], start=1):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        steps = item.get("steps", [])
        normalized_steps = [
            str(step).strip()
            for step in steps
            if str(step).strip()
        ][:3]
        if not label or not normalized_steps:
            continue
        normalized.append(
            {
                "id": str(item.get("id", "")).strip() or f"phase-{index}",
                "label": label,
                "steps": normalized_steps,
            }
        )

    return normalized or fallback_phases


def _select_blueprint(user_message: str, *, reason: str, lane: str) -> dict:
    normalized = " ".join((user_message or "").strip().lower().split())

    if lane != "slow":
        return {
            "title": "Respuesta rapida",
            "badge": "Cerebro rapido",
            "summary": {
                "delegated": "Respuesta inmediata en curso.",
                "context-ready": "Respuesta inmediata en curso.",
                "thinking": "Respuesta inmediata en curso.",
                "finalizing": "Cerrando la respuesta.",
                "done": "Respuesta completada.",
            },
            "phases": [
                {
                    "id": "fast-answer",
                    "label": "Respuesta al vuelo",
                    "steps": [
                        "Interpretar el pedido",
                        "Responder en streaming",
                    ],
                }
            ],
        }

    if any(token in normalized for token in ("archivo", "workspace", "carpeta", "documento", "pdf")):
        return {
            "title": "Pensamiento profundo en marcha",
            "badge": "Cerebro lento",
            "summary": {
                "delegated": "He lanzado una lectura profunda del contexto antes de responderte.",
                "context-ready": "Ya tengo el contexto principal. Ahora estoy ordenando lo importante.",
                "thinking": "Sigo cruzando el contexto para darte una respuesta util y compacta.",
                "finalizing": "Estoy cerrando el resumen final y limpiando la salida.",
                "done": "Proceso completado.",
            },
            "phases": [
                {
                    "id": "phase-context",
                    "label": "Fase 1: Recopilacion de Contexto",
                    "steps": [
                        "Localizar fuentes relevantes",
                        "Leer archivos y memoria util",
                    ],
                },
                {
                    "id": "phase-analysis",
                    "label": "Fase 2: Analisis y Sintesis",
                    "steps": [
                        "Extraer hallazgos principales",
                        "Ordenar una respuesta accionable",
                    ],
                },
                {
                    "id": "phase-close",
                    "label": "Fase 3: Cierre",
                    "steps": [
                        "Redactar el resumen final",
                        "Revisar claridad y cobertura",
                    ],
                },
            ],
        }

    if any(token in normalized for token in ("codigo", "code", "bug", "error", "traceback", "stacktrace")):
        return {
            "title": "Analisis tecnico en marcha",
            "badge": "Cerebro lento",
            "summary": {
                "delegated": "He lanzado una revision mas profunda para no responderte a ciegas.",
                "context-ready": "Ya tengo el contexto tecnico. Ahora voy con la hipotesis principal.",
                "thinking": "Estoy contrastando sintomas, contexto y solucion probable.",
                "finalizing": "Estoy cerrando la explicacion y los siguientes pasos.",
                "done": "Proceso completado.",
            },
            "phases": [
                {
                    "id": "phase-inspect",
                    "label": "Fase 1: Inspeccion Tecnica",
                    "steps": [
                        "Identificar el area afectada",
                        "Revisar sintomas y contexto",
                    ],
                },
                {
                    "id": "phase-resolve",
                    "label": "Fase 2: Resolucion",
                    "steps": [
                        "Construir una hipotesis util",
                        "Definir cambio o explicacion",
                    ],
                },
                {
                    "id": "phase-output",
                    "label": "Fase 3: Cierre",
                    "steps": [
                        "Sintetizar la propuesta",
                        "Revisar riesgos y siguientes pasos",
                    ],
                },
            ],
        }

    if any(token in normalized for token in ("historia", "cuento", "inventa", "imagina")):
        return {
            "title": "Narrativa en construccion",
            "badge": "Cerebro lento",
            "summary": {
                "delegated": "He pasado esto al modo narrativo para que tenga mas forma y ritmo.",
                "context-ready": "Ya tengo el tono y los elementos principales. Ahora construyo la historia.",
                "thinking": "Estoy montando la historia para que tenga un hilo claro y un cierre bueno.",
                "finalizing": "Estoy puliendo el ritmo y el remate final.",
                "done": "Proceso completado.",
            },
            "phases": [
                {
                    "id": "phase-scene",
                    "label": "Fase 1: Preparacion Narrativa",
                    "steps": [
                        "Identificar protagonistas y tono",
                        "Definir escenario y punto de partida",
                    ],
                },
                {
                    "id": "phase-story",
                    "label": "Fase 2: Construccion de la Historia",
                    "steps": [
                        "Elegir conflicto o giro",
                        "Ordenar inicio, nudo y cierre",
                    ],
                },
                {
                    "id": "phase-polish",
                    "label": "Fase 3: Pulido Final",
                    "steps": [
                        "Ajustar ritmo y voz",
                        "Cerrar la respuesta",
                    ],
                },
            ],
        }

    return {
        "title": "Pensamiento profundo en marcha",
        "badge": "Cerebro lento",
        "summary": {
            "delegated": "He lanzado una pasada mas lenta para ordenar bien la respuesta.",
            "context-ready": "Ya tengo el contexto principal. Ahora bajo a los detalles relevantes.",
            "thinking": "Estoy ordenando el enfoque antes de responderte.",
            "finalizing": "Estoy cerrando los puntos clave para entregartelo limpio.",
            "done": "Proceso completado.",
        },
        "phases": [
            {
                "id": "phase-understand",
                "label": "Fase 1: Comprension",
                "steps": [
                    "Aterrizar el objetivo real",
                    "Recuperar contexto util",
                ],
            },
            {
                "id": "phase-build",
                "label": "Fase 2: Construccion",
                "steps": [
                    "Construir el enfoque principal",
                    "Resolver puntos ambiguos",
                ],
            },
            {
                "id": "phase-review",
                "label": "Fase 3: Cierre",
                "steps": [
                    "Redactar la respuesta final",
                    "Revisar claridad y tono",
                ],
            },
        ],
    }


def _materialize_phases(phase_defs: list[dict], *, stage: str, tick: int) -> list[dict]:
    stage_map = {
        "delegated": (0, 0),
        "context-ready": (1, 0),
        "thinking": (1, min(1, max(0, tick))),
        "finalizing": (2, 0),
        "done": (999, 999),
    }
    active_phase_index, active_step_index = stage_map.get(stage, (1, 0))
    phases: list[dict] = []

    for phase_index, phase_def in enumerate(phase_defs):
        steps: list[dict] = []
        for step_index, step_label in enumerate(phase_def["steps"]):
            if stage == "done":
                status = "done"
            elif phase_index < active_phase_index:
                status = "done"
            elif phase_index > active_phase_index:
                status = "pending"
            elif step_index < active_step_index:
                status = "done"
            elif step_index == active_step_index:
                status = "active"
            else:
                status = "pending"
            steps.append(
                {
                    "id": f"{phase_def['id']}-step-{step_index + 1}",
                    "label": step_label,
                    "status": status,
                }
            )

        if stage == "done":
            phase_status = "done"
        elif phase_index < active_phase_index:
            phase_status = "done"
        elif phase_index == active_phase_index:
            phase_status = "active"
        else:
            phase_status = "pending"

        phases.append(
            {
                "id": phase_def["id"],
                "label": phase_def["label"],
                "status": phase_status,
                "steps": steps,
            }
        )

    return phases
