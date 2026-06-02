"""Initial triage to decide the internal cognitive route."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TriageDecision:
    """Cognitive routing decision."""

    lane: str
    reason: str


def classify_message(user_message: str) -> TriageDecision:
    """Cheap structural fallback when semantic triage is unavailable."""
    normalized = " ".join((user_message or "").strip().lower().split())
    if not normalized:
        return TriageDecision(lane="fast", reason="empty")

    if "```" in normalized:
        return TriageDecision(lane="slow", reason="code-block")

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
