from __future__ import annotations

import asyncio
import shutil
import sys
import unittest
from pathlib import Path

from azul_backend.azul_brain.runtime.approval_service import ApprovalService
from azul_backend.azul_brain.runtime.skill_workflow_runtime import (
    HumanApprovalResponse,
    SkillWorkflowRuntime,
    SkillWorkflowStore,
)


class SkillWorkflowRuntimeTests(unittest.TestCase):
    def _runtime_dir(self, name: str) -> Path:
        root = Path("memory") / "test-skill-workflows" / name
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_human_approval_request_registers_lifecycle_and_pauses_run(self) -> None:
        root = self._runtime_dir("approval-request")
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )

        try:
            run = runtime.start_run(
                skill_id="dev.azulclaw.desktop-organizer",
                user_id="desktop-user",
                conversation_id="conv-1",
                workflow_name="desktop-organizer",
            )
            request = runtime.request_human_approval(
                run_id=run.run_id,
                request_id="approval-1",
                action_kind="move_files",
                title="Folder Organizer",
                summary="Approve moving 4 files.",
                payload={"tool": "organize_target_folder"},
            )

            stored_run = runtime.store.get_run(run.run_id)
            lifecycle = runtime.approval_service.get_by_action_id("approval-1")

            self.assertEqual(request.request_id, "approval-1")
            self.assertIsNotNone(stored_run)
            assert stored_run is not None
            self.assertEqual(stored_run.status, "waiting_for_human")
            self.assertEqual(stored_run.pending_request_id, "approval-1")
            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(lifecycle.source, "skill_workflow")
            self.assertEqual(lifecycle.action_kind, "move_files")
            self.assertEqual(lifecycle.status, "pending")
            self.assertEqual(lifecycle.metadata["run_id"], run.run_id)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolated_worker_request_info_registers_human_approval(self) -> None:
        root = self._runtime_dir("isolated-request-info")
        worker = root / "worker.py"
        worker.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    "message = json.loads(sys.stdin.readline())",
                    "print(json.dumps({",
                    "  'type': 'request_info',",
                    "  'request': {",
                    "    'request_id': 'approval-1',",
                    "    'action_kind': 'move_files',",
                    "    'title': 'Folder Organizer',",
                    "    'summary': 'Approve moving 4 files.',",
                    "    'payload': {'move_count': 4},",
                    "  }",
                    "}), flush=True)",
                ]
            ),
            encoding="utf-8",
        )
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )

        async def _run() -> tuple[str, list[str]]:
            run, events = await runtime.start_isolated_workflow(
                spec={
                    "skill_id": "dev.azulclaw.desktop-organizer",
                    "skill_name": "Folder Organizer",
                    "mode": "isolated_process",
                    "protocol_version": "1.0",
                    "command": sys.executable,
                    "args": [str(worker.resolve())],
                    "cwd": str(root),
                    "tools": {"preview": "preview_folder_organization"},
                },
                user_id="desktop-user",
                conversation_id="conv-1",
                input_payload={"prompt": "organize"},
            )
            return run.run_id, [event.type for event in events]

        try:
            run_id, event_types = asyncio.run(_run())
            stored_run = runtime.store.get_run(run_id)
            lifecycle = runtime.approval_service.get_by_action_id("approval-1")

            self.assertEqual(event_types, ["request_info"])
            self.assertIsNotNone(stored_run)
            assert stored_run is not None
            self.assertEqual(stored_run.status, "waiting_for_human")
            self.assertEqual(stored_run.pending_request_id, "approval-1")
            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(lifecycle.status, "pending")
            self.assertEqual(lifecycle.metadata["payload"]["move_count"], 4)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolated_worker_tool_calls_are_limited_to_declared_tools(self) -> None:
        root = self._runtime_dir("isolated-tool-call")
        worker = root / "worker.py"
        worker.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    "json.loads(sys.stdin.readline())",
                    "print(json.dumps({'type': 'tool_call', 'id': 'tool-1', 'tool': 'preview', 'arguments': {'recursive': True}}), flush=True)",
                    "result = json.loads(sys.stdin.readline())",
                    "print(json.dumps({'type': 'completed', 'data': {'tool_ok': result.get('ok'), 'tool_name': result.get('result', {}).get('tool')}}), flush=True)",
                ]
            ),
            encoding="utf-8",
        )
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )

        async def _run() -> tuple[str, list[dict]]:
            async def _tool_invoker(tool_name: str, arguments: dict) -> dict:
                self.assertEqual(tool_name, "preview_folder_organization")
                self.assertEqual(arguments, {"recursive": True})
                return {"tool": tool_name, "summary": "2 file(s) ready to organize."}

            run, events = await runtime.start_isolated_workflow(
                spec={
                    "skill_id": "dev.azulclaw.desktop-organizer",
                    "skill_name": "Folder Organizer",
                    "mode": "isolated_process",
                    "protocol_version": "1.0",
                    "command": sys.executable,
                    "args": [str(worker.resolve())],
                    "cwd": str(root),
                    "tools": {"preview": "preview_folder_organization"},
                },
                user_id="desktop-user",
                conversation_id="conv-1",
                tool_invoker=_tool_invoker,
            )
            return run.run_id, [event.data for event in events]

        try:
            run_id, event_data = asyncio.run(_run())
            stored_run = runtime.store.get_run(run_id)

            self.assertEqual(event_data[-1]["tool_ok"], True)
            self.assertEqual(event_data[-1]["tool_name"], "preview_folder_organization")
            self.assertIsNotNone(stored_run)
            assert stored_run is not None
            self.assertEqual(stored_run.status, "completed")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolated_worker_sensitive_tool_requires_approved_hitl_request(self) -> None:
        root = self._runtime_dir("isolated-sensitive-tool-policy")
        worker = root / "worker.py"
        worker.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    "json.loads(sys.stdin.readline())",
                    "print(json.dumps({'type': 'tool_call', 'id': 'execute-1', 'tool': 'execute', 'arguments': {'recursive': True}}), flush=True)",
                    "result = json.loads(sys.stdin.readline())",
                    "print(json.dumps({'type': 'completed', 'data': {'tool_ok': result.get('ok'), 'error': result.get('error')}}), flush=True)",
                ]
            ),
            encoding="utf-8",
        )
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )

        async def _run() -> list[dict]:
            async def _tool_invoker(_tool_name: str, _arguments: dict) -> dict:
                raise AssertionError("Sensitive tool should not be invoked without HITL approval.")

            _run, events = await runtime.start_isolated_workflow(
                spec={
                    "skill_id": "dev.azulclaw.desktop-organizer",
                    "skill_name": "Folder Organizer",
                    "mode": "isolated_process",
                    "protocol_version": "1.0",
                    "command": sys.executable,
                    "args": [str(worker.resolve())],
                    "cwd": str(root),
                    "tools": {
                        "execute": "organize_target_folder",
                    },
                    "tool_policies": {
                        "execute": {
                            "requires_approval": True,
                            "sensitive_action": "move_files",
                        }
                    },
                    "sensitive_actions": ["move_files"],
                },
                user_id="desktop-user",
                conversation_id="conv-1",
                tool_invoker=_tool_invoker,
            )
            return [event.data for event in events]

        try:
            event_data = asyncio.run(_run())

            self.assertEqual(event_data[-1]["tool_ok"], False)
            self.assertIn("requires approved HITL action", event_data[-1]["error"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_folder_organizer_worker_creates_approval_only_after_plan_acceptance(self) -> None:
        root = self._runtime_dir("folder-worker")
        worker = Path("skills") / "official" / "desktop-organizer" / "workflow" / "main.py"
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )
        tool_calls: list[str] = []

        async def _run() -> tuple[str, str, list[dict], list[dict], list[dict]]:
            async def _tool_invoker(tool_name: str, arguments: dict) -> dict:
                tool_calls.append(tool_name)
                if tool_name == "preview_folder_organization":
                    self.assertEqual(arguments, {"recursive": True})
                    return {
                        "summary": "2 file(s) ready to organize.",
                        "recursive": True,
                        "moves": [
                            {"source": "invoice.pdf", "destination": "Documents/invoice.pdf", "status": "planned"},
                            {"source": "photo.jpg", "destination": "Images/photo.jpg", "status": "planned"},
                        ],
                    }
                self.assertEqual(tool_name, "organize_target_folder")
                self.assertEqual(arguments, {"recursive": True})
                return {"summary": "2 file(s) moved."}

            spec = {
                "skill_id": "dev.azulclaw.desktop-organizer",
                "skill_name": "Folder Organizer",
                "mode": "isolated_process",
                "protocol_version": "1.0",
                "command": sys.executable,
                "args": [str(worker.resolve())],
                "cwd": str(worker.parent.resolve()),
                "tools": {
                    "preview": "preview_folder_organization",
                    "execute": "organize_target_folder",
                },
                "tool_policies": {
                    "execute": {
                        "requires_approval": True,
                        "sensitive_action": "move_files",
                    }
                },
                "sensitive_actions": ["move_files"],
            }
            plan_run, plan_events = await runtime.start_isolated_workflow(
                spec=spec,
                user_id="desktop-user",
                conversation_id="conv-1",
                input_payload={"recursive": True},
                tool_invoker=_tool_invoker,
            )
            organization_plan = plan_events[-1].data["organization_plan"]
            approval_run, events = await runtime.start_isolated_workflow(
                spec=spec,
                user_id="desktop-user",
                conversation_id="conv-1",
                input_payload={"approved_organization_plan": organization_plan},
                tool_invoker=_tool_invoker,
            )
            request_id = events[-1].data["request_id"]
            resumed_run, resume_events = await runtime.resume_isolated_workflow(
                spec=spec,
                run_id=approval_run.run_id,
                request_id=request_id,
                response=HumanApprovalResponse(approved=True, user_id="desktop-user"),
                tool_invoker=_tool_invoker,
            )
            self.assertEqual(resumed_run.run_id, approval_run.run_id)
            return (
                plan_run.run_id,
                approval_run.run_id,
                [event.data for event in plan_events],
                [event.data for event in events],
                [event.data for event in resume_events],
            )

        try:
            plan_run_id, approval_run_id, plan_event_data, event_data, resume_event_data = asyncio.run(_run())
            stored_plan_run = runtime.store.get_run(plan_run_id)
            stored_approval_run = runtime.store.get_run(approval_run_id)
            request_event = event_data[-1]
            lifecycle = runtime.approval_service.get_by_action_id(request_event["request_id"])

            self.assertEqual(tool_calls, ["preview_folder_organization", "organize_target_folder"])
            self.assertEqual(plan_event_data[-1]["status"], "plan_ready")
            self.assertTrue(plan_event_data[-1]["organization_plan"]["executable"])
            self.assertEqual(request_event["action_kind"], "move_files")
            self.assertEqual(request_event["payload"]["execute_tool"], "execute")
            self.assertEqual(request_event["payload"]["execute_arguments"]["recursive"], True)
            self.assertEqual(resume_event_data[-1]["status"], "executed")
            self.assertEqual(resume_event_data[-1]["result"]["summary"], "2 file(s) moved.")
            self.assertIsNotNone(stored_plan_run)
            assert stored_plan_run is not None
            self.assertEqual(stored_plan_run.status, "completed")
            self.assertIsNotNone(stored_approval_run)
            assert stored_approval_run is not None
            self.assertEqual(stored_approval_run.status, "completed")
            self.assertTrue(stored_approval_run.checkpoint_id)
            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(lifecycle.status, "completed")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_folder_organizer_worker_returns_plan_before_any_execution_path(self) -> None:
        root = self._runtime_dir("folder-worker-plan-only")
        worker = Path("skills") / "official" / "desktop-organizer" / "workflow" / "main.py"
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )
        tool_calls: list[str] = []

        async def _run() -> tuple[object, list[dict]]:
            async def _tool_invoker(tool_name: str, arguments: dict) -> dict:
                tool_calls.append(tool_name)
                self.assertEqual(tool_name, "preview_folder_organization")
                self.assertEqual(arguments, {"recursive": True, "include_moves": True})
                return {
                    "summary": (
                        "No files are ready to organize. 1 blocked by conflicts. "
                        "Other: 1. Blocked files: Escritorio/kdenlive-25.04.1_standalone/LICENSE."
                    ),
                    "recursive": True,
                    "blocked_items": [
                        {
                            "source_relative_path": "Escritorio/kdenlive-25.04.1_standalone/LICENSE",
                            "category": "Other",
                            "status": "blocked",
                        }
                    ],
                }

            spec = {
                "skill_id": "dev.azulclaw.desktop-organizer",
                "skill_name": "Folder Organizer",
                "mode": "isolated_process",
                "protocol_version": "1.0",
                "command": sys.executable,
                "args": [str(worker.resolve())],
                "cwd": str(worker.parent.resolve()),
                "tools": {
                    "preview": "preview_folder_organization",
                    "execute": "organize_target_folder",
                },
                "tool_policies": {
                    "execute": {
                        "requires_approval": True,
                        "sensitive_action": "move_files",
                    }
                },
                "sensitive_actions": ["move_files"],
            }
            return await runtime.start_isolated_workflow(
                spec=spec,
                user_id="desktop-user",
                conversation_id="conv-1",
                input_payload={
                    "preview_arguments": {"recursive": True, "include_moves": True},
                },
                tool_invoker=_tool_invoker,
            )

        try:
            run, events = asyncio.run(_run())
            event_types = [event.type for event in events]
            completed = events[-1].data
            plan = completed["organization_plan"]
            lifecycle_records = runtime.approval_service.load()

            self.assertEqual(run.status, "completed")
            self.assertNotIn("request_info", event_types)
            self.assertEqual(tool_calls, ["preview_folder_organization"])
            self.assertEqual(completed["status"], "plan_only")
            self.assertFalse(plan["executable"])
            self.assertEqual(plan["status"], "blocked")
            self.assertEqual(plan["blocked_count"], 1)
            self.assertEqual(plan["execute_arguments"], {})
            self.assertIn("Organization plan generated", completed["summary"])
            self.assertIn("Executable moves: none", completed["summary"])
            self.assertIn("Blocked items to resolve", completed["summary"])
            self.assertIn("No files are ready to organize", plan["summary"])
            self.assertEqual(lifecycle_records, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_folder_organizer_worker_uses_batched_preview_when_moves_are_truncated(self) -> None:
        root = self._runtime_dir("folder-worker-truncated-batches")
        worker = Path("skills") / "official" / "desktop-organizer" / "workflow" / "main.py"
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )
        tool_calls: list[tuple[str, dict]] = []
        execute_results = [
            {
                "summary": "28 file(s) moved. Documents: 7, Images: 7.",
                "selected_batch": {"moved_count": 28},
                "remaining_batch_count": 1,
                "next_batch_index": 2,
                "plan_complete": False,
            },
            {
                "summary": "48 file(s) moved. Documents: 47, Images: 1.",
                "selected_batch": {"moved_count": 48},
                "remaining_batch_count": 0,
                "next_batch_index": None,
                "plan_complete": True,
            },
        ]

        async def _run() -> tuple[list[dict], list[dict], list[dict]]:
            async def _tool_invoker(tool_name: str, arguments: dict) -> dict:
                tool_calls.append((tool_name, dict(arguments)))
                if tool_name == "preview_folder_organization":
                    return {
                        "summary": (
                            "76 file(s) ready to organize. Documents: 54, Images: 8. "
                            "Result truncated to keep the planning context bounded."
                        ),
                        "recursive": True,
                        "move_detail_mode": "full",
                        "truncated": True,
                        "total_move_count": 76,
                        "displayed_move_count": 0,
                        "omitted_move_count": 76,
                        "moves": [],
                        "plan_token": "plan-token-123",
                        "plan_batch_count": 2,
                        "remaining_batch_count": 2,
                        "batches": [
                            {
                                "batch_index": 1,
                                "source_folder_relative_path": ".",
                                "planned_count": 28,
                                "blocked_count": 0,
                                "summary": "28 file(s) ready to organize.",
                                "categories": {"Documents": 7, "Images": 7},
                            },
                            {
                                "batch_index": 2,
                                "source_folder_relative_path": "ALGEBRA",
                                "planned_count": 48,
                                "blocked_count": 0,
                                "summary": "48 file(s) ready to organize.",
                                "categories": {"Documents": 47, "Images": 1},
                            },
                        ],
                    }
                self.assertEqual(tool_name, "organize_target_folder")
                self.assertEqual(arguments, {"plan_token": "plan-token-123", "recursive": True})
                return execute_results.pop(0)

            spec = {
                "skill_id": "dev.azulclaw.desktop-organizer",
                "skill_name": "Folder Organizer",
                "mode": "isolated_process",
                "protocol_version": "1.0",
                "command": sys.executable,
                "args": [str(worker.resolve())],
                "cwd": str(worker.parent.resolve()),
                "tools": {
                    "preview": "preview_folder_organization",
                    "execute": "organize_target_folder",
                },
                "tool_policies": {
                    "execute": {
                        "requires_approval": True,
                        "sensitive_action": "move_files",
                    }
                },
                "sensitive_actions": ["move_files"],
            }
            plan_run, plan_events = await runtime.start_isolated_workflow(
                spec=spec,
                user_id="desktop-user",
                conversation_id="conv-1",
                input_payload={"preview_arguments": {"recursive": True, "include_moves": True}},
                tool_invoker=_tool_invoker,
            )
            organization_plan = plan_events[-1].data["organization_plan"]
            approval_run, approval_events = await runtime.start_isolated_workflow(
                spec=spec,
                user_id="desktop-user",
                conversation_id="conv-1",
                input_payload={"approved_organization_plan": organization_plan},
                tool_invoker=_tool_invoker,
            )
            request_id = approval_events[-1].data["request_id"]
            _resumed_run, resume_events = await runtime.resume_isolated_workflow(
                spec=spec,
                run_id=approval_run.run_id,
                request_id=request_id,
                response=HumanApprovalResponse(approved=True, user_id="desktop-user"),
                tool_invoker=_tool_invoker,
            )
            self.assertEqual(plan_run.status, "completed")
            return [event.data for event in plan_events], [event.data for event in approval_events], [
                event.data for event in resume_events
            ]

        try:
            plan_event_data, approval_event_data, resume_event_data = asyncio.run(_run())
            plan = plan_event_data[-1]["organization_plan"]
            result = resume_event_data[-1]["result"]

            self.assertEqual(plan_event_data[-1]["status"], "plan_ready")
            self.assertTrue(plan["executable"])
            self.assertEqual(plan["planned_move_count"], 76)
            self.assertEqual(plan["plan_batch_count"], 2)
            self.assertIn("Reviewed batches", plan_event_data[-1]["summary"])
            self.assertEqual(approval_event_data[-1]["action_kind"], "move_files")
            self.assertEqual(result["batch_execution_count"], 2)
            self.assertEqual(result["moved_count"], 76)
            self.assertTrue(result["plan_complete"])
            self.assertEqual(
                [name for name, _arguments in tool_calls],
                [
                    "preview_folder_organization",
                    "organize_target_folder",
                    "organize_target_folder",
                ],
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_resolve_human_approval_marks_request_and_lifecycle(self) -> None:
        root = self._runtime_dir("approval-response")
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )

        try:
            run = runtime.start_run(
                skill_id="dev.azulclaw.desktop-organizer",
                user_id="desktop-user",
                conversation_id="conv-1",
            )
            runtime.request_human_approval(
                run_id=run.run_id,
                request_id="approval-1",
                action_kind="move_files",
                title="Folder Organizer",
                summary="Approve moving 4 files.",
            )

            pending = runtime.resolve_human_approval(
                run_id=run.run_id,
                request_id="approval-1",
                response=HumanApprovalResponse(approved=True, user_id="desktop-user"),
            )

            stored_run = runtime.store.get_run(run.run_id)
            lifecycle = runtime.approval_service.get_by_action_id("approval-1")
            self.assertEqual(pending.status, "approved")
            self.assertIsNotNone(pending.response)
            assert pending.response is not None
            self.assertTrue(pending.response.approved)
            self.assertIsNotNone(stored_run)
            assert stored_run is not None
            self.assertEqual(stored_run.status, "approved")
            self.assertEqual(stored_run.pending_request_id, "")
            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(lifecycle.status, "approved")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
