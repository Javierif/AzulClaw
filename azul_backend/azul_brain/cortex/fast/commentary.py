"""Fast commentator and visible progress for the user."""

from __future__ import annotations

import json


def build_commentary(user_message: str, *, reason: str, lane: str) -> str:
    """Deterministic fallback if the fast brain cannot narrate."""
    if lane == "fast":
        if reason == "short-question":
            return "Let me answer that right away."
        return "On it. I'll walk you through it as I go."

    if reason in {"complex-request", "deep-analysis-request"}:
        return "Routing this to the slow brain to think it through properly. I'll keep you updated."
    if reason == "long-request":
        return "Breaking down the request step by step. Back with a full response shortly."
    return "Thinking this through more carefully. Full response coming up."


def prompt_for_fast_visible_commentary(
    user_message: str,
    *,
    reason: str,
    lane: str,
) -> list[dict[str, str]]:
    """Prompt for the fast brain to draft the first visible bubble."""
    lane_label = "fast brain" if lane == "fast" else "slow brain"
    return [
        {
            "role": "system",
            "text": (
                "You are AzulClaw's fast visible brain. "
                "Return only valid JSON, no markdown or extra text. "
                'Use this exact schema: {"commentary":""}. '
                "The commentary must be a single short, natural sentence. "
                "Do not give the final answer. "
                "Do not expose chain of thought. "
                "Sound like a useful first reaction from the agent while it works."
            ),
        },
        {
            "role": "user",
            "text": (
                "Generate the first visible narration for this request.\n"
                f"Active route: {lane_label}\n"
                f"Triage reason: {reason}\n"
                f"Request: {user_message}"
            ),
        },
    ]


def build_progress_snapshot(
    user_message: str,
    *,
    reason: str,
    lane: str,
    stage: str,
    event_type: str = "progress-update",
    tick: int = 0,
    summary: str = "",
    current_step_label: str = "",
    started_at: str = "",
    last_updated_at: str = "",
    blueprint: dict | None = None,
) -> dict:
    """Builds a safe, summarised view of internal progress."""
    selected_blueprint = blueprint or _select_blueprint(user_message, reason=reason, lane=lane)
    phases = _materialize_phases(selected_blueprint["phases"], stage=stage, tick=tick)
    active_count = sum(
        1
        for phase in phases
        for step in phase["steps"]
        if step["status"] != "done"
    )
    return {
        "event_type": event_type,
        "title": selected_blueprint["title"],
        "summary": summary or selected_blueprint["summary"].get(stage, selected_blueprint["summary"]["thinking"]),
        "badge": selected_blueprint["badge"],
        "lane": lane,
        "lane_label": _lane_label(lane),
        "triage_reason": reason,
        "reason_label": _reason_label(reason, lane=lane),
        "current_step_label": current_step_label or _current_step_label(phases, stage=stage),
        "started_at": started_at,
        "last_updated_at": last_updated_at,
        "active_count": active_count,
        "phases": phases,
    }


def prompt_for_fast_visible_plan(user_message: str, *, reason: str) -> list[dict[str, str]]:
    """Prompt for the fast brain to generate the first visible response."""
    return [
        {
            "role": "system",
            "text": (
                "You are AzulClaw's fast visible brain. "
                "The slow brain will work in the background. "
                "Return only valid JSON, no markdown or extra text. "
                "Do not expose chain of thought. "
                "Respond with this exact schema: "
                '{"commentary":"",'
                '"title":"",'
                '"badge":"Slow brain",'
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
                "Generate the first visible narration and a summarised plan for this request.\n"
                f"Triage reason: {reason}\n"
                f"Request: {user_message}"
            ),
        },
    ]


def normalize_fast_visible_plan(raw_text: str, *, user_message: str, reason: str) -> tuple[str, dict]:
    """Normalises the fast brain output and merges it with a safe fallback."""
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
    """Normalises the first visible bubble generated by the fast brain."""
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


