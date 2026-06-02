from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from azul_backend.azul_brain import config
from azul_backend.azul_brain.api import skill_services
from azul_backend.azul_brain.api.routes import desktop_workflow_request_decision_handler
from azul_backend.azul_brain.conversation import ConversationOrchestrator
from azul_backend.azul_brain.mcp_client import AzulMCPMultiplexer
from azul_backend.azul_brain.runtime.agent_runtime import AgentRuntimeManager
from azul_backend.azul_brain.runtime.approval_service import ApprovalService
from azul_backend.azul_brain.runtime.process_registry import ProcessRegistry
from azul_backend.azul_brain.runtime.semantic_judge import SemanticJudgeService
from azul_backend.azul_brain.runtime.skill_workflow_runtime import SkillWorkflowRuntime, SkillWorkflowStore
from azul_backend.azul_brain.runtime.store import RuntimeStore


REPO_ROOT = Path(__file__).resolve().parents[1]
FOLDER_ORGANIZER_SKILL_ID = "dev.azulclaw.desktop-organizer"


def _load_local_env() -> None:
    config.load_env_files(REPO_ROOT / "azul_backend" / "azul_brain")
    config.load_env_files(REPO_ROOT / "azul_backend")


def _live_semantic_ready() -> tuple[bool, str]:
    _load_local_env()
    if os.environ.get("AZUL_RUN_LIVE_SEMANTIC_TESTS", "").strip() != "1":
        return False, "set AZUL_RUN_LIVE_SEMANTIC_TESTS=1 to run live semantic tests"
    required = {
        "AZURE_OPENAI_ENDPOINT": os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip(),
        "AZURE_OPENAI_API_KEY": os.environ.get("AZURE_OPENAI_API_KEY", "").strip(),
        "AZURE_OPENAI_FAST_DEPLOYMENT": (
            os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "").strip()
            or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
        ),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        return False, f"missing live Azure OpenAI setting(s): {', '.join(missing)}"
    placeholders = [
        key
        for key, value in required.items()
        if value.casefold().startswith("tu-") or "tu_" in value.casefold() or "your-" in value.casefold()
    ]
    if placeholders:
        return False, f"replace placeholder Azure OpenAI setting(s): {', '.join(placeholders)}"
    os.environ.setdefault("AZUL_AZURE_OPENAI_AUTH_MODE", "api_key")
    return True, ""


def _workflow_spec() -> dict[str, object]:
    worker = REPO_ROOT / "skills" / "official" / "desktop-organizer" / "workflow" / "main.py"
    return {
        "skill_id": FOLDER_ORGANIZER_SKILL_ID,
        "skill_name": "Folder Organizer",
        "description": "Organizes files inside the configured target folder.",
        "capabilities": [
            "Preview the configured target folder.",
            "Build an organization plan before any execution.",
            "Move files only after human approval.",
        ],
        "mode": "isolated_process",
        "protocol_version": "1.0",
        "command": sys.executable,
        "args": [str(worker.resolve())],
        "cwd": str(worker.parent.resolve()),
        "activation": {
            "workflow_intents": [
                "Build a folder organization plan for the configured target folder.",
                "Preview and optionally apply file moves after human approval.",
            ],
            "workflow_examples": [
                "Dame un plan de organizacion de la carpeta objetivo.",
                "Organize my target folder and ask before moving files.",
            ],
        },
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
        "input_defaults": {
            "preview_arguments": {"recursive": True, "include_moves": True},
        },
    }


class RecordingMemory:
    _conn = None

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def add_message(self, user_id: str, role: str, content: str, conversation_id: str | None = None) -> str:
        message_id = f"msg-{len(self.messages) + 1}"
        self.messages.append(
            {
                "id": message_id,
                "user_id": user_id,
                "conversation_id": conversation_id or "",
                "role": role,
                "content": content,
            }
        )
        return message_id

    def get_conversation_messages(self, conversation_id: str, limit: int = 12) -> list[dict]:
        return []

    def get_history(self, user_id: str, limit: int = 12) -> list[dict]:
        return []

    def get_conversation_title(self, conversation_id: str) -> str | None:
        return None


