from __future__ import annotations

import unittest

from azul_backend.azul_brain.runtime.approval_protocol import (
    contains_pending_action_block,
    parse_approval_block,
    parse_pending_action_block_fields,
    render_approval_block,
    strip_pending_action_block,
)


class ApprovalProtocolTests(unittest.TestCase):
    def test_shared_approval_block_roundtrip_preserves_core_fields(self) -> None:
        rendered = render_approval_block(
            action_id="pending-123",
            action_kind="folder_organizer",
            title="Folder Organizer",
            summary="Approve applying the reviewed folder plan.",
            approve_label="Apply changes",
            reject_label="Cancel",
            extra_fields={
                "Scope": ".",
                "ExecutionBinding": "Reviewed preview",
            },
        )

        self.assertTrue(contains_pending_action_block(rendered))
        parsed = parse_approval_block(rendered)

        self.assertEqual(parsed["ActionId"], "pending-123")
        self.assertEqual(parsed["ActionKind"], "folder_organizer")
        self.assertEqual(parsed["Title"], "Folder Organizer")
        self.assertEqual(parsed["Summary"], "Approve applying the reviewed folder plan.")
        self.assertEqual(parsed["ApproveLabel"], "Apply changes")
        self.assertEqual(parsed["RejectLabel"], "Cancel")
        self.assertEqual(parsed["Scope"], ".")
        self.assertEqual(parsed["ExecutionBinding"], "Reviewed preview")

    def test_generic_helpers_support_non_approval_pending_blocks(self) -> None:
        text = (
            "Plan ready.\n\n"
            "[PENDING_ACTION:folder_organizer]\n"
            "Title: Folder Organizer\n"
            "Summary: Approve applying the proposed folder organization changes.\n"
            "ArgumentsJson: {\"recursive\": true}\n"
            "[/PENDING_ACTION]"
        )

        self.assertTrue(contains_pending_action_block(text))
        fields = parse_pending_action_block_fields(text, "folder_organizer")

        self.assertEqual(fields["Title"], "Folder Organizer")
        self.assertEqual(fields["ArgumentsJson"], "{\"recursive\": true}")
        self.assertEqual(strip_pending_action_block(text), "Plan ready.")


if __name__ == "__main__":
    unittest.main()
