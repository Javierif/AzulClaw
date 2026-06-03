"""Attachment and visual-input handling for the conversation orchestrator.

Extracted from ``conversation.py`` as a mixin. Relies on the orchestrator's
``self.runtime_manager`` and ``self.memory``; carries no state of its own.
"""

import logging
from pathlib import Path

from agent_framework import Content

from .attachments import (
    AttachmentError,
    build_attachment_context,
    build_vision_capability_error,
    render_pdf_pages_as_data_uris,
)

LOGGER = logging.getLogger(__name__)


class AttachmentMixin:
    """Resolves visual-capable lanes and assembles attachment inputs for a turn."""

    def _supports_visual_inputs(self, lane: str) -> bool:
        checker = getattr(self.runtime_manager, "supports_multimodal_input", None)
        if callable(checker):
            return bool(checker(lane))
        return False

    def _resolve_visual_lane(self, preferred_lane: str) -> str:
        """Chooses a lane that can accept visual inputs, preferring fast as fallback."""
        if self._supports_visual_inputs(preferred_lane):
            return preferred_lane
        if preferred_lane != "fast" and self._supports_visual_inputs("fast"):
            LOGGER.info(
                "[Brain] Falling back from lane=%s to lane=fast for visual input support.",
                preferred_lane,
            )
            return "fast"
        return preferred_lane

    def _load_attachment(self, attachment_id: str, user_id: str) -> dict | None:
        loader = getattr(self.memory, "get_attachment", None)
        if not callable(loader):
            return None
        return loader(attachment_id, user_id)

    def _prepare_attachment_inputs(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_message: str,
        lane: str,
        attachment_ids: list[str] | None,
    ) -> tuple[str, list[Content], bool, str]:
        requested_ids = [str(item).strip() for item in (attachment_ids or []) if str(item).strip()]
        current_attachments = [
            attachment
            for attachment in (self._load_attachment(attachment_id, user_id) for attachment_id in requested_ids)
            if attachment is not None
        ]
        if requested_ids and len(current_attachments) != len(requested_ids):
            missing = sorted(set(requested_ids) - {str(item["id"]) for item in current_attachments})
            raise AttachmentError(f"Attachment not found: {', '.join(missing)}")

        conversation_attachments: list[dict] = []
        if conversation_id and hasattr(self.memory, "list_conversation_attachments"):
            conversation_attachments = self.memory.list_conversation_attachments(conversation_id, user_id)
        all_attachments = [
            *conversation_attachments,
            *[
                item
                for item in current_attachments
                if str(item["id"]) not in {str(existing.get("id")) for existing in conversation_attachments}
            ],
        ]

        document_context, _ = build_attachment_context(all_attachments, user_message)

        visual_candidates = [
            item
            for item in current_attachments
            if item.get("kind") == "image" or item.get("extraction_status") == "low_text_quality"
        ]
        if not visual_candidates and conversation_id and hasattr(self.memory, "list_recent_visual_attachments"):
            visual_candidates = self.memory.list_recent_visual_attachments(conversation_id, user_id, limit=2)

        if not visual_candidates:
            return document_context, [], False, lane

        selected_lane = self._resolve_visual_lane(lane)
        if not self._supports_visual_inputs(selected_lane):
            raise AttachmentError(build_vision_capability_error())

        visual_contents: list[Content] = []
        for attachment in visual_candidates:
            mime_type = str(attachment.get("mime_type", "")).lower()
            storage_path = Path(str(attachment.get("storage_path", "")))
            if not storage_path.exists():
                continue
            if mime_type.startswith("image/"):
                visual_contents.append(Content.from_data(storage_path.read_bytes(), mime_type))
                continue
            if mime_type == "application/pdf":
                for data_uri in render_pdf_pages_as_data_uris(storage_path):
                    visual_contents.append(Content.from_uri(data_uri, media_type="image/png"))

        return document_context, visual_contents, bool(visual_contents), selected_lane
