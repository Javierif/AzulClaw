"""Shared approval-card protocol for pending chat actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

APPROVAL_BLOCK_KIND = "approval"
_PENDING_ACTION_PREFIX = "[PENDING_ACTION:"
_PENDING_ACTION_SUFFIX = "[/PENDING_ACTION]"


@dataclass(frozen=True)
class ApprovalBlock:
    """Structured approval block shared across pending-action flows."""

    action_id: str
    action_kind: str
    title: str
    summary: str
    approve_label: str = "Approve"
    reject_label: str = "Cancel"
    extra_fields: Mapping[str, str] | None = None

    def render(self) -> str:
        lines = [
            f"{_PENDING_ACTION_PREFIX}{APPROVAL_BLOCK_KIND}]",
            f"ActionId: {self.action_id}",
            f"ActionKind: {self.action_kind}",
            f"Title: {self.title}",
            f"Summary: {self.summary}",
        ]
        for label, value in (self.extra_fields or {}).items():
            if value is None:
                continue
            rendered = str(value).strip()
            if not rendered:
                continue
            lines.append(f"{label}: {rendered}")
        lines.extend(
            [
                f"ApproveLabel: {self.approve_label}",
                f"RejectLabel: {self.reject_label}",
                _PENDING_ACTION_SUFFIX,
            ]
        )
        return "\n".join(lines)


def contains_pending_action_block(text: str, *, block_kind: str | None = None) -> bool:
    """Returns whether the text contains a pending-action block."""

    candidate = text or ""
    if block_kind:
        return f"{_PENDING_ACTION_PREFIX}{block_kind}]" in candidate
    return _PENDING_ACTION_PREFIX in candidate


def strip_pending_action_block(text: str) -> str:
    """Removes the first pending-action block from a reply."""

    start = (text or "").find(_PENDING_ACTION_PREFIX)
    if start < 0:
        return (text or "").strip()
    end = (text or "").find(_PENDING_ACTION_SUFFIX, start)
    if end < 0:
        return (text or "").strip()
    stripped = ((text or "")[:start] + (text or "")[end + len(_PENDING_ACTION_SUFFIX) :]).strip()
    return stripped


def parse_pending_action_block_fields(text: str, block_kind: str) -> dict[str, str]:
    """Parses a pending-action block with the given kind into flat string fields."""

    marker = f"{_PENDING_ACTION_PREFIX}{block_kind}]"
    start = (text or "").find(marker)
    if start < 0:
        return {}
    end = (text or "").find(_PENDING_ACTION_SUFFIX, start)
    if end < 0:
        return {}
    block = (text or "")[start : end + len(_PENDING_ACTION_SUFFIX)]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line or line.startswith("["):
            continue
        label, value = line.split(":", 1)
        fields[label.strip()] = value.strip()
    return fields


def parse_approval_block(text: str) -> dict[str, str]:
    """Parses the shared approval block fields."""

    return parse_pending_action_block_fields(text, APPROVAL_BLOCK_KIND)


def render_approval_block(
    *,
    action_id: str,
    action_kind: str,
    title: str,
    summary: str,
    approve_label: str = "Approve",
    reject_label: str = "Cancel",
    extra_fields: Mapping[str, str] | None = None,
) -> str:
    """Renders the shared approval block used by all approval cards."""

    return ApprovalBlock(
        action_id=action_id,
        action_kind=action_kind,
        title=title,
        summary=summary,
        approve_label=approve_label,
        reject_label=reject_label,
        extra_fields=extra_fields,
    ).render()
