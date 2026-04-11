"""Initial triage to decide the internal cognitive route."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TriageDecision:
    """Cognitive routing decision."""

    lane: str
    reason: str


SIMPLE_EXACT = {
    "hola",
    "buenas",
    "hey",
    "hello",
    "gracias",
    "muchas gracias",
    "ok",
    "vale",
    "perfecto",
}

COMPLEX_MARKERS = (
    "refactor",
    "implement",
    "codigo",
    "code",
    "bug",
    "error",
    "stacktrace",
    "traceback",
    "archivo",
    "workspace",
    "pdf",
    "resume",
    "resum",
    "revisa",
    "revisar",
    "contenido",
    "analiza",
    "analizar",
    "depura",
    "debug",
    "cron",
    "heartbeat",
    "modelo",
    "arquitectura",
    "test",
    "tool",
    "mcp",
    "paso a paso",
    "detall",
    "estrateg",
    "investiga",
    "investigar",
)


def classify_message(user_message: str) -> TriageDecision:
    """Decides whether the message can be resolved via the fast or slow route."""
    normalized = " ".join((user_message or "").strip().lower().split())
    if not normalized:
        return TriageDecision(lane="fast", reason="empty")

    if normalized in SIMPLE_EXACT:
        return TriageDecision(lane="fast", reason="phatic")

    if "```" in normalized:
        return TriageDecision(lane="slow", reason="code-block")

    if any(marker in normalized for marker in COMPLEX_MARKERS):
        return TriageDecision(lane="slow", reason="complex-marker")

    words = re.findall(r"\S+", normalized)
    if len(words) <= 16 and "?" not in normalized:
        return TriageDecision(lane="fast", reason="short-utterance")

    if len(words) <= 18 and normalized.endswith("?"):
        return TriageDecision(lane="fast", reason="short-question")

    if len(words) >= 28:
        return TriageDecision(lane="slow", reason="long-request")

    # System 1 should be the default route; System 2 only activates on
    # clear signals of complexity or a genuinely long request.
    return TriageDecision(lane="fast", reason="default-fast")
