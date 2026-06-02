"""Isolated Agent Framework workflow for the Folder Organizer skill.

AzulClaw core launches this file out of process. The skill owns the workflow
shape while the core mediates tools, permissions, approvals, and checkpoint
state through the JSON-lines protocol.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from agent_framework import (
    Executor,
    FileCheckpointStorage,
    WorkflowBuilder,
    handler,
    response_handler,
)

SKILL_ID = "dev.azulclaw.desktop-organizer"
WORKFLOW_NAME = "dev.azulclaw.desktop-organizer.workflow.v1"


def _emit(message: dict) -> None:
    print(json.dumps(message, ensure_ascii=False), flush=True)


def _read_message() -> dict:
    line = sys.stdin.readline()
    if not line:
        return {}
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        _emit({"type": "failed", "error": "invalid-json"})
        return {}
    return message if isinstance(message, dict) else {}


def _coerce_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _extract_tool_result(message: dict) -> dict:
    if not message.get("ok"):
        return {"ok": False, "error": str(message.get("error", "Tool call failed.")).strip()}
    return {"ok": True, "result": _coerce_dict(message.get("result"))}


def _preview_summary(preview: dict) -> str:
    summary = str(preview.get("summary", "")).strip()
    if summary:
        return summary
    moves = preview.get("moves")
    if isinstance(moves, list):
        planned = sum(1 for item in moves if isinstance(item, dict) and item.get("status") == "planned")
        return f"Approve moving {planned} file(s)." if planned else "No files need moving."
    return "Folder Organizer preview is ready."


def _move_items(preview: dict, status: str) -> list[dict]:
    moves = preview.get("moves")
    if not isinstance(moves, list):
        return []
    return [
        dict(item)
        for item in moves
        if isinstance(item, dict) and str(item.get("status", "")).strip() == status
    ]


def _blocked_items(preview: dict) -> list[dict]:
    blocked_items = preview.get("blocked_items")
    if isinstance(blocked_items, list):
        return [dict(item) for item in blocked_items if isinstance(item, dict)]
    return _move_items(preview, "blocked")


def _batches(preview: dict) -> list[dict]:
    batches = preview.get("batches")
    if not isinstance(batches, list):
        return []
    return [dict(item) for item in batches if isinstance(item, dict)]


def _coerce_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _planned_move_count(preview: dict) -> int:
    planned_moves = _move_items(preview, "planned")
    if planned_moves:
        return len(planned_moves)
    batch_count = sum(_coerce_int(item.get("planned_count")) for item in _batches(preview))
    if batch_count:
        return batch_count
    planned_count = _coerce_int(preview.get("planned_count"))
    if planned_count:
        return planned_count
    total_count = _coerce_int(preview.get("total_move_count"))
    blocked_count = _blocked_count(preview)
    return max(0, total_count - blocked_count)


def _blocked_count(preview: dict) -> int:
    blocked = _blocked_items(preview)
    if blocked:
        return len(blocked)
    batch_count = sum(_coerce_int(item.get("blocked_count")) for item in _batches(preview))
    if batch_count:
        return batch_count
    return _coerce_int(preview.get("blocked_count"))


def _semantic_group_names(preview: dict) -> list[str]:
    names = preview.get("semantic_custom_categories")
    if not isinstance(names, list):
        return []
    return [str(item).strip() for item in names if str(item).strip()]


def _planned_group_names(preview: dict) -> list[str]:
    names: list[str] = []
    for item in _move_items(preview, "planned"):
        category = str(item.get("category", "")).strip()
        if category and category not in names:
            names.append(category)
    for batch in _batches(preview):
        categories = batch.get("categories")
        if not isinstance(categories, dict):
            continue
        for category, count in categories.items():
            name = str(category).strip()
            if name and _coerce_int(count) > 0 and name not in names:
                names.append(name)
    return names


def _build_organization_plan(*, preview: dict, execution_arguments: dict) -> dict:
    planned_moves = _move_items(preview, "planned")
    blocked = _blocked_items(preview)
    planned_count = _planned_move_count(preview)
    blocked_count = _blocked_count(preview)
    group_names = _semantic_group_names(preview) or _planned_group_names(preview)
    executable = planned_count > 0
    summary = _preview_summary(preview)
    if executable:
        plan_status = "ready_for_approval"
        recommendation = "Review and approve the generated move plan before execution."
    elif blocked_count:
        plan_status = "blocked"
        recommendation = "Resolve the blocked items or rerun preview after the folder state changes."
    else:
        plan_status = "plan_only"
        recommendation = "No executable moves are available from the current folder state."

    return {
        "status": plan_status,
        "summary": summary,
        "executable": executable,
        "planned_move_count": planned_count,
        "blocked_count": blocked_count,
        "plan_batch_count": _coerce_int(preview.get("plan_batch_count")) or len(_batches(preview)),
        "recommended_groups": group_names,
        "blocked_items": blocked,
        "batches": _batches(preview),
        "preview": preview,
        "execute_tool": "execute",
        "execute_arguments": execution_arguments if executable else {},
        "recommendation": recommendation,
    }


def _item_path(item: dict) -> str:
    value = (
        item.get("source_relative_path")
        or item.get("source")
        or item.get("path")
        or item.get("name")
        or ""
    )
    return str(value).strip()


def _render_organization_plan_summary(plan: dict) -> str:
    lines = ["Organization plan generated from the current preview."]
    summary = str(plan.get("summary", "")).strip()
    if summary:
        lines.append(f"Current state: {summary}")
    if bool(plan.get("executable")):
        lines.append(f"Executable moves: {int(plan.get('planned_move_count', 0) or 0)}.")
    else:
        lines.append("Executable moves: none from the current folder state.")

    preview = _coerce_dict(plan.get("preview"))
    planned_moves = _move_items(preview, "planned")
    if planned_moves:
        lines.append("Reviewed moves:")
        for item in planned_moves[:12]:
            source = _item_path(item)
            destination = str(
                item.get("destination_relative_path")
                or item.get("destination")
                or item.get("target")
                or ""
            ).strip()
            category = str(item.get("category", "")).strip()
            detail = source or "Unnamed item"
            if destination:
                detail = f"{detail} -> {destination}"
            if category:
                detail = f"{detail} ({category})"
            lines.append(f"- {detail}")
        if len(planned_moves) > 12:
            lines.append(f"- ... and {len(planned_moves) - 12} more move(s).")
    elif bool(plan.get("executable")):
        batches = plan.get("batches")
        if isinstance(batches, list) and batches:
            lines.append("Reviewed batches:")
            for batch in batches[:12]:
                if not isinstance(batch, dict):
                    continue
                source = str(batch.get("source_folder_relative_path", ".")).strip() or "."
                planned = _coerce_int(batch.get("planned_count"))
                blocked = _coerce_int(batch.get("blocked_count"))
                summary = str(batch.get("summary", "")).strip()
                detail = f"{source}: {planned} planned"
                if blocked:
                    detail = f"{detail}, {blocked} blocked"
                if summary:
                    detail = f"{detail}. {summary}"
                lines.append(f"- {detail}")
            if len(batches) > 12:
                lines.append(f"- ... and {len(batches) - 12} more batch(es).")

    recommended_groups = plan.get("recommended_groups")
    if isinstance(recommended_groups, list) and recommended_groups:
        groups = [str(item).strip() for item in recommended_groups if str(item).strip()]
        if groups:
            lines.append("Reviewed destination groups: " + ", ".join(groups[:8]) + ".")

    blocked_items = plan.get("blocked_items")
    if isinstance(blocked_items, list) and blocked_items:
        paths = [_item_path(item) for item in blocked_items if isinstance(item, dict)]
        shown = [path for path in paths if path]
        if shown:
            lines.append("Blocked items to resolve: " + ", ".join(shown[:5]) + ".")

    recommendation = str(plan.get("recommendation", "")).strip()
    if recommendation:
        lines.append(f"Next step: {recommendation}")
    return "\n".join(lines)


def _execution_moved_count(result: dict) -> int:
    selected_batch = result.get("selected_batch")
    if isinstance(selected_batch, dict):
        moved_count = _coerce_int(selected_batch.get("moved_count"))
        if moved_count:
            return moved_count
    batches = result.get("batches")
    if isinstance(batches, list):
        moved_count = sum(
            _coerce_int(item.get("moved_count"))
            for item in batches
            if isinstance(item, dict)
        )
        if moved_count:
            return moved_count
    moves = result.get("moves")
    if isinstance(moves, list):
        return sum(
            1
            for item in moves
            if isinstance(item, dict) and str(item.get("status", "")).strip() == "moved"
        )
    return 0


def _plan_batch_count(plan: dict) -> int:
    value = _coerce_int(plan.get("plan_batch_count"))
    if value:
        return value
    batches = plan.get("batches")
    return len(batches) if isinstance(batches, list) else 0


async def _execute_approved_plan(bridge: "HostBridge", *, payload: dict) -> dict:
    organization_plan = _coerce_dict(payload.get("organization_plan"))
    execute_tool = str(payload.get("execute_tool", "execute")).strip() or "execute"
    execute_arguments = _coerce_dict(payload.get("execute_arguments"))
    max_executions = 1
    if str(execute_arguments.get("plan_token", "")).strip():
        max_executions = max(1, _plan_batch_count(organization_plan))

    results: list[dict] = []
    for _index in range(max_executions):
        tool_response = await bridge.call_tool(execute_tool, execute_arguments)
        if not tool_response["ok"]:
            return {"ok": False, "error": tool_response["error"]}
        result = tool_response["result"]
        results.append(result)
        if not str(execute_arguments.get("plan_token", "")).strip():
            break
        if bool(result.get("plan_complete")) or _coerce_int(result.get("remaining_batch_count")) == 0:
            break

    if len(results) == 1:
        return {"ok": True, "result": results[0]}
    moved_count = sum(_execution_moved_count(result) for result in results)
    last_summary = str(results[-1].get("summary", "")).strip()
    summary = f"{moved_count} file(s) moved across {len(results)} approved batch(es)."
    if last_summary:
        summary = f"{summary} Last batch: {last_summary}"
    return {
        "ok": True,
        "result": {
            "summary": summary,
            "batch_execution_count": len(results),
            "moved_count": moved_count,
            "plan_complete": bool(results[-1].get("plan_complete", True)),
            "executions": results,
        },
    }


def _plan_completion_output(plan: dict) -> dict:
    status = "plan_ready" if bool(plan.get("executable")) else "plan_only"
    return {
        "status": status,
        "summary": _render_organization_plan_summary(plan),
        "organization_plan": plan,
    }


async def _request_plan_approval(ctx, *, run_id: str, organization_plan: dict) -> None:
    if not bool(organization_plan.get("executable")):
        await ctx.yield_output(
            {
                "status": "failed",
                "error": "Folder Organizer approval requires an executable organization_plan.",
            }
        )
        return
    preview = _coerce_dict(organization_plan.get("preview"))
    execution_arguments = _coerce_dict(organization_plan.get("execute_arguments"))
    request_id = f"{run_id}:move-files" if run_id else ""
    await ctx.request_info(
        {
            "request_id": request_id,
            "action_kind": "move_files",
            "title": "Folder Organizer",
            "summary": _render_organization_plan_summary(organization_plan),
            "risk": "medium",
            "payload": {
                "organization_plan": organization_plan,
                "preview": preview,
                "execute_tool": str(organization_plan.get("execute_tool", "execute")).strip() or "execute",
                "execute_arguments": execution_arguments,
            },
            "labels": {
                "approve": "Apply plan",
                "reject": "Cancel",
            },
        },
        dict,
        request_id=request_id or None,
    )


class HostBridge:
    """Small bridge from Agent Framework executors to AzulClaw's host protocol."""

    def emit_status(self, status: str, **data: object) -> None:
        payload = {"status": status}
        payload.update(data)
        _emit({"type": "status", "data": payload})

    async def call_tool(self, tool: str, arguments: dict) -> dict:
        _emit(
            {
                "type": "tool_call",
                "id": tool,
                "tool": tool,
                "arguments": arguments,
            }
        )
        return _extract_tool_result(_read_message())


