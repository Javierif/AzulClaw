from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import types
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from azul_backend.azul_brain.api import skill_services
from azul_backend.azul_brain import config as runtime_config
from azul_backend.azul_brain.channels.access_control import evaluate_channel_connector_access
from scripts.package_skills import iter_bundle_files, package_skill, write_catalog

try:
    from azul_backend.azul_brain.mcp_client import AzulMCPMultiplexer
except ModuleNotFoundError:
    AzulMCPMultiplexer = None


class _FakeUrlopenResponse:
    def __init__(self, body: str | bytes):
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class SkillMarketplaceTests(unittest.TestCase):
    @contextmanager
    def _runtime_dir(self, name: str):
        root = Path("memory") / "test-skill-marketplace" / name
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            yield str(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_marketplace_discovers_official_skills(self) -> None:
        with self._runtime_dir("catalog") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                catalog = skill_services.list_marketplace_skills()

        ids = {item["id"] for item in catalog["items"]}
        self.assertIn("dev.azulclaw.telegram", ids)
        self.assertIn("dev.azulclaw.gemini", ids)
        self.assertIn("dev.azulclaw.desktop-organizer", ids)
        telegram = next(item for item in catalog["items"] if item["id"] == "dev.azulclaw.telegram")
        self.assertTrue(telegram["activation"]["requires_azure_relay"])
        self.assertEqual(telegram["activation"]["relay_function_path"], "src/relay_function")
        self.assertEqual(telegram["deployment"]["skill_root_path"], "skills\\official\\telegram")
        self.assertEqual(telegram["deployment"]["runtime_path"], "skills\\official\\telegram\\src\\relay_function")
        self.assertEqual(telegram["deployment"]["infra_path"], "skills\\official\\telegram\\infra\\terraform")

    def test_install_configure_and_enable_redacts_secret_config(self) -> None:
        with self._runtime_dir("telegram") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                installed = skill_services.install_skill("dev.azulclaw.telegram")
                self.assertTrue(installed["installed"])
                self.assertFalse(installed["configured"])
                self.assertEqual(
                    installed["missing_required_fields"],
                    ["serviceBusConnectionString", "microsoftAppId", "microsoftAppPassword"],
                )

                configured = skill_services.configure_skill(
                    "dev.azulclaw.telegram",
                    {
                        "serviceBusConnectionString": "Endpoint=sb://example/;SharedAccessKey=secret",
                        "microsoftAppId": "app-id",
                        "microsoftAppPassword": "app-secret",
                        "allowedUserIds": "123456789",
                    },
                )
                self.assertTrue(configured["configured"])
                self.assertEqual(configured["config"]["serviceBusConnectionString"], skill_services.SECRET_REDACTION)
                self.assertEqual(configured["config"]["microsoftAppPassword"], skill_services.SECRET_REDACTION)
                self.assertEqual(configured["config"]["allowedUserIds"], "123456789")

                enabled = skill_services.update_skill_enabled("dev.azulclaw.telegram", True)
                self.assertTrue(enabled["enabled"])

                reconfigured = skill_services.configure_skill(
                    "dev.azulclaw.telegram",
                    {
                        "serviceBusConnectionString": "",
                        "microsoftAppId": "app-id",
                        "microsoftAppPassword": "",
                        "allowedUserIds": "987654321",
                    },
                )
                self.assertTrue(reconfigured["configured"])
                self.assertEqual(reconfigured["config"]["serviceBusConnectionString"], skill_services.SECRET_REDACTION)
                self.assertEqual(reconfigured["config"]["microsoftAppPassword"], skill_services.SECRET_REDACTION)
                self.assertEqual(reconfigured["config"]["allowedUserIds"], "987654321")

                state = skill_services.load_installed_skill_state()
                stored_config = state["skills"]["dev.azulclaw.telegram"]["config"]
                self.assertNotIn("serviceBusConnectionString", stored_config)
                self.assertNotIn("microsoftAppPassword", stored_config)
                self.assertEqual(
                    stored_config["_secret_values"],
                    {
                        "SERVICE_BUS_CONNECTION_STRING": "Endpoint=sb://example/;SharedAccessKey=secret",
                        "MicrosoftAppPassword": "app-secret",
                    },
                )

    def test_enable_rejects_missing_required_config(self) -> None:
        with self._runtime_dir("desktop-organizer") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.install_skill("dev.azulclaw.desktop-organizer")

                with self.assertRaises(ValueError) as raised:
                    skill_services.update_skill_enabled("dev.azulclaw.desktop-organizer", True)

        self.assertIn("missing required config", str(raised.exception))

    def test_enabled_local_mcp_runtime_specs_resolve_skill_paths_and_env(self) -> None:
        with self._runtime_dir("local-mcp-runtime") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.install_skill("dev.azulclaw.desktop-organizer")
                skill_services.configure_skill(
                    "dev.azulclaw.desktop-organizer",
                    {
                        "targetFolder": "C:/Users/javie/Desktop/Test",
                        "semanticCategorization": True,
                    },
                )
                skill_services.update_skill_enabled("dev.azulclaw.desktop-organizer", True)

                specs = skill_services.list_enabled_local_mcp_runtime_specs()

        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["skill_id"], "dev.azulclaw.desktop-organizer")
        self.assertEqual(Path(spec["command"]).resolve(), Path(sys.executable).resolve())
        resolved_arg = str(spec["args"][0]).replace("\\", "/")
        self.assertTrue(resolved_arg.endswith("mcp/server.py") or resolved_arg == "mcp/server.py")
        self.assertEqual(spec["env"]["AZUL_SKILL_CONFIG_TARGETFOLDER"], "C:/Users/javie/Desktop/Test")
        self.assertEqual(str(spec["env"]["AZUL_SKILL_CONFIG_SEMANTICCATEGORIZATION"]).lower(), "true")
        self.assertIn("AZUL_SKILL_ROOT", spec["env"])

    def test_enabled_workflow_runtime_specs_resolve_isolated_entrypoints(self) -> None:
        with self._runtime_dir("workflow-runtime") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.install_skill("dev.azulclaw.desktop-organizer")
                skill_services.configure_skill(
                    "dev.azulclaw.desktop-organizer",
                    {
                        "targetFolder": "C:/Users/javie/Desktop/Test",
                        "semanticCategorization": True,
                    },
                )
                skill_services.update_skill_enabled("dev.azulclaw.desktop-organizer", True)

                specs = skill_services.list_enabled_workflow_runtime_specs()

        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["skill_id"], "dev.azulclaw.desktop-organizer")
        self.assertEqual(spec["mode"], "isolated_process")
        self.assertEqual(spec["protocol_version"], "1.0")
        self.assertEqual(Path(spec["command"]).resolve(), Path(sys.executable).resolve())
        resolved_arg = str(spec["args"][0]).replace("\\", "/")
        self.assertTrue(resolved_arg.endswith("workflow/main.py") or resolved_arg == "workflow/main.py")
        self.assertEqual(spec["tools"]["preview"], "preview_folder_organization")
        self.assertEqual(spec["tools"]["execute"], "organize_target_folder")
        self.assertEqual(spec["tool_policies"]["execute"]["sensitive_action"], "move_files")
        self.assertTrue(spec["tool_policies"]["execute"]["requires_approval"])
        self.assertEqual(spec["input_defaults"]["preview_arguments"]["include_moves"], True)
        self.assertIn("workflow_intents", spec["activation"])
        self.assertTrue(spec["capabilities"])
        self.assertIn("move_files", spec["sensitive_actions"])
        self.assertEqual(spec["env"]["AZUL_SKILL_CONFIG_TARGETFOLDER"], "C:/Users/javie/Desktop/Test")

    def test_workflow_capability_prompt_loads_declared_skill_asset(self) -> None:
        prompt = skill_services.get_skill_workflow_capability_prompt("dev.azulclaw.desktop-organizer")

        self.assertIn("configured root folder", prompt)
        self.assertIn("Conceptual taxonomy guidance", prompt)
        self.assertIn("Active Projects", prompt)

    def test_desktop_organizer_connects_through_local_mcp_runtime(self) -> None:
        if AzulMCPMultiplexer is None:
            self.skipTest("mcp package is not installed in this test environment.")

        class _DummyPrimaryClient:
            async def cleanup(self) -> None:
                return None

        def _extract_text(result: object) -> str:
            content = getattr(result, "content", None)
            self.assertIsInstance(content, list)
            texts = [
                getattr(item, "text", "")
                for item in content
                if isinstance(getattr(item, "text", None), str)
            ]
            self.assertTrue(texts)
            return "\n".join(texts)

        async def _exercise_runtime(target_folder: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
            mux = AzulMCPMultiplexer(
                _DummyPrimaryClient(),
                skill_specs_provider=skill_services.list_enabled_local_mcp_runtime_specs,
            )
            try:
                await mux.reload_skill_clients()
                status = mux.get_skill_runtime_status()
                preview_result = await mux.call_tool(
                    "preview_folder_organization",
                    {},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                preview = json.loads(_extract_text(preview_result))
                recursive_preview_result = await mux.call_tool(
                    "preview_folder_organization",
                    {"recursive": True},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                recursive_preview = json.loads(_extract_text(recursive_preview_result))
                projects_preview_result = await mux.call_tool(
                    "preview_folder_organization",
                    {"relative_path": "Projects", "recursive": True, "max_depth": 3},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                projects_preview = json.loads(_extract_text(projects_preview_result))
                browse_result = await mux.call_tool(
                    "list_target_folder_contents",
                    {"relative_path": "Projects", "recursive": True, "max_depth": 2},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                browse = json.loads(_extract_text(browse_result))
                organize_result = await mux.call_tool(
                    "organize_target_folder",
                    {},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                organize = json.loads(_extract_text(organize_result))
                recursive_organize_result = await mux.call_tool(
                    "organize_target_folder",
                    {"relative_path": "Projects", "recursive": True, "max_depth": 3},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                recursive_organize = json.loads(_extract_text(recursive_organize_result))
                return status, {
                    "browse": browse,
                    "preview": preview,
                    "recursive_preview": recursive_preview,
                    "projects_preview": projects_preview,
                    "organize": organize,
                    "recursive_organize": recursive_organize,
                    "top_level_files_after": sorted(path.name for path in target_folder.iterdir() if path.is_file()),
                    "listing": sorted(
                        str(path.relative_to(target_folder)).replace("\\", "/")
                        for path in target_folder.rglob("*")
                        if path.is_file()
                    ),
                }
            finally:
                await mux.cleanup()

        with self._runtime_dir("desktop-organizer-mcp-runtime") as runtime_dir:
            folder = (Path(runtime_dir) / "Desktop").resolve()
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "invoice.pdf").write_text("pdf", encoding="utf-8")
            (folder / "photo.jpg").write_text("jpg", encoding="utf-8")
            (folder / "notes.txt").write_text("txt", encoding="utf-8")
            projects = folder / "Projects"
            projects.mkdir()
            (projects / "draft.md").write_text("draft", encoding="utf-8")
            inbox = projects / "Inbox"
            inbox.mkdir()
            (inbox / "sketch.png").write_text("sketch", encoding="utf-8")
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.install_skill("dev.azulclaw.desktop-organizer")
                skill_services.configure_skill(
                    "dev.azulclaw.desktop-organizer",
                    {"targetFolder": str(folder), "organizationDepth": "2"},
                )
                skill_services.update_skill_enabled("dev.azulclaw.desktop-organizer", True)

                status, result = asyncio.run(_exercise_runtime(folder))

        self.assertEqual(len(status), 1)
        self.assertEqual(status[0]["skill_id"], "dev.azulclaw.desktop-organizer")
        self.assertEqual(status[0]["status"], "connected")
        self.assertEqual(result["preview"]["summary"], "3 file(s) ready to organize. Documents: 2, Images: 1.")
        self.assertEqual(len(result["preview"]["moves"]), 3)
        self.assertEqual(result["browse"]["relative_path"], "Projects")
        self.assertTrue(result["browse"]["recursive"])
        self.assertEqual(
            {entry["path"] for entry in result["browse"]["entries"]},
            {"Projects/Inbox", "Projects/draft.md", "Projects/Inbox/sketch.png"},
        )
        self.assertEqual(result["recursive_preview"]["relative_path"], ".")
        self.assertTrue(result["recursive_preview"]["recursive"])
        self.assertEqual(result["recursive_preview"]["max_depth"], 2)
        self.assertEqual(
            result["recursive_preview"]["summary"],
            "4 file(s) ready to organize. Documents: 3, Images: 1.",
        )
        self.assertTrue(result["recursive_preview"]["batched"])
        self.assertEqual(result["recursive_preview"]["move_detail_mode"], "summary-first")
        self.assertEqual(result["recursive_preview"]["moves"], [])
        self.assertIn("plan_token", result["recursive_preview"])
        self.assertEqual(result["recursive_preview"]["next_batch_index"], 1)
        self.assertEqual(result["recursive_preview"]["remaining_batch_count"], 2)
        self.assertEqual(result["projects_preview"]["relative_path"], "Projects")
        self.assertEqual(result["projects_preview"]["max_depth"], 3)
        self.assertIn("plan_token", result["projects_preview"])
        self.assertEqual(result["projects_preview"]["batch_count"], 2)
        self.assertEqual(result["organize"]["summary"], "3 file(s) moved. Documents: 2, Images: 1.")
        self.assertEqual(
            result["recursive_organize"]["summary"],
            "2 file(s) moved. Documents: 1, Images: 1.",
        )
        self.assertEqual(result["top_level_files_after"], [])
        self.assertEqual(
            result["listing"],
            [
                "Documents/invoice.pdf",
                "Documents/notes.txt",
                "Images/photo.jpg",
                "Projects/Documents/draft.md",
                "Projects/Images/sketch.png",
            ],
        )

    def test_desktop_organizer_recursive_plan_token_executes_batches_progressively(self) -> None:
        if AzulMCPMultiplexer is None:
            self.skipTest("mcp package is not installed in this test environment.")

        class _DummyPrimaryClient:
            async def cleanup(self) -> None:
                return None

        def _extract_text(result: object) -> str:
            content = getattr(result, "content", None)
            self.assertIsInstance(content, list)
            texts = [
                getattr(item, "text", "")
                for item in content
                if isinstance(getattr(item, "text", None), str)
            ]
            self.assertTrue(texts)
            return "\n".join(texts)

        async def _exercise_runtime(target_folder: Path) -> dict[str, object]:
            mux = AzulMCPMultiplexer(
                _DummyPrimaryClient(),
                skill_specs_provider=skill_services.list_enabled_local_mcp_runtime_specs,
            )
            try:
                await mux.reload_skill_clients()
                preview_result = await mux.call_tool(
                    "preview_folder_organization",
                    {"relative_path": "Projects", "recursive": True, "max_depth": 3},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                preview = json.loads(_extract_text(preview_result))
                first_batch_result = await mux.call_tool(
                    "organize_target_folder",
                    {"recursive": True, "plan_token": preview["plan_token"]},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                first_batch = json.loads(_extract_text(first_batch_result))
                listing_after_first = sorted(
                    str(path.relative_to(target_folder)).replace("\\", "/")
                    for path in target_folder.rglob("*")
                    if path.is_file()
                )
                second_batch_result = await mux.call_tool(
                    "organize_target_folder",
                    {"recursive": True, "plan_token": preview["plan_token"]},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                second_batch = json.loads(_extract_text(second_batch_result))
                listing_after_second = sorted(
                    str(path.relative_to(target_folder)).replace("\\", "/")
                    for path in target_folder.rglob("*")
                    if path.is_file()
                )
                return {
                    "preview": preview,
                    "first_batch": first_batch,
                    "second_batch": second_batch,
                    "listing_after_first": listing_after_first,
                    "listing_after_second": listing_after_second,
                }
            finally:
                await mux.cleanup()

        with self._runtime_dir("desktop-organizer-plan-token") as runtime_dir:
            folder = (Path(runtime_dir) / "Desktop").resolve()
            folder.mkdir(parents=True, exist_ok=True)
            projects = folder / "Projects"
            projects.mkdir()
            (projects / "draft.md").write_text("draft", encoding="utf-8")
            inbox = projects / "Inbox"
            inbox.mkdir()
            (inbox / "sketch.png").write_text("sketch", encoding="utf-8")
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.install_skill("dev.azulclaw.desktop-organizer")
                skill_services.configure_skill(
                    "dev.azulclaw.desktop-organizer",
                    {"targetFolder": str(folder), "organizationDepth": "3"},
                )
                skill_services.update_skill_enabled("dev.azulclaw.desktop-organizer", True)

                result = asyncio.run(_exercise_runtime(folder))

        self.assertEqual(result["preview"]["summary"], "2 file(s) ready to organize. Documents: 1, Images: 1.")
        self.assertIn("plan_token", result["preview"])
        self.assertEqual(result["preview"]["next_batch_index"], 1)
        self.assertEqual(result["preview"]["remaining_batch_count"], 2)
        self.assertEqual(result["first_batch"]["summary"], "1 file(s) moved. Documents: 1.")
        self.assertEqual(result["first_batch"]["requested_batch_index"], 1)
        self.assertEqual(result["first_batch"]["next_batch_index"], 2)
        self.assertEqual(result["first_batch"]["remaining_batch_count"], 1)
        self.assertFalse(result["first_batch"]["plan_complete"])
        self.assertEqual(
            result["listing_after_first"],
            ["Projects/Documents/draft.md", "Projects/Inbox/sketch.png"],
        )
        self.assertEqual(result["second_batch"]["summary"], "1 file(s) moved. Images: 1.")
        self.assertEqual(result["second_batch"]["requested_batch_index"], 2)
        self.assertIsNone(result["second_batch"]["next_batch_index"])
        self.assertEqual(result["second_batch"]["remaining_batch_count"], 0)
        self.assertTrue(result["second_batch"]["plan_complete"])
        self.assertTrue(result["second_batch"]["plan_token_released"])
        self.assertEqual(
            result["listing_after_second"],
            ["Projects/Documents/draft.md", "Projects/Images/sketch.png"],
        )

    def test_desktop_organizer_semantic_mode_applies_category_overrides(self) -> None:
        if AzulMCPMultiplexer is None:
            self.skipTest("mcp package is not installed in this test environment.")

        class _DummyPrimaryClient:
            async def cleanup(self) -> None:
                return None

        def _extract_text(result: object) -> str:
            content = getattr(result, "content", None)
            self.assertIsInstance(content, list)
            texts = [
                getattr(item, "text", "")
                for item in content
                if isinstance(getattr(item, "text", None), str)
            ]
            self.assertTrue(texts)
            return "\n".join(texts)

        async def _exercise_runtime(target_folder: Path) -> dict[str, object]:
            mux = AzulMCPMultiplexer(
                _DummyPrimaryClient(),
                skill_specs_provider=skill_services.list_enabled_local_mcp_runtime_specs,
            )
            try:
                await mux.reload_skill_clients()
                preview_result = await mux.call_tool(
                    "preview_folder_organization",
                    {},
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                preview = json.loads(_extract_text(preview_result))
                custom_preview_result = await mux.call_tool(
                    "preview_folder_organization",
                    {
                        "category_overrides": {
                            "invoice.pdf": "Facturas",
                            "notes.txt": "Trabajo",
                            "photo.jpg": "Capturas",
                        }
                    },
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                custom_preview = json.loads(_extract_text(custom_preview_result))
                organize_result = await mux.call_tool(
                    "organize_target_folder",
                    {
                        "category_overrides": {
                            "invoice.pdf": "Facturas",
                            "notes.txt": "Trabajo",
                            "photo.jpg": "Capturas",
                        }
                    },
                    skill_id="dev.azulclaw.desktop-organizer",
                )
                organize = json.loads(_extract_text(organize_result))
                return {
                    "preview": preview,
                    "custom_preview": custom_preview,
                    "organize": organize,
                    "listing": sorted(
                        str(path.relative_to(target_folder)).replace("\\", "/")
                        for path in target_folder.rglob("*")
                        if path.is_file()
                    ),
                }
            finally:
                await mux.cleanup()

        with self._runtime_dir("desktop-organizer-semantic") as runtime_dir:
            folder = (Path(runtime_dir) / "Desktop").resolve()
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "invoice.pdf").write_text("pdf", encoding="utf-8")
            (folder / "photo.jpg").write_text("jpg", encoding="utf-8")
            (folder / "notes.txt").write_text("txt", encoding="utf-8")
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.install_skill("dev.azulclaw.desktop-organizer")
                skill_services.configure_skill(
                    "dev.azulclaw.desktop-organizer",
                    {
                        "targetFolder": str(folder),
                        "semanticCategorization": True,
                    },
                )
                skill_services.update_skill_enabled("dev.azulclaw.desktop-organizer", True)

                result = asyncio.run(_exercise_runtime(folder))

        self.assertEqual(result["preview"]["categorization_mode"], "semantic")
        self.assertEqual(result["preview"]["semantic_override_count"], 0)
        self.assertEqual(result["custom_preview"]["semantic_override_count"], 3)
        self.assertEqual(
            {item["destination_relative_path"] for item in result["custom_preview"]["moves"]},
            {"Capturas/photo.jpg", "Facturas/invoice.pdf", "Trabajo/notes.txt"},
        )
        self.assertEqual(result["organize"]["categorization_mode"], "semantic")
        self.assertEqual(result["organize"]["semantic_override_count"], 3)
        self.assertEqual(
            {item["category_source"] for item in result["organize"]["moves"]},
            {"semantic_override"},
        )
        self.assertEqual(
            result["listing"],
            ["Capturas/photo.jpg", "Facturas/invoice.pdf", "Trabajo/notes.txt"],
        )

    def test_remote_agent_secret_field_is_required_and_redacted(self) -> None:
        with self._runtime_dir("remote-agent-config") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                installed = skill_services.install_skill("dev.azulclaw.gemini")
                self.assertFalse(installed["configured"])
                self.assertEqual(installed["missing_required_fields"], ["endpoint", "GEMINI_API_KEY"])
                self.assertEqual(installed["secrets"][0]["field"], "GEMINI_API_KEY")
                self.assertFalse(installed["secrets"][0]["configured"])

                configured = skill_services.configure_skill(
                    "dev.azulclaw.gemini",
                    {
                        "endpoint": "https://example.com/agent",
                        "GEMINI_API_KEY": "top-secret",
                    },
                )

                self.assertTrue(configured["configured"])
                self.assertEqual(configured["config"]["GEMINI_API_KEY"], skill_services.SECRET_REDACTION)
                self.assertTrue(configured["secrets"][0]["configured"])

    def test_enabled_remote_agent_runtime_specs_resolve_endpoint_and_headers(self) -> None:
        with self._runtime_dir("remote-agent-runtime") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.install_skill("dev.azulclaw.gemini")
                skill_services.configure_skill(
                    "dev.azulclaw.gemini",
                    {
                        "endpoint": "https://example.com/agent",
                        "GEMINI_API_KEY": "secret-key",
                    },
                )
                skill_services.update_skill_enabled("dev.azulclaw.gemini", True)

                specs = skill_services.list_enabled_remote_agent_runtime_specs()

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["skill_id"], "dev.azulclaw.gemini")
        self.assertEqual(specs[0]["endpoint"], "https://example.com/agent")
        self.assertEqual(specs[0]["headers"]["x-api-key"], "secret-key")
        self.assertEqual(specs[0]["status"], "connected")

    def test_enabled_channel_connector_runtime_specs_include_telegram_metadata(self) -> None:
        with self._runtime_dir("channel-connector-runtime") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.install_skill("dev.azulclaw.telegram")
                skill_services.configure_skill(
                    "dev.azulclaw.telegram",
                    {
                        "serviceBusConnectionString": "Endpoint=sb://example/;SharedAccessKey=secret",
                        "serviceBusInboundQueue": "tg-inbound",
                        "serviceBusOutboundQueue": "tg-outbound",
                        "serviceBusUseSessions": "auto",
                        "botSyncReplyTimeoutSeconds": "7.5",
                        "microsoftAppId": "app-id",
                        "microsoftAppPassword": "app-secret",
                        "microsoftAppTenantId": "tenant-id",
                        "allowedUserIds": "1,2",
                        "allowedChatIds": "10",
                    },
                )
                skill_services.update_skill_enabled("dev.azulclaw.telegram", True)

                specs = skill_services.list_enabled_channel_connector_runtime_specs()

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["skill_id"], "dev.azulclaw.telegram")
        self.assertEqual(specs[0]["channels"], ["telegram"])
        self.assertEqual(specs[0]["config"]["allowedUserIds"], "1,2")
        self.assertNotIn("_secret_values", specs[0]["config"])
        self.assertEqual(specs[0]["runtime_env"]["SERVICE_BUS_INBOUND_QUEUE"], "tg-inbound")
        self.assertEqual(specs[0]["runtime_env"]["SERVICE_BUS_OUTBOUND_QUEUE"], "tg-outbound")
        self.assertEqual(specs[0]["runtime_env"]["MicrosoftAppId"], "app-id")
        self.assertEqual(specs[0]["runtime_env"]["MicrosoftAppPassword"], "app-secret")
        self.assertEqual(specs[0]["runtime_env"]["MicrosoftAppTenantId"], "tenant-id")
        self.assertEqual(specs[0]["runtime_env"]["BOT_SYNC_REPLY_TIMEOUT_SECONDS"], "7.5")
        self.assertIn("Azure relay required", specs[0]["message"])
        self.assertIn("2 users, 1 chats", specs[0]["message"])

    def test_runtime_config_prefers_enabled_telegram_skill_allowlists(self) -> None:
        with self._runtime_dir("telegram-runtime-config") as runtime_dir:
            with patch.dict(
                os.environ,
                {
                    "AZUL_RUNTIME_DIR": runtime_dir,
                    "TELEGRAM_ALLOWED_USER_IDS": "legacy-user",
                    "TELEGRAM_ALLOWED_CHAT_IDS": "legacy-chat",
                },
                clear=False,
            ):
                skill_services.install_skill("dev.azulclaw.telegram")
                skill_services.configure_skill(
                    "dev.azulclaw.telegram",
                    {
                        "serviceBusConnectionString": "Endpoint=sb://skill/;SharedAccessKey=secret",
                        "serviceBusInboundQueue": "skill-in",
                        "serviceBusOutboundQueue": "skill-out",
                        "serviceBusUseSessions": "false",
                        "botSyncReplyTimeoutSeconds": "9.25",
                        "microsoftAppId": "skill-app-id",
                        "microsoftAppPassword": "skill-app-secret",
                        "microsoftAppTenantId": "skill-tenant",
                        "allowedUserIds": "skill-user-1, skill-user-2",
                        "allowedChatIds": "skill-chat",
                    },
                )
                skill_services.update_skill_enabled("dev.azulclaw.telegram", True)
                policies = runtime_config._resolve_channel_connector_policies(
                    {
                        "telegram": {
                            "allowed_user_ids": frozenset({"legacy-user"}),
                            "allowed_chat_ids": frozenset({"legacy-chat"}),
                        }
                    }
                )

        self.assertEqual(policies["telegram"]["allowed_user_ids"], frozenset({"skill-user-1", "skill-user-2"}))
        self.assertEqual(policies["telegram"]["allowed_chat_ids"], frozenset({"skill-chat"}))

    def test_runtime_config_prefers_enabled_telegram_skill_transport(self) -> None:
        with self._runtime_dir("telegram-runtime-transport") as runtime_dir:
            with patch.dict(
                os.environ,
                {
                    "AZUL_RUNTIME_DIR": runtime_dir,
                    "SERVICE_BUS_CONNECTION_STRING": "Endpoint=sb://legacy/;",
                    "SERVICE_BUS_INBOUND_QUEUE": "legacy-in",
                    "SERVICE_BUS_OUTBOUND_QUEUE": "legacy-out",
                    "SERVICE_BUS_USE_SESSIONS": "true",
                    "BOT_SYNC_REPLY_TIMEOUT_SECONDS": "3",
                    "MicrosoftAppId": "legacy-app",
                    "MicrosoftAppPassword": "legacy-secret",
                    "MicrosoftAppTenantId": "legacy-tenant",
                },
                clear=False,
            ):
                skill_services.install_skill("dev.azulclaw.telegram")
                skill_services.configure_skill(
                    "dev.azulclaw.telegram",
                    {
                        "serviceBusConnectionString": "Endpoint=sb://skill/;SharedAccessKey=secret",
                        "serviceBusInboundQueue": "skill-in",
                        "serviceBusOutboundQueue": "skill-out",
                        "serviceBusUseSessions": "false",
                        "botSyncReplyTimeoutSeconds": "9.25",
                        "microsoftAppId": "skill-app-id",
                        "microsoftAppPassword": "skill-app-secret",
                        "microsoftAppTenantId": "skill-tenant",
                    },
                )
                skill_services.update_skill_enabled("dev.azulclaw.telegram", True)
                loaded = runtime_config.load_runtime_config(Path("."))

        self.assertEqual(loaded.service_bus_connection_string, "Endpoint=sb://skill/;SharedAccessKey=secret")
        self.assertEqual(loaded.service_bus_inbound_queue, "skill-in")
        self.assertEqual(loaded.service_bus_outbound_queue, "skill-out")
        self.assertEqual(loaded.service_bus_use_sessions, "false")
        self.assertEqual(loaded.bot_sync_reply_timeout_seconds, 9.25)
        self.assertEqual(loaded.app_id, "skill-app-id")
        self.assertEqual(loaded.app_password, "skill-app-secret")
        self.assertEqual(loaded.tenant_id, "skill-tenant")

    def test_channel_connector_access_evaluates_generic_telegram_policy(self) -> None:
        decision = evaluate_channel_connector_access(
            {
                "channelId": "telegram",
                "from": {"id": "blocked-user"},
                "conversation": {"id": "chat-1"},
            },
            {
                "telegram": {
                    "allowed_user_ids": frozenset({"allowed-user"}),
                    "allowed_chat_ids": frozenset({"chat-1"}),
                }
            },
        )

        self.assertFalse(decision.authorized)
        self.assertEqual(decision.channel_id, "telegram")
        self.assertEqual(decision.reason, "telegram user not allowlisted")

    def test_invoke_remote_agent_posts_json_payload(self) -> None:
        class _FakeResponse:
            def __init__(self) -> None:
                self.status = 200
                self.headers = {"Content-Type": "application/json"}

            async def text(self) -> str:
                return json.dumps({"reply": "Gemini says hello"})

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

        class _FakeSession:
            def __init__(self, *args, **kwargs) -> None:
                self.calls = calls

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            def post(self, url, *, json=None, headers=None):
                self.calls.append({"url": url, "json": json, "headers": headers})
                return _FakeResponse()

        calls: list[dict[str, object]] = []
        with self._runtime_dir("remote-agent-invoke") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.install_skill("dev.azulclaw.gemini")
                skill_services.configure_skill(
                    "dev.azulclaw.gemini",
                    {
                        "endpoint": "https://example.com/agent",
                        "GEMINI_API_KEY": "secret-key",
                    },
                )
                skill_services.update_skill_enabled("dev.azulclaw.gemini", True)
                fake_aiohttp = types.SimpleNamespace(
                    ClientSession=_FakeSession,
                    ClientTimeout=lambda total=0: {"total": total},
                )
                with patch.dict(sys.modules, {"aiohttp": fake_aiohttp}):
                    reply = asyncio.run(
                        skill_services.invoke_remote_agent(
                            "dev.azulclaw.gemini",
                            "Summarize this",
                            {"mode": "brief"},
                        )
                    )

        self.assertEqual(reply, "Gemini says hello")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], "https://example.com/agent")
        self.assertEqual(calls[0]["json"], {
            "skill_id": "dev.azulclaw.gemini",
            "skill_name": "Gemini",
            "prompt": "Summarize this",
            "context": {"mode": "brief"},
        })
        self.assertEqual(calls[0]["headers"]["x-api-key"], "secret-key")

    def test_manifest_validation_rejects_incompatible_runtime_kind(self) -> None:
        manifest = {
            "schema_version": "1.0",
            "id": "com.example.invalid",
            "name": "Invalid",
            "version": "0.1.0",
            "publisher": "Example",
            "description": "Invalid skill.",
            "kind": "local_mcp",
            "runtime": {"kind": "none"},
            "compatibility": {"azulclaw_min_version": "0.1.0"},
            "permissions": {},
            "capabilities": [{"id": "invalid", "description": "Invalid."}],
        }

        with self.assertRaises(ValueError) as raised:
            skill_services.validate_skill_manifest(manifest)

        self.assertIn("must use runtime.kind mcp", str(raised.exception))

    def test_manifest_validation_rejects_unknown_required_config_field(self) -> None:
        manifest = {
            "schema_version": "1.0",
            "id": "com.example.invalid",
            "name": "Invalid",
            "version": "0.1.0",
            "publisher": "Example",
            "description": "Invalid skill.",
            "kind": "knowledge",
            "runtime": {"kind": "none"},
            "compatibility": {"azulclaw_min_version": "0.1.0"},
            "config_schema": {
                "type": "object",
                "required": ["missingField"],
                "properties": {},
            },
            "permissions": {},
            "capabilities": [{"id": "invalid", "description": "Invalid."}],
        }

        with self.assertRaises(ValueError) as raised:
            skill_services.validate_skill_manifest(manifest)

        self.assertIn("references undefined field", str(raised.exception))

    def test_manifest_validation_accepts_isolated_skill_workflow(self) -> None:
        manifest = {
            "schema_version": "1.0",
            "id": "com.example.workflow-skill",
            "name": "Workflow Skill",
            "version": "1.0.0",
            "publisher": "Example",
            "description": "Workflow skill.",
            "kind": "local_mcp",
            "runtime": {"kind": "mcp", "command": "python", "args": ["mcp/server.py"]},
            "compatibility": {"azulclaw_min_version": "0.1.0"},
            "permissions": {"process": True, "sensitive_actions": ["move_files"]},
            "capabilities": [{"id": "example.workflow", "description": "Run workflow."}],
            "workflow": {
                "mode": "isolated_process",
                "protocol_version": "1.0",
                "entrypoint": {"command": "python", "args": ["workflow/main.py"]},
                "tools": {"preview": "preview_items", "execute": "execute_items"},
                "tool_policies": {
                    "execute": {
                        "requires_approval": True,
                        "sensitive_action": "move_files",
                    }
                },
                "sensitive_actions": ["move_files"],
                "capability_prompt": "prompts/capability.md",
                "schemas": {"intent": "schemas/intent.schema.json"},
                "checkpoint_policy": "required",
            },
        }

        skill_services.validate_skill_manifest(manifest)

    def test_manifest_validation_rejects_workflow_sensitive_action_not_declared_permission(self) -> None:
        manifest = {
            "schema_version": "1.0",
            "id": "com.example.workflow-skill",
            "name": "Workflow Skill",
            "version": "1.0.0",
            "publisher": "Example",
            "description": "Workflow skill.",
            "kind": "local_mcp",
            "runtime": {"kind": "mcp", "command": "python", "args": ["mcp/server.py"]},
            "compatibility": {"azulclaw_min_version": "0.1.0"},
            "permissions": {"process": True, "sensitive_actions": []},
            "capabilities": [{"id": "example.workflow", "description": "Run workflow."}],
            "workflow": {
                "mode": "isolated_process",
                "protocol_version": "1.0",
                "entrypoint": {"command": "python", "args": ["workflow/main.py"]},
                "sensitive_actions": ["move_files"],
            },
        }

        with self.assertRaises(ValueError) as raised:
            skill_services.validate_skill_manifest(manifest)
        self.assertIn("workflow.sensitive_actions must be declared", str(raised.exception))

    def test_manifest_validation_rejects_workflow_tool_policy_unknown_sensitive_action(self) -> None:
        manifest = {
            "schema_version": "1.0",
            "id": "com.example.workflow-skill",
            "name": "Workflow Skill",
            "version": "1.0.0",
            "publisher": "Example",
            "description": "Workflow skill.",
            "kind": "local_mcp",
            "runtime": {"kind": "mcp", "command": "python", "args": ["mcp/server.py"]},
            "compatibility": {"azulclaw_min_version": "0.1.0"},
            "permissions": {"process": True, "sensitive_actions": ["move_files"]},
            "capabilities": [{"id": "example.workflow", "description": "Run workflow."}],
            "workflow": {
                "mode": "isolated_process",
                "protocol_version": "1.0",
                "entrypoint": {"command": "python", "args": ["workflow/main.py"]},
                "tools": {"execute": "execute_items"},
                "tool_policies": {
                    "execute": {
                        "requires_approval": True,
                        "sensitive_action": "delete_files",
                    }
                },
                "sensitive_actions": ["move_files"],
            },
        }

        with self.assertRaises(ValueError) as raised:
            skill_services.validate_skill_manifest(manifest)
        self.assertIn("sensitive_action must be declared in workflow.sensitive_actions", str(raised.exception))

    def test_manifest_validation_rejects_isolated_workflow_without_process_permission(self) -> None:
        manifest = {
            "schema_version": "1.0",
            "id": "com.example.workflow-skill",
            "name": "Workflow Skill",
            "version": "1.0.0",
            "publisher": "Example",
            "description": "Workflow skill.",
            "kind": "local_mcp",
            "runtime": {"kind": "mcp", "command": "python", "args": ["mcp/server.py"]},
            "compatibility": {"azulclaw_min_version": "0.1.0"},
            "permissions": {"process": False, "sensitive_actions": ["move_files"]},
            "capabilities": [{"id": "example.workflow", "description": "Run workflow."}],
            "workflow": {
                "mode": "isolated_process",
                "protocol_version": "1.0",
                "entrypoint": {"command": "python", "args": ["workflow/main.py"]},
                "sensitive_actions": ["move_files"],
            },
        }

        with self.assertRaises(ValueError) as raised:
            skill_services.validate_skill_manifest(manifest)
        self.assertIn("requires permissions.process=true", str(raised.exception))

    def test_package_skill_excludes_local_settings_and_creates_bundle(self) -> None:
        with self._runtime_dir("package") as runtime_dir:
            output_dir = Path(runtime_dir) / "dist"
            skill_dir = (Path("skills") / "official" / "telegram").resolve()
            files = iter_bundle_files(skill_dir)
            relative_files = {path.relative_to(skill_dir).as_posix() for path in files}
            self.assertIn("azul.skill.json", relative_files)
            self.assertIn("src/relay_function/local.settings.example.json", relative_files)
            self.assertNotIn("src/relay_function/local.settings.json", relative_files)

            packaged = package_skill(skill_dir, output_dir)

            self.assertEqual(packaged.skill_id, "dev.azulclaw.telegram")
            self.assertEqual(packaged.path.suffix, ".azulskill")
            self.assertTrue(packaged.path.exists())
            self.assertEqual(len(packaged.sha256), 64)
            with zipfile.ZipFile(packaged.path) as bundle:
                names = set(bundle.namelist())
            self.assertIn("azul.skill.json", names)
            self.assertIn("src/relay_function/function_app.py", names)

    def test_package_catalog_contains_artifact_metadata(self) -> None:
        with self._runtime_dir("catalog-package") as runtime_dir:
            output_dir = Path(runtime_dir) / "dist"
            packaged = package_skill((Path("skills") / "official" / "gemini").resolve(), output_dir)
            catalog_path = write_catalog([packaged], output_dir)

            payload = json.loads(catalog_path.read_text(encoding="utf-8"))
            skill = payload["skills"][0]
            self.assertEqual(payload["schema_version"], "1.0")
            self.assertEqual(skill["id"], "dev.azulclaw.gemini")
            self.assertTrue(skill["approved"])
            self.assertEqual(skill["artifact"]["filename"], packaged.path.name)
            self.assertEqual(skill["artifact"]["sha256"], packaged.sha256)
            self.assertGreater(skill["artifact"]["size_bytes"], 0)
            self.assertEqual(skill["presentation"]["banner"]["variant"], "gemini")

    def test_resolve_skill_asset_rejects_unsafe_paths(self) -> None:
        with self._runtime_dir("asset-path") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                with self.assertRaises(ValueError):
                    skill_services.resolve_skill_asset_path("dev.azulclaw.gemini", "../azul.skill.json")

    def test_marketplace_settings_persist_registry_url(self) -> None:
        with self._runtime_dir("marketplace-settings") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                saved = skill_services.save_skill_marketplace_settings({
                    "registry_url": "https://example.azurewebsites.net/",
                })
                loaded = skill_services.load_skill_marketplace_settings()

        self.assertEqual(saved["registry_url"], "https://example.azurewebsites.net")
        self.assertEqual(loaded["registry_url"], "https://example.azurewebsites.net")

    def test_marketplace_settings_redacts_consumer_and_admin_keys(self) -> None:
        with self._runtime_dir("marketplace-settings-key") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                saved = skill_services.save_skill_marketplace_settings({
                    "registry_url": "https://example.azurewebsites.net",
                    "registry_auth_mode": "function_key",
                    "registry_consumer_key": "consumer-secret",
                    "registry_admin_key": "admin-secret",
                })
                loaded = skill_services.load_skill_marketplace_settings()
                private = skill_services._load_private_skill_marketplace_settings()

        self.assertEqual(saved["registry_auth_mode"], "function_key")
        self.assertTrue(saved["registry_consumer_key_configured"])
        self.assertTrue(saved["registry_admin_key_configured"])
        self.assertNotIn("registry_consumer_key", saved)
        self.assertNotIn("registry_admin_key", saved)
        self.assertNotIn("registry_consumer_key", loaded)
        self.assertNotIn("registry_admin_key", loaded)
        self.assertEqual(private["registry_consumer_key"], "consumer-secret")
        self.assertEqual(private["registry_admin_key"], "admin-secret")

    def test_marketplace_settings_reject_invalid_registry_url(self) -> None:
        with self._runtime_dir("marketplace-settings-invalid") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                with self.assertRaises(ValueError):
                    skill_services.save_skill_marketplace_settings({"registry_url": "not-a-url"})

    def test_probe_skill_registry_returns_local_only_without_url(self) -> None:
        with self._runtime_dir("registry-probe-local") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                probe = skill_services.probe_skill_registry()

        self.assertEqual(probe["status"], "local_only")
        self.assertFalse(probe["health_ok"])
        self.assertFalse(probe["catalog_ok"])

    def test_probe_skill_registry_uses_function_key_and_reads_catalog(self) -> None:
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(request)
            url = request.full_url if hasattr(request, "full_url") else str(request)
            if url.endswith("/api/health"):
                return _FakeUrlopenResponse("OK")
            if url.endswith("/api/catalog"):
                return _FakeUrlopenResponse(json.dumps({
                    "schema_version": "1.0",
                    "registry": "corp-private",
                    "skills": [{"id": "dev.azulclaw.gemini"}],
                }))
            raise AssertionError(f"Unexpected URL {url}")

        with self._runtime_dir("registry-probe-ok") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.save_skill_marketplace_settings({
                    "registry_url": "https://example.azurewebsites.net",
                    "registry_auth_mode": "function_key",
                    "registry_consumer_key": "secret-key",
                })
                with patch("azul_backend.azul_brain.api.skill_services.urlopen", side_effect=fake_urlopen):
                    probe = skill_services.probe_skill_registry()

        self.assertEqual(probe["status"], "ok")
        self.assertTrue(probe["health_ok"])
        self.assertTrue(probe["catalog_ok"])
        self.assertEqual(probe["registry_name"], "corp-private")
        self.assertEqual(probe["skill_count"], 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(hasattr(call, "header_items") for call in calls))
        first_headers = {key.lower(): value for key, value in calls[0].header_items()}
        self.assertEqual(first_headers["x-functions-key"], "secret-key")

    def test_probe_skill_registry_rejects_missing_function_key(self) -> None:
        with self._runtime_dir("registry-probe-missing-key") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                probe = skill_services.probe_skill_registry({
                    "registry_url": "https://example.azurewebsites.net",
                    "registry_auth_mode": "function_key",
                    "registry_consumer_key": "",
                })

        self.assertEqual(probe["status"], "error")
        self.assertIn("no key is configured", probe["message"])

    def test_registry_admin_overview_uses_admin_key(self) -> None:
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(request)
            url = request.full_url if hasattr(request, "full_url") else str(request)
            self.assertTrue(url.endswith("/api/admin/overview"))
            return _FakeUrlopenResponse(
                json.dumps(
                    {
                        "registry": "corp-private",
                        "totals": {
                            "skills": 2,
                            "versions": 3,
                            "approved_skills": 1,
                            "draft_versions": 1,
                            "revoked_versions": 1,
                        },
                        "items": [],
                    }
                )
            )

        with self._runtime_dir("registry-admin-overview") as runtime_dir:
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.save_skill_marketplace_settings(
                    {
                        "registry_url": "https://example.azurewebsites.net",
                        "registry_auth_mode": "function_key",
                        "registry_consumer_key": "consumer-secret",
                        "registry_admin_key": "admin-secret",
                    }
                )
                with patch("azul_backend.azul_brain.api.skill_services.urlopen", side_effect=fake_urlopen):
                    overview = skill_services.get_registry_admin_overview()

        self.assertEqual(overview["registry"], "corp-private")
        self.assertEqual(overview["totals"]["versions"], 3)
        self.assertEqual(len(calls), 1)
        headers = {key.lower(): value for key, value in calls[0].header_items()}
        self.assertEqual(headers["x-functions-key"], "admin-secret")

    def test_publish_registry_bundle_posts_multipart_with_admin_key(self) -> None:
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(request)
            return _FakeUrlopenResponse(
                json.dumps(
                    {
                        "id": "dev.azulclaw.gemini",
                        "version": "0.1.0",
                        "status": "draft",
                    }
                )
            )

        with self._runtime_dir("registry-admin-publish") as runtime_dir:
            output_dir = Path(runtime_dir) / "dist"
            packaged = package_skill((Path("skills") / "official" / "gemini").resolve(), output_dir)
            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                skill_services.save_skill_marketplace_settings(
                    {
                        "registry_url": "https://example.azurewebsites.net",
                        "registry_auth_mode": "function_key",
                        "registry_consumer_key": "consumer-secret",
                        "registry_admin_key": "admin-secret",
                    }
                )
                with patch("azul_backend.azul_brain.api.skill_services.urlopen", side_effect=fake_urlopen):
                    published = skill_services.publish_registry_bundle(packaged.path)

        self.assertEqual(published["id"], "dev.azulclaw.gemini")
        self.assertEqual(published["status"], "draft")
        self.assertEqual(len(calls), 1)
        headers = {key.lower(): value for key, value in calls[0].header_items()}
        self.assertEqual(headers["x-functions-key"], "admin-secret")
        self.assertIn("multipart/form-data", headers["content-type"])
        self.assertEqual(calls[0].get_method(), "POST")
        payload = calls[0].data.decode("utf-8", errors="replace")
        self.assertIn('name="status"', payload)
        self.assertIn("draft", payload)
        self.assertIn(packaged.path.name, payload)

    def test_install_skill_bundle_extracts_package_and_uses_packaged_manifest(self) -> None:
        with self._runtime_dir("bundle-install") as runtime_dir:
            output_dir = Path(runtime_dir) / "dist"
            packaged = package_skill((Path("skills") / "official" / "gemini").resolve(), output_dir)
            empty_skills_root = Path(runtime_dir) / "empty-skills"
            empty_skills_root.mkdir()

            with patch.dict(
                os.environ,
                {
                    "AZUL_RUNTIME_DIR": runtime_dir,
                    "AZUL_SKILLS_ROOT": str(empty_skills_root),
                },
                clear=False,
            ):
                installed = skill_services.install_skill_bundle(packaged.path, packaged.sha256)
                self.assertEqual(installed["id"], "dev.azulclaw.gemini")
                self.assertEqual(installed["source"]["kind"], "package")
                self.assertTrue(installed["installed"])
                self.assertFalse(installed["configured"])

                extracted_manifest = (
                    Path(runtime_dir)
                    / "skills"
                    / "packages"
                    / "dev.azulclaw.gemini"
                    / "0.1.0"
                    / "azul.skill.json"
                )
                self.assertTrue(extracted_manifest.exists())

                listed = skill_services.list_installed_skills()
                self.assertEqual(listed["items"][0]["id"], "dev.azulclaw.gemini")
                self.assertEqual(listed["items"][0]["source"]["kind"], "package")

    def test_install_skill_bundle_rejects_hash_mismatch(self) -> None:
        with self._runtime_dir("bundle-hash") as runtime_dir:
            output_dir = Path(runtime_dir) / "dist"
            packaged = package_skill((Path("skills") / "official" / "gemini").resolve(), output_dir)

            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                with self.assertRaises(ValueError) as raised:
                    skill_services.install_skill_bundle(packaged.path, "0" * 64)

        self.assertIn("sha256", str(raised.exception))

    def test_install_skill_bundle_rejects_unsafe_zip_paths(self) -> None:
        with self._runtime_dir("bundle-unsafe") as runtime_dir:
            bundle_path = Path(runtime_dir) / "unsafe.azulskill"
            manifest_path = Path("skills") / "official" / "gemini" / "azul.skill.json"
            with zipfile.ZipFile(bundle_path, mode="w") as bundle:
                bundle.write(manifest_path, "azul.skill.json")
                bundle.writestr("../escape.txt", "nope")

            with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": runtime_dir}, clear=False):
                with self.assertRaises(ValueError) as raised:
                    skill_services.install_skill_bundle(bundle_path)

        self.assertIn("unsafe path", str(raised.exception))

    def test_cached_registry_catalog_adds_marketplace_skill(self) -> None:
        with self._runtime_dir("registry-catalog") as runtime_dir:
            output_dir = Path(runtime_dir) / "dist"
            packaged = package_skill((Path("skills") / "official" / "gemini").resolve(), output_dir)
            catalog_path = write_catalog([packaged], output_dir)
            empty_skills_root = Path(runtime_dir) / "empty-skills"
            empty_skills_root.mkdir()

            with patch.dict(
                os.environ,
                {
                    "AZUL_RUNTIME_DIR": runtime_dir,
                    "AZUL_SKILLS_ROOT": str(empty_skills_root),
                },
                clear=False,
            ):
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                skill_services.save_registry_catalog(catalog)
                marketplace = skill_services.list_marketplace_skills()

        self.assertEqual(marketplace["items"][0]["id"], "dev.azulclaw.gemini")
        self.assertEqual(marketplace["items"][0]["source"]["kind"], "registry")
        self.assertTrue(marketplace["items"][0]["capabilities"])
        self.assertIn("endpoint", marketplace["items"][0]["config_schema"]["properties"])

    def test_install_skill_downloads_cached_registry_artifact(self) -> None:
        with self._runtime_dir("registry-install") as runtime_dir:
            output_dir = Path(runtime_dir) / "dist"
            packaged = package_skill((Path("skills") / "official" / "gemini").resolve(), output_dir)
            catalog_path = write_catalog([packaged], output_dir)
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog["skills"][0]["artifact"]["download_url"] = packaged.path.resolve().as_uri()
            empty_skills_root = Path(runtime_dir) / "empty-skills"
            empty_skills_root.mkdir()

            with patch.dict(
                os.environ,
                {
                    "AZUL_RUNTIME_DIR": runtime_dir,
                    "AZUL_SKILLS_ROOT": str(empty_skills_root),
                },
                clear=False,
            ):
                skill_services.save_registry_catalog(catalog)
                installed = skill_services.install_skill("dev.azulclaw.gemini")
                self.assertTrue((Path(runtime_dir) / "skills" / "downloads" / packaged.path.name).exists())

        self.assertEqual(installed["id"], "dev.azulclaw.gemini")
        self.assertEqual(installed["source"]["kind"], "package")


if __name__ == "__main__":
    unittest.main()