def _lane_label(lane: str) -> str:
    return "Slow brain" if lane == "slow" else "Fast brain"


def _reason_label(reason: str, *, lane: str) -> str:
    mapping = {
        "phatic": "Greeting or acknowledgement",
        "empty": "Empty input",
        "code-block": "Code block detected",
        "complex-marker": "Complex or technical request",
        "short-utterance": "Short request",
        "short-question": "Quick question",
        "long-request": "Long request",
        "default-fast": "Default quick route",
        "explicit": "Lane selected explicitly",
        "runtime-default": "Runtime default route",
        "heartbeat-intent": "Heartbeat automation flow",
    }
    normalized = (reason or "").strip()
    if normalized in mapping:
        return mapping[normalized]
    if normalized.endswith("|visual-fallback-fast"):
        return "Visual input required a fast-compatible route"
    if lane == "slow":
        return "Deep analysis requested"
    return "Quick response path"


def _current_step_label(phases: list[dict], *, stage: str) -> str:
    if stage == "done":
        return "Response ready"
    for phase in phases:
        for step in phase.get("steps", []):
            if step.get("status") == "active":
                return str(step.get("label", "")).strip() or str(phase.get("label", "")).strip()
    for phase in phases:
        if phase.get("status") == "active":
            return str(phase.get("label", "")).strip()
    return "Preparing the response"


def _select_blueprint(user_message: str, *, reason: str, lane: str) -> dict:
    if lane != "slow":
        return {
            "title": "Quick response",
            "badge": "Fast brain",
            "summary": {
                "delegated": "Immediate response in progress.",
                "context-ready": "Immediate response in progress.",
                "thinking": "Immediate response in progress.",
                "finalizing": "Wrapping up the response.",
                "done": "Response complete.",
            },
            "phases": [
                {
                    "id": "fast-answer",
                    "label": "On-the-fly response",
                    "steps": [
                        "Interpret the request",
                        "Respond in streaming",
                    ],
                }
            ],
        }

    if reason in {"complex-request", "deep-analysis-request"}:
        return {
            "title": "Deep thinking in progress",
            "badge": "Slow brain",
            "summary": {
                "delegated": "Started a deep context read before replying.",
                "context-ready": "Context gathered. Now organising what matters most.",
                "thinking": "Still cross-referencing context to give you a useful, concise answer.",
                "finalizing": "Finalising the summary and cleaning up the output.",
                "done": "Process complete.",
            },
            "phases": [
                {
                    "id": "phase-context",
                    "label": "Phase 1: Context Gathering",
                    "steps": [
                        "Locate relevant sources",
                        "Read files and useful memory",
                    ],
                },
                {
                    "id": "phase-analysis",
                    "label": "Phase 2: Analysis and Synthesis",
                    "steps": [
                        "Extract key findings",
                        "Structure an actionable response",
                    ],
                },
                {
                    "id": "phase-close",
                    "label": "Phase 3: Close",
                    "steps": [
                        "Draft the final summary",
                        "Review clarity and coverage",
                    ],
                },
            ],
        }

    return {
        "title": "Deep thinking in progress",
        "badge": "Slow brain",
        "summary": {
            "delegated": "Running a slower pass to structure the response properly.",
            "context-ready": "Main context ready. Diving into the relevant details now.",
            "thinking": "Organising the approach before replying.",
            "finalizing": "Closing the key points to deliver a clean response.",
            "done": "Process complete.",
        },
        "phases": [
            {
                "id": "phase-understand",
                "label": "Phase 1: Understanding",
                "steps": [
                    "Clarify the real objective",
                    "Retrieve useful context",
                ],
            },
            {
                "id": "phase-build",
                "label": "Phase 2: Construction",
                "steps": [
                    "Build the main approach",
                    "Resolve ambiguous points",
                ],
            },
            {
                "id": "phase-review",
                "label": "Phase 3: Close",
                "steps": [
                    "Draft the final response",
                    "Review clarity and tone",
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
