"""MCP contract tests for the Folder Organizer skill.

These tests exercise the skill's own MCP server (``mcp/server.py``) in isolation,
without importing the AzulClaw backend. They live alongside the skill so the bundle
stays self-contained and portable. Brain-side integration tests (routing, semantic
grouping wiring, HITL) live in the repository-level ``tests/`` package instead.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

_SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp" / "server.py"


def _load_server_module() -> ModuleType:
    """Loads the skill's MCP server module fresh, by path, independent of CWD."""
    spec = importlib.util.spec_from_file_location("desktop_organizer_server", _SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopOrganizerMcpServerTests(unittest.TestCase):
    @contextmanager
    def _runtime_dir(self, name: str):
        root = Path(tempfile.mkdtemp(prefix=f"folder-organizer-{name}-"))
        try:
            yield str(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_desktop_organizer_server_builds_and_executes_plan(self) -> None:
        with self._runtime_dir("server") as runtime_dir:
            folder = Path(runtime_dir) / "Desktop"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "invoice.pdf").write_text("pdf", encoding="utf-8")
            (folder / "photo.jpg").write_text("jpg", encoding="utf-8")
            (folder / "notes.txt").write_text("txt", encoding="utf-8")
            keep = folder / "keep"
            keep.mkdir()
            nested = keep / "nested"
            nested.mkdir()
            (nested / "todo.md").write_text("todo", encoding="utf-8")
            design = keep / "design"
            design.mkdir()
            (design / "sketch.png").write_text("sketch", encoding="utf-8")
            dupes = keep / "dupes"
            dupes.mkdir()
            (keep / "photo.jpg").write_text("root-photo", encoding="utf-8")
            (dupes / "photo.jpg").write_text("dupe-photo", encoding="utf-8")

            module = _load_server_module()

            plan = module._build_plan(folder)
            self.assertEqual(len(plan), 3)
            browse = module._list_folder_contents(folder, relative_path="keep", recursive=True, max_depth=2)
            self.assertEqual(browse["relative_path"], "keep")
            self.assertEqual(
                {entry["path"] for entry in browse["entries"]},
                {"keep/design", "keep/design/sketch.png", "keep/dupes", "keep/nested", "keep/photo.jpg", "keep/dupes/photo.jpg", "keep/nested/todo.md"},
            )
            with self.assertRaises(ValueError):
                module._list_folder_contents(folder, relative_path="../outside")
            recursive_plan = module._build_plan(folder, relative_path="keep", recursive=True, max_depth=3)
            self.assertEqual(len(recursive_plan), 4)
            recursive_batches = module._build_plan_batches(recursive_plan)
            self.assertEqual(len(recursive_batches), 4)
            self.assertEqual(
                {batch["source_folder_relative_path"] for batch in recursive_batches},
                {"keep", "keep/design", "keep/dupes", "keep/nested"},
            )
            recursive_body = module._build_plan_tool_body(
                folder=folder,
                relative_path="keep",
                recursive=True,
                max_depth=3,
                plan=recursive_plan,
                batches=recursive_batches,
                include_moves=False,
            )
            self.assertTrue(recursive_body["batched"])
            self.assertEqual(recursive_body["batch_count"], 4)
            self.assertEqual(recursive_body["move_detail_mode"], "summary-first")
            self.assertEqual(recursive_body["moves"], [])
            blocked = [item for item in recursive_plan if item["status"] == "blocked"]
            self.assertEqual(len(blocked), 1)
            self.assertIn("Destination would collide", blocked[0]["reason"])
            moved = module._execute_plan(plan)
            self.assertEqual(len(moved), 3)
            recursive_moved, executed_batches = module._execute_plan_in_batches(recursive_plan)
            self.assertEqual(len(recursive_moved), 4)
            self.assertEqual(len(executed_batches), 4)
            self.assertEqual(sum(batch["moved_count"] for batch in executed_batches), 3)
            self.assertEqual(sum(batch["blocked_count"] for batch in executed_batches), 1)
            self.assertTrue((folder / "Documents" / "invoice.pdf").exists())
            self.assertTrue((folder / "Images" / "photo.jpg").exists())
            self.assertTrue((folder / "Documents" / "notes.txt").exists())
            self.assertTrue((keep / "Documents" / "todo.md").exists())
            self.assertTrue((keep / "Images" / "sketch.png").exists())
            self.assertTrue((keep / "Images" / "photo.jpg").exists())
            self.assertTrue((dupes / "photo.jpg").exists())

    def test_desktop_organizer_reroutes_files_inside_category_folders_with_overrides(self) -> None:
        with self._runtime_dir("reroute") as runtime_dir:
            folder = Path(runtime_dir) / "Desktop"
            folder.mkdir(parents=True, exist_ok=True)
            images = folder / "Images"
            images.mkdir()
            (images / "banner.png").write_text("png", encoding="utf-8")
            docs = folder / "Documents"
            docs.mkdir()
            (docs / "report.pdf").write_text("pdf", encoding="utf-8")

            module = _load_server_module()

            # Without overrides, files already inside their category folders are left untouched.
            default_plan = module._build_plan(folder, recursive=True, max_depth=3)
            self.assertEqual(default_plan, [])

            # With an override, a file already inside a category folder is re-routed semantically.
            plan = module._build_plan(
                folder,
                recursive=True,
                max_depth=3,
                category_overrides={"Images/banner.png": "Eventos"},
            )
            by_source = {item["source_relative_path"]: item for item in plan}
            self.assertIn("Images/banner.png", by_source)
            self.assertEqual(by_source["Images/banner.png"]["destination_relative_path"], "Eventos/banner.png")
            self.assertEqual(by_source["Images/banner.png"]["status"], "planned")
            self.assertEqual(by_source["Images/banner.png"]["category_source"], "semantic_override")
            # A file with no override that is already correctly placed stays put.
            self.assertNotIn("Documents/report.pdf", by_source)

            module._execute_plan(plan)
            self.assertTrue((folder / "Eventos" / "banner.png").exists())
            self.assertFalse((images / "banner.png").exists())

    def test_desktop_organizer_recursive_preview_supports_batch_detail(self) -> None:
        with self._runtime_dir("batch-detail") as runtime_dir:
            folder = Path(runtime_dir) / "Desktop"
            folder.mkdir(parents=True, exist_ok=True)
            keep = folder / "keep"
            keep.mkdir()
            (keep / "photo.jpg").write_text("root-photo", encoding="utf-8")
            nested = keep / "nested"
            nested.mkdir()
            (nested / "todo.md").write_text("todo", encoding="utf-8")

            module = _load_server_module()

            recursive_plan = module._build_plan(folder, relative_path="keep", recursive=True, max_depth=3)
            recursive_batches = module._build_plan_batches(recursive_plan)
            batch_body = module._build_plan_tool_body(
                folder=folder,
                relative_path="keep",
                recursive=True,
                max_depth=3,
                plan=recursive_plan,
                batches=recursive_batches,
                include_moves=False,
                batch_index=2,
            )

            self.assertEqual(batch_body["move_detail_mode"], "selected-batch")
            self.assertEqual(batch_body["selected_batch"]["batch_index"], 2)
            self.assertEqual(batch_body["selected_batch"]["source_folder_relative_path"], "keep/nested")
            self.assertEqual(len(batch_body["moves"]), 1)
            self.assertEqual(batch_body["moves"][0]["source_relative_path"], "keep/nested/todo.md")

    def test_desktop_organizer_recursive_execute_supports_batch_selection(self) -> None:
        with self._runtime_dir("execute-batch") as runtime_dir:
            folder = Path(runtime_dir) / "Desktop"
            folder.mkdir(parents=True, exist_ok=True)
            keep = folder / "keep"
            keep.mkdir()
            (keep / "photo.jpg").write_text("root-photo", encoding="utf-8")
            nested = keep / "nested"
            nested.mkdir()
            (nested / "todo.md").write_text("todo", encoding="utf-8")

            module = _load_server_module()

            recursive_plan = module._build_plan(folder, relative_path="keep", recursive=True, max_depth=3)
            selected_plan, selected_batch = module._select_plan_items(recursive_plan, batch_index=2)

            self.assertIsNotNone(selected_batch)
            self.assertEqual(selected_batch["source_folder_relative_path"], "keep/nested")
            moved = module._execute_plan(selected_plan)
            body = module._build_plan_tool_body(
                folder=folder,
                relative_path="keep",
                recursive=True,
                max_depth=3,
                plan=moved,
                dry_run=False,
                batches=[
                    module._summarize_plan_batch(
                        selected_batch["source_folder_relative_path"],
                        moved,
                        batch_index=selected_batch["batch_index"],
                    )
                ],
                include_moves=False,
            )
            body["batch_execution_scope"] = "selected-batch"
            body["requested_batch_index"] = selected_batch["batch_index"]
            body["selected_batch"] = module._summarize_plan_batch(
                selected_batch["source_folder_relative_path"],
                moved,
                batch_index=selected_batch["batch_index"],
            )
            body["move_detail_mode"] = "selected-batch"

            self.assertEqual(body["batch_execution_scope"], "selected-batch")
            self.assertEqual(body["requested_batch_index"], 2)
            self.assertEqual(body["selected_batch"]["moved_count"], 1)
            self.assertEqual(body["move_detail_mode"], "selected-batch")
            self.assertEqual(body["moves"][0]["source_relative_path"], "keep/nested/todo.md")
            self.assertTrue((keep / "Documents" / "todo.md").exists())
            self.assertTrue((keep / "photo.jpg").exists())
            self.assertFalse((keep / "Images" / "photo.jpg").exists())

    def test_desktop_organizer_summary_lists_blocked_files(self) -> None:
        with self._runtime_dir("blocked-summary") as runtime_dir:
            folder = Path(runtime_dir) / "Desktop"
            folder.mkdir(parents=True, exist_ok=True)
            keep = folder / "keep"
            keep.mkdir()
            (keep / "sketch.png").write_text("sketch", encoding="utf-8")
            dupes = keep / "dupes"
            dupes.mkdir()
            (dupes / "sketch.png").write_text("dupe", encoding="utf-8")

            module = _load_server_module()

            recursive_plan = module._build_plan(folder, relative_path="keep", recursive=True, max_depth=3)
            summary = module._plan_summary(recursive_plan)
            batch_summary = module._summarize_plan_batch("keep/dupes", [recursive_plan[1]], batch_index=2)

            self.assertIn("1 blocked by conflicts", summary)
            self.assertIn("Blocked files: keep/dupes/sketch.png.", summary)
            self.assertEqual(batch_summary["blocked_count"], 1)
            self.assertEqual(len(batch_summary["blocked_items"]), 1)
            self.assertEqual(batch_summary["blocked_items"][0]["source_relative_path"], "keep/dupes/sketch.png")

    def test_desktop_organizer_execute_plan_continues_after_filesystem_error(self) -> None:
        with self._runtime_dir("rename-error") as runtime_dir:
            folder = Path(runtime_dir) / "Desktop"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "invoice.pdf").write_text("pdf", encoding="utf-8")
            (folder / "photo.jpg").write_text("jpg", encoding="utf-8")

            module = _load_server_module()

            plan = module._build_plan(folder)
            original_rename = Path.rename

            def flaky_rename(path_self: Path, destination: Path) -> None:
                if path_self.name == "invoice.pdf":
                    raise OSError("sharing violation")
                original_rename(path_self, destination)

            with patch.object(Path, "rename", autospec=True, side_effect=flaky_rename):
                moved = module._execute_plan(plan)

            invoice_entry = next(item for item in moved if item["source_relative_path"] == "invoice.pdf")
            photo_entry = next(item for item in moved if item["source_relative_path"] == "photo.jpg")

            self.assertEqual(invoice_entry["status"], "blocked")
            self.assertIn("Move failed: sharing violation", invoice_entry["reason"])
            self.assertEqual(photo_entry["status"], "moved")
            self.assertTrue((folder / "Images" / "photo.jpg").exists())
            self.assertTrue((folder / "invoice.pdf").exists())
            self.assertIn("Blocked files: invoice.pdf.", module._plan_summary(moved))

    def test_desktop_organizer_server_truncates_large_tool_payloads(self) -> None:
        module = _load_server_module()

        body = {
            "summary": "Large plan.",
            "moves": [
                {
                    "source": f"C:/demo/source-{index}.txt",
                    "destination": f"C:/demo/Documents/source-{index}.txt",
                    "source_relative_path": f"source-{index}.txt",
                    "destination_relative_path": f"Documents/source-{index}.txt",
                    "category": "Documents",
                    "status": "planned",
                }
                for index in range(400)
            ],
        }

        raw = module._serialize_tool_payload(body, array_key="moves", item_label="move")
        payload = json.loads(raw)

        self.assertLessEqual(len(raw), module.MAX_TOOL_RESULT_CHARS)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["total_move_count"], 400)
        self.assertLess(payload["displayed_move_count"], 400)
        self.assertEqual(len(payload["moves"]), payload["displayed_move_count"])


if __name__ == "__main__":
    unittest.main()
