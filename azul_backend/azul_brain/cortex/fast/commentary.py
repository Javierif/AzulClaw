"""Fast commentator and visible progress for the user."""

from __future__ import annotations

import json


def build_commentary(user_message: str, *, reason: str, lane: str) -> str:
    """Heuristic fallback if the fast brain cannot narrate."""
    normalized = " ".join((user_message or "").strip().lower().split())

    if lane == "fast":
        if any(token in normalized for token in ("historia", "cuento", "inventa", "imagina")):
            return "Let me picture that for a second. I'll start telling you now."
        if normalized.endswith("?"):
            return "Let me answer that right away."
        return "On it. I'll walk you through it as I go."

    if any(token in normalized for token in ("archivo", "workspace", "carpeta", "documento", "pdf")):
        return "Reviewing the context and reading what I need. I'll have a full response shortly."
    if any(token in normalized for token in ("codigo", "code", "bug", "error", "traceback", "stacktrace")):
        return "Taking a closer look at this. Let me gather context first, then give you a definitive answer."
    if any(token in normalized for token in ("analiza", "arquitectura", "plan", "estrategia", "detall")):
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
    tick: int = 0,
    summary: str = "",
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
        "title": selected_blueprint["title"],
        "summary": summary or selected_blueprint["summary"].get(stage, selected_blueprint["summary"]["thinking"]),
        "badge": selected_blueprint["badge"],
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


def _select_blueprint(user_message: str, *, reason: str, lane: str) -> dict:
    normalized = " ".join((user_message or "").strip().lower().split())

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

    if any(token in normalized for token in ("archivo", "workspace", "carpeta", "documento", "pdf")):
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

    if any(token in normalized for token in ("codigo", "code", "bug", "error", "traceback", "stacktrace")):
        return {
            "title": "Technical analysis in progress",
            "badge": "Slow brain",
            "summary": {
                "delegated": "Started a deeper review to avoid a blind answer.",
                "context-ready": "Technical context ready. Moving to the main hypothesis.",
                "thinking": "Cross-checking symptoms, context, and probable solution.",
                "finalizing": "Finalising the explanation and next steps.",
                "done": "Process complete.",
            },
            "phases": [
                {
                    "id": "phase-inspect",
                    "label": "Phase 1: Technical Inspection",
                    "steps": [
                        "Identify the affected area",
                        "Review symptoms and context",
                    ],
                },
                {
                    "id": "phase-resolve",
                    "label": "Phase 2: Resolution",
                    "steps": [
                        "Build a useful hypothesis",
                        "Define the change or explanation",
                    ],
                },
                {
                    "id": "phase-output",
                    "label": "Phase 3: Close",
                    "steps": [
                        "Synthesise the proposal",
                        "Review risks and next steps",
                    ],
                },
            ],
        }

    if any(token in normalized for token in ("historia", "cuento", "inventa", "imagina")):
        return {
            "title": "Narrative in construction",
            "badge": "Slow brain",
            "summary": {
                "delegated": "Switched to narrative mode for better shape and flow.",
                "context-ready": "Tone and key elements ready. Building the story now.",
                "thinking": "Crafting the story so it has a clear thread and a good ending.",
                "finalizing": "Polishing the pace and the final payoff.",
                "done": "Process complete.",
            },
            "phases": [
                {
                    "id": "phase-scene",
                    "label": "Phase 1: Narrative Preparation",
                    "steps": [
                        "Identify protagonists and tone",
                        "Define setting and starting point",
                    ],
                },
                {
                    "id": "phase-story",
                    "label": "Phase 2: Story Construction",
                    "steps": [
                        "Choose conflict or twist",
                        "Structure beginning, middle, and end",
                    ],
                },
                {
                    "id": "phase-polish",
                    "label": "Phase 3: Final Polish",
                    "steps": [
                        "Adjust pace and voice",
                        "Close the response",
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
