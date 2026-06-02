from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts.package_skills import package_skill


def _load_registry_store_module():
    module_path = Path("azure") / "marketplace" / "registry_api" / "registry_store.py"
    spec = importlib.util.spec_from_file_location("skill_registry_store", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry_store = _load_registry_store_module()


class SkillRegistryStoreTests(unittest.TestCase):
    @contextmanager
    def _registry_dir(self, name: str):
        root = Path("memory") / "test-skill-registry" / name
        shutil.rmtree(root, ignore_errors=True)
        artifacts = root / "artifacts"
        metadata = root / "registry_state.json"
        artifacts.mkdir(parents=True, exist_ok=True)
        env = {
            "AZUL_SKILL_ARTIFACT_DIR": str(artifacts.resolve()),
            "AZUL_SKILL_REGISTRY_METADATA_PATH": str(metadata.resolve()),
            "AZUL_SKILL_REGISTRY_NAME": "corp-private",
        }
        try:
            with patch.dict(os.environ, env, clear=False):
                yield root
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_publish_and_approve_bundle_updates_public_catalog(self) -> None:
        with self._registry_dir("publish-approve") as root:
            output_dir = root / "dist"
            packaged = package_skill((Path("skills") / "official" / "gemini").resolve(), output_dir)

            published = registry_store.publish_bundle_from_path(str(packaged.path))
            self.assertEqual(published["status"], "draft")
            self.assertEqual(published["id"], "dev.azulclaw.gemini")

            catalog_before = registry_store.build_public_catalog()
            self.assertEqual(catalog_before["skills"], [])

            approved = registry_store.set_skill_version_approval("dev.azulclaw.gemini", "0.1.0", True)
            self.assertEqual(approved["status"], "approved")

            catalog_after = registry_store.build_public_catalog()
            self.assertEqual(len(catalog_after["skills"]), 1)
            self.assertEqual(catalog_after["skills"][0]["id"], "dev.azulclaw.gemini")
            self.assertEqual(catalog_after["skills"][0]["status"], "approved")
            self.assertEqual(catalog_after["skills"][0]["artifact"]["filename"], packaged.path.name)
            self.assertTrue((root / "artifacts" / packaged.path.name).exists())

    def test_list_versions_returns_unapproved_entries(self) -> None:
        with self._registry_dir("versions") as root:
            output_dir = root / "dist"
            packaged = package_skill((Path("skills") / "official" / "telegram").resolve(), output_dir)
            registry_store.publish_bundle_from_path(str(packaged.path))

            skills = registry_store.list_registry_skills()
            self.assertEqual(skills["items"][0]["id"], "dev.azulclaw.telegram")
            self.assertEqual(skills["items"][0]["latest_version"], "0.1.0")
            self.assertEqual(skills["items"][0]["approved_version"], "")

            versions = registry_store.list_skill_versions("dev.azulclaw.telegram")
            self.assertEqual(versions["skill"]["id"], "dev.azulclaw.telegram")
            self.assertEqual(versions["versions"][0]["version"], "0.1.0")
            self.assertEqual(versions["versions"][0]["status"], "draft")

    def test_publish_bundle_from_base64_persists_state(self) -> None:
        with self._registry_dir("base64") as root:
            output_dir = root / "dist"
            packaged = package_skill((Path("skills") / "official" / "desktop-organizer").resolve(), output_dir)
            payload = packaged.path.read_bytes()

            published = registry_store.publish_bundle_from_base64(
                base64.b64encode(payload).decode("utf-8"),
                filename=packaged.path.name,
            )

            self.assertEqual(published["id"], "dev.azulclaw.desktop-organizer")
            state = json.loads((root / "registry_state.json").read_text(encoding="utf-8"))
            self.assertIn("dev.azulclaw.desktop-organizer", state["skills"])

    def test_approve_new_version_revokes_previous_approved_version(self) -> None:
        with self._registry_dir("approve-switch") as root:
            output_dir = root / "dist"
            source = (Path("skills") / "official" / "gemini").resolve()
            bundle_v1 = package_skill(source, output_dir)

            manifest = json.loads((source / "azul.skill.json").read_text(encoding="utf-8"))
            manifest["version"] = "0.2.0"
            temp_skill = root / "temp-gemini"
            shutil.copytree(source, temp_skill)
            (temp_skill / "azul.skill.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            bundle_v2 = package_skill(temp_skill, output_dir)

            registry_store.publish_bundle_from_path(str(bundle_v1.path))
            registry_store.set_skill_version_status("dev.azulclaw.gemini", "0.1.0", "approved")
            registry_store.publish_bundle_from_path(str(bundle_v2.path))
            registry_store.set_skill_version_status("dev.azulclaw.gemini", "0.2.0", "approved")

            versions = registry_store.list_skill_versions("dev.azulclaw.gemini")
            status_by_version = {item["version"]: item["status"] for item in versions["versions"]}
            self.assertEqual(status_by_version["0.2.0"], "approved")
            self.assertEqual(status_by_version["0.1.0"], "revoked")

    def test_admin_overview_counts_drafts_approved_and_revoked(self) -> None:
        with self._registry_dir("overview") as root:
            output_dir = root / "dist"
            packaged = package_skill((Path("skills") / "official" / "telegram").resolve(), output_dir)
            registry_store.publish_bundle_from_path(str(packaged.path))
            registry_store.set_skill_version_status("dev.azulclaw.telegram", "0.1.0", "approved")
            registry_store.set_skill_version_status("dev.azulclaw.telegram", "0.1.0", "revoked")

            overview = registry_store.build_admin_overview()

        self.assertEqual(overview["totals"]["skills"], 1)
        self.assertEqual(overview["totals"]["versions"], 1)
        self.assertEqual(overview["totals"]["approved_skills"], 0)
        self.assertEqual(overview["totals"]["draft_versions"], 0)
        self.assertEqual(overview["totals"]["revoked_versions"], 1)


if __name__ == "__main__":
    unittest.main()
