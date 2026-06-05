"""Progress and slow-lane commentary loops for the conversation orchestrator.

Extracted from ``conversation.py`` as a mixin. These coroutines are driven
entirely by their arguments and module-level timing helpers, so they hold no
orchestrator state of their own.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from .conversation_helpers import _random_progress_delay_seconds


class ProgressMixin:
    """Background coroutines that surface progress while the slow brain works."""

    async def _slow_commentary_loop(
        self,
        user_message: str,
        *,
        reason: str,
        on_commentary: Callable[[str], Awaitable[None]],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        started_at: str,
        progress_blueprint: dict | None = None,
    ) -> None:
        """Emits lightweight feedback while the slow brain is still working."""
        updates = [
            "Still thinking this through to give you a thorough answer.",
            "Structuring the response and making sure the approach makes sense.",
            "Almost there. Closing the key points before responding.",
        ]
        index = 0
        while True:
            await asyncio.sleep(_random_progress_delay_seconds())
            commentary = updates[index % len(updates)]
            await on_commentary(commentary)
            if on_progress is not None:
                stage = "thinking" if index < 2 else "finalizing"
                await on_progress(
                    "progress-update",
                    stage=stage,
                    summary=commentary,
                    tick=index,
                    blueprint=progress_blueprint,
                )
            index += 1

    async def _progress_idle_watchdog(
        self,
        get_last_visible_update: Callable[[], float],
        emit_commentary: Callable[[str], Awaitable[None]],
        emit_progress: Callable[..., Awaitable[None]],
        lane_getter: Callable[[], str],
        blueprint_getter: Callable[[], dict | None],
    ) -> None:
        """Emits a reassurance update when the user has not seen progress for a while."""
        idle_commentary = "Still processing this; I haven't stalled."
        next_idle_after = _random_progress_delay_seconds()
        while True:
            await asyncio.sleep(1.0)
            if (time.monotonic() - get_last_visible_update()) < next_idle_after:
                continue
            lane = lane_getter()
            blueprint = blueprint_getter()
            await emit_commentary(idle_commentary)
            await emit_progress(
                "progress-idle",
                stage="thinking" if lane == "slow" else "delegated",
                summary=idle_commentary,
                blueprint=blueprint,
            )
            next_idle_after = _random_progress_delay_seconds()