class JsonToolResult:
    def __init__(self, payload: dict) -> None:
        self.content = [SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))]


class FakeFolderOrganizerMcpClient:
    def __init__(self, preview_payload: dict) -> None:
        self.preview_payload = preview_payload
        self.calls: list[tuple[str, dict, str]] = []

    async def call_tool(self, tool_name: str, arguments: dict, *, skill_id: str = "") -> JsonToolResult:
        self.calls.append((tool_name, dict(arguments), skill_id))
        if tool_name != "preview_folder_organization":
            raise AssertionError(f"unexpected live skill smoke tool call: {tool_name}")
        return JsonToolResult(self.preview_payload)


class DummyPrimaryMcpClient:
    async def cleanup(self) -> None:
        return None


class WorkflowDecisionRequest:
    def __init__(
        self,
        *,
        payload: dict,
        orchestrator: ConversationOrchestrator,
        runtime: SkillWorkflowRuntime,
        mcp_client: AzulMCPMultiplexer,
        run_id: str,
        request_id: str,
    ) -> None:
        self._payload = payload
        self.app = {
            "orchestrator": orchestrator,
            "skill_workflow_runtime": runtime,
            "mcp_client": mcp_client,
        }
        self.match_info = {"run_id": run_id, "request_id": request_id}

    async def json(self) -> dict:
        return self._payload


class LiveFolderOrganizerSkillTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ready, reason = _live_semantic_ready()
        if not ready:
            raise unittest.SkipTest(reason)

    def setUp(self) -> None:
        self.runtime_root = REPO_ROOT / "memory" / "test-live-folder-organizer"
        shutil.rmtree(self.runtime_root, ignore_errors=True)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.runtime_store = RuntimeStore(
            settings_path=self.runtime_root / "runtime-settings.json",
            jobs_path=self.runtime_root / "runtime-jobs.json",
            process_history_path=self.runtime_root / "runtime-process-history.json",
        )
        self.runtime_manager = AgentRuntimeManager(
            mcp_client=None,
            store=self.runtime_store,
            process_registry=ProcessRegistry(self.runtime_store),
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.runtime_root, ignore_errors=True)

    async def test_live_semantic_router_selects_folder_organizer_workflow(self) -> None:
        judge = SemanticJudgeService(self.runtime_manager)

        verdict = await judge.judge_skill_workflow_route(
            user_message=(
                "Dame un plan de organizacion utilizando la skill de folder organizer "
                "de la carpeta objetivo, con nombres que tengan sentido."
            ),
            workflow_specs=[
                _workflow_spec(),
                {
                    "skill_id": "dev.example.telegram",
                    "skill_name": "Telegram",
                    "description": "Send and receive Telegram messages.",
                    "capabilities": ["Telegram channel connector"],
                    "activation": {"workflow_intents": ["Send a Telegram message."]},
                },
            ],
        )

        self.assertIsInstance(verdict, dict)
        self.assertEqual(verdict.get("decision"), "run_workflow")
        self.assertEqual(verdict.get("skill_id"), FOLDER_ORGANIZER_SKILL_ID)

    async def test_live_semantic_router_handles_natural_phrasings_without_naming_skill(self) -> None:
        judge = SemanticJudgeService(self.runtime_manager)
        other_spec = {
            "skill_id": "dev.example.telegram",
            "skill_name": "Telegram",
            "description": "Send and receive Telegram messages.",
            "capabilities": ["Telegram channel connector"],
            "activation": {"workflow_intents": ["Send a Telegram message."]},
        }
        natural_phrasings = [
            "organiza mi escritorio",
            "necesito limpiar y organizar esa carpeta",
            "clasifica los archivos de la carpeta",
            "tidy up and sort my files",
        ]

        for phrasing in natural_phrasings:
            with self.subTest(phrasing=phrasing):
                verdict = await judge.judge_skill_workflow_route(
                    user_message=phrasing,
                    workflow_specs=[_workflow_spec(), other_spec],
                )
                self.assertIsInstance(verdict, dict)
                self.assertEqual(verdict.get("decision"), "run_workflow")
                self.assertEqual(verdict.get("skill_id"), FOLDER_ORGANIZER_SKILL_ID)

    async def test_live_conversation_runs_folder_plan_without_frontend_or_backend(self) -> None:
        preview_payload = {
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
        mcp_client = FakeFolderOrganizerMcpClient(preview_payload)
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.mcp_client = mcp_client
        orchestrator.runtime_manager = self.runtime_manager
        orchestrator.skill_workflow_runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(self.runtime_root / "skill-workflows.json"),
            approval_service=ApprovalService(self.runtime_root / "approval-lifecycle.json"),
        )
        orchestrator.semantic_judges = SemanticJudgeService(self.runtime_manager)
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RecordingMemory()
        orchestrator.preference_extractor = None
        orchestrator.vector_memory = None
        orchestrator.embedding_service = None

        with patch(
            "azul_backend.azul_brain.conversation.list_enabled_workflow_runtime_specs",
            return_value=[_workflow_spec()],
        ):
            reply = await orchestrator.process_user_message(
                "desktop-user",
                (
                    "Dame un plan de organizacion utilizando la skill de folder organizer "
                    "de la carpeta objetivo. Dame un plan que me de sentido."
                ),
                conversation_id="conv-live-folder",
            )

        self.assertEqual(reply.turn_status, "final_answer")
        self.assertTrue(reply.text.strip())
        self.assertTrue(reply.workflow_events)
        self.assertFalse(any(event.get("type") == "request_info" for event in reply.workflow_events or []))
        self.assertEqual([call[0] for call in mcp_client.calls], ["preview_folder_organization"])

    async def test_live_conversation_accepts_generated_folder_plan_and_creates_hitl_request(self) -> None:
        preview_payload = {
            "summary": "2 file(s) ready to organize.",
            "recursive": True,
            "moves": [
                {
                    "source_relative_path": "Facturas/Factura Enero.pdf",
                    "destination_relative_path": "Facturas/2026/Factura Enero.pdf",
                    "category": "Facturas 2026",
                    "status": "planned",
                },
                {
                    "source_relative_path": "Proyecto AzulClaw/notas.txt",
                    "destination_relative_path": "Proyectos/AzulClaw/notas.txt",
                    "category": "Proyecto AzulClaw",
                    "status": "planned",
                },
            ],
        }
        mcp_client = FakeFolderOrganizerMcpClient(preview_payload)
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.mcp_client = mcp_client
        orchestrator.runtime_manager = self.runtime_manager
        orchestrator.skill_workflow_runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(self.runtime_root / "skill-workflows.json"),
            approval_service=ApprovalService(self.runtime_root / "approval-lifecycle.json"),
        )
        orchestrator.semantic_judges = SemanticJudgeService(self.runtime_manager)
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RecordingMemory()
        orchestrator.preference_extractor = None
        orchestrator.vector_memory = None
        orchestrator.embedding_service = None

        with patch(
            "azul_backend.azul_brain.conversation.list_enabled_workflow_runtime_specs",
            return_value=[_workflow_spec()],
        ):
            plan_reply = await orchestrator.process_user_message(
                "desktop-user",
                (
                    "Dame un plan de organizacion utilizando la skill de folder organizer "
                    "de la carpeta objetivo."
                ),
                conversation_id="conv-live-folder-approval",
            )
            approval_reply = await orchestrator.process_user_message(
                "desktop-user",
                "Me parece bien el plan, preparalo para aplicarlo.",
                conversation_id="conv-live-folder-approval",
            )

        self.assertEqual(plan_reply.turn_status, "final_answer")
        self.assertTrue(plan_reply.text.strip())
        self.assertFalse(any(event.get("type") == "request_info" for event in plan_reply.workflow_events or []))
        self.assertEqual(approval_reply.turn_status, "approval_required")
        self.assertTrue(approval_reply.text.strip())
        approval_request_event = next(
            (event for event in approval_reply.workflow_events or [] if event.get("type") == "request_info"),
            None,
        )
        self.assertIsNotNone(approval_request_event)
        self.assertTrue(str(approval_request_event["data"].get("summary", "")).strip())
        self.assertEqual([call[0] for call in mcp_client.calls], ["preview_folder_organization"])

    async def test_live_folder_organizer_full_e2e_with_real_mcp_runtime(self) -> None:
        target_folder = self.runtime_root / "Javier Escritorio"
        target_folder.mkdir(parents=True, exist_ok=True)
        (target_folder / "Factura Enero.pdf").write_text("factura", encoding="utf-8")
        (target_folder / "foto viaje.jpg").write_text("foto", encoding="utf-8")
        (target_folder / "notas proyecto.txt").write_text("notas", encoding="utf-8")

        workflow_runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(self.runtime_root / "skill-workflows.json"),
            approval_service=ApprovalService(self.runtime_root / "approval-lifecycle.json"),
        )
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.runtime_manager = self.runtime_manager
        orchestrator.skill_workflow_runtime = workflow_runtime
        orchestrator.semantic_judges = SemanticJudgeService(self.runtime_manager)
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RecordingMemory()
        orchestrator.preference_extractor = None
        orchestrator.vector_memory = None
        orchestrator.embedding_service = None

        mux = AzulMCPMultiplexer(
            DummyPrimaryMcpClient(),
            skill_specs_provider=skill_services.list_enabled_local_mcp_runtime_specs,
        )
        with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": str(self.runtime_root)}, clear=False):
            skill_services.install_skill(FOLDER_ORGANIZER_SKILL_ID)
            skill_services.configure_skill(
                FOLDER_ORGANIZER_SKILL_ID,
                {
                    "targetFolder": str(target_folder),
                    "organizationDepth": "1",
                    "semanticCategorization": False,
                },
            )
            skill_services.update_skill_enabled(FOLDER_ORGANIZER_SKILL_ID, True)
            try:
                await mux.reload_skill_clients()
                orchestrator.mcp_client = mux

                plan_reply = await orchestrator.process_user_message(
                    "desktop-user",
                    "Dame un plan de organizacion de la carpeta objetivo con Folder Organizer.",
                    conversation_id="conv-live-folder-real-mcp",
                )
                approval_reply = await orchestrator.process_user_message(
                    "desktop-user",
                    "El plan me parece bien, preparalo para aplicarlo.",
                    conversation_id="conv-live-folder-real-mcp",
                )
                request_event = next(
                    event for event in approval_reply.workflow_events or [] if event.get("type") == "request_info"
                )
                request_data = request_event["data"]
                decision_request = WorkflowDecisionRequest(
                    payload={"user_id": "desktop-user", "decision": "approve"},
                    orchestrator=orchestrator,
                    runtime=workflow_runtime,
                    mcp_client=mux,
                    run_id=str(request_data["run_id"]),
                    request_id=str(request_data["request_id"]),
                )
                decision_response = await desktop_workflow_request_decision_handler(decision_request)
            finally:
                await mux.cleanup()

        decision_payload = json.loads(decision_response.text)
        remaining_top_level_files = sorted(path.name for path in target_folder.iterdir() if path.is_file())
        final_listing = sorted(
            str(path.relative_to(target_folder)).replace("\\", "/")
            for path in target_folder.rglob("*")
            if path.is_file()
        )

        self.assertEqual(plan_reply.turn_status, "final_answer")
        self.assertTrue(plan_reply.text.strip())
        self.assertFalse(any(event.get("type") == "request_info" for event in plan_reply.workflow_events or []))
        self.assertEqual(approval_reply.turn_status, "approval_required")
        self.assertEqual(decision_response.status, 200)
        self.assertEqual(decision_payload["status"], "completed")
        self.assertEqual(decision_payload["workflow_status"], "completed")
        self.assertIn("moved", decision_payload["reply"])
        self.assertEqual(remaining_top_level_files, [])
        self.assertEqual(
            final_listing,
            [
                "Documents/Factura Enero.pdf",
                "Documents/notas proyecto.txt",
                "Images/foto viaje.jpg",
            ],
        )


if __name__ == "__main__":
    unittest.main()