class FolderOrganizerWorkflowExecutor(Executor):
    def __init__(self, bridge: HostBridge) -> None:
        super().__init__(id="folder_organizer_flow")
        self.bridge = bridge

    @handler(input=dict, workflow_output=dict)
    async def start(self, message, ctx) -> None:
        run_id = str(message.get("run_id", "")).strip()
        input_payload = _coerce_dict(message.get("input"))
        approved_organization_plan = _coerce_dict(input_payload.get("approved_organization_plan"))
        if approved_organization_plan:
            await _request_plan_approval(
                ctx,
                run_id=run_id,
                organization_plan=approved_organization_plan,
            )
            return

        preview_arguments = _coerce_dict(input_payload.get("preview_arguments"))
        if "recursive" in input_payload and "recursive" not in preview_arguments:
            preview_arguments["recursive"] = bool(input_payload.get("recursive"))
        if "relative_path" in input_payload and "relative_path" not in preview_arguments:
            preview_arguments["relative_path"] = str(input_payload.get("relative_path", "")).strip()
        if "max_depth" in input_payload and "max_depth" not in preview_arguments:
            preview_arguments["max_depth"] = input_payload.get("max_depth")

        self.bridge.emit_status("previewing-folder")
        tool_response = await self.bridge.call_tool("preview", preview_arguments)
        if not tool_response["ok"]:
            await ctx.yield_output({"status": "failed", "error": tool_response["error"]})
            return

        preview = tool_response["result"]
        execution_arguments = _coerce_dict(input_payload.get("execution_arguments"))
        for key in ("plan_token", "recursive", "relative_path", "max_depth"):
            if key in preview and key not in execution_arguments:
                execution_arguments[key] = preview.get(key)

        self.bridge.emit_status("planning-folder")
        organization_plan = _build_organization_plan(
            preview=preview,
            execution_arguments=execution_arguments,
        )
        await ctx.yield_output(_plan_completion_output(organization_plan))

    @response_handler(request=dict, response=dict, workflow_output=dict)
    async def handle_approval(
        self,
        original_request,
        response,
        ctx,
    ) -> None:
        payload = _coerce_dict(original_request.get("payload"))
        if not bool(response.get("approved")):
            await ctx.yield_output({"status": "rejected", "summary": "Folder organization was cancelled."})
            return

        organization_plan = _coerce_dict(payload.get("organization_plan"))
        if not bool(organization_plan.get("executable")):
            await ctx.yield_output(
                {
                    "status": "failed",
                    "error": "Folder Organizer execution requires a prior executable organization_plan.",
                }
            )
            return

        tool_response = await _execute_approved_plan(self.bridge, payload=payload)
        if not tool_response["ok"]:
            await ctx.yield_output({"status": "failed", "error": tool_response["error"]})
            return
        await ctx.yield_output({"status": "executed", "result": tool_response["result"]})


