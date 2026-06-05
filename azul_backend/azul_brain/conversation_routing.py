"""Lane/route resolution for the conversation orchestrator.

Extracted from ``conversation.py`` as a mixin. Relies on the orchestrator's
``self.runtime_manager`` and the semantic-judge route helper
(``self._judge_route_semantically``); carries no state of its own.
"""

from .cortex.fast.triage import TriageDecision, classify_message


class RoutingMixin:
    """Resolves the effective cognitive lane/route for a turn."""

    def resolve_route(self, user_message: str, requested_lane: str = "auto") -> TriageDecision:
        """Cheap deterministic fallback route."""
        normalized = (requested_lane or "").strip().lower()
        if normalized in {"fast", "slow"}:
            return TriageDecision(lane=normalized, reason="explicit")
        if normalized == "auto":
            return classify_message(user_message)

        default_lane = self.runtime_manager.load_settings().default_lane
        if default_lane == "auto":
            return classify_message(user_message)
        return TriageDecision(lane=default_lane, reason="runtime-default")

    def resolve_lane(self, user_message: str, requested_lane: str = "auto") -> str:
        """Backwards compatibility helper to get only the lane."""
        return self.resolve_route(user_message, requested_lane).lane

    async def resolve_route_async(self, user_message: str, requested_lane: str = "auto") -> TriageDecision:
        """Determines the effective cognitive route for this turn."""
        normalized = (requested_lane or "").strip().lower()
        if normalized in {"fast", "slow"}:
            return TriageDecision(lane=normalized, reason="explicit")
        if normalized != "auto":
            default_lane = self.runtime_manager.load_settings().default_lane
            if default_lane != "auto":
                return TriageDecision(lane=default_lane, reason="runtime-default")
        semantic_route = await self._judge_route_semantically(user_message)
        if semantic_route is not None:
            return semantic_route
        return self.resolve_route(user_message, requested_lane)