def _checkpoint_storage() -> FileCheckpointStorage:
    configured = os.environ.get("AZUL_SKILL_WORKFLOW_CHECKPOINT_DIR", "").strip()
    root = Path(configured) if configured else Path.cwd() / ".azul_workflow_checkpoints"
    return FileCheckpointStorage(root)


def _build_workflow(bridge: HostBridge):
    executor = FolderOrganizerWorkflowExecutor(bridge)
    return WorkflowBuilder(
        name=WORKFLOW_NAME,
        description="Folder Organizer preview, HITL approval, and execution flow.",
        start_executor=executor,
        checkpoint_storage=_checkpoint_storage(),
        output_executors=[executor],
    ).build()


async def _emit_workflow_result(result, *, storage: FileCheckpointStorage) -> None:
    for output in result.get_outputs():
        if not isinstance(output, dict):
            continue
        status = str(output.get("status", "")).strip()
        if status == "failed":
            _emit({"type": "failed", "error": str(output.get("error", "Workflow failed.")).strip()})
            return
        _emit({"type": "completed", "data": output})
        return

    requests = result.get_request_info_events()
    if not requests:
        _emit({"type": "completed", "data": {"status": "idle"}})
        return

    checkpoint = await storage.get_latest(workflow_name=WORKFLOW_NAME)
    checkpoint_id = checkpoint.checkpoint_id if checkpoint is not None else ""
    request_event = requests[-1]
    request = _coerce_dict(request_event.data)
    if checkpoint_id:
        request["checkpoint_id"] = checkpoint_id
    if request_event.request_id and not str(request.get("request_id", "")).strip():
        request["request_id"] = request_event.request_id
    _emit(
        {
            "type": "request_info",
            "checkpoint_id": checkpoint_id,
            "request": request,
            "workflow_state": result.get_final_state().value,
        }
    )


async def _start_workflow(message: dict) -> None:
    storage = _checkpoint_storage()
    workflow = _build_workflow(HostBridge())
    result = await workflow.run(message, checkpoint_storage=storage)
    await _emit_workflow_result(result, storage=storage)


async def _resume_workflow(message: dict) -> None:
    storage = _checkpoint_storage()
    workflow = _build_workflow(HostBridge())
    request = _coerce_dict(message.get("request"))
    response = _coerce_dict(message.get("response"))
    request_id = str(request.get("request_id", message.get("request_id", ""))).strip()
    checkpoint_id = str(message.get("checkpoint_id", "")).strip() or str(request.get("checkpoint_id", "")).strip()
    if not checkpoint_id:
        _emit({"type": "failed", "error": "missing-checkpoint"})
        return
    if not request_id:
        _emit({"type": "failed", "error": "missing-request-id"})
        return

    result = await workflow.run(
        responses={request_id: response},
        checkpoint_id=checkpoint_id,
        checkpoint_storage=storage,
    )
    await _emit_workflow_result(result, storage=storage)


def main() -> int:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _emit({"type": "failed", "error": "invalid-json"})
            continue
        if message.get("type") == "ping":
            _emit({"type": "pong", "skill": SKILL_ID})
        elif message.get("type") == "shutdown":
            return 0
        elif message.get("type") == "start":
            try:
                asyncio.run(_start_workflow(message))
            except Exception as error:
                _emit({"type": "failed", "error": str(error)})
            return 0
        elif message.get("type") == "resume":
            try:
                asyncio.run(_resume_workflow(message))
            except Exception as error:
                _emit({"type": "failed", "error": str(error)})
            return 0
        else:
            _emit(
                {
                    "type": "status",
                    "data": {
                        "status": "workflow-entrypoint-ready",
                        "detail": "Folder Organizer workflow worker is installed.",
                    },
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
