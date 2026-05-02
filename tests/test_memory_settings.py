from __future__ import annotations

import os
import shutil
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

from azul_backend.azul_brain.api.services import save_memory_runtime_settings
from azul_backend.azul_brain.api.hatching_store import (
    HatchingProfile,
    HatchingStore,
    MemorySettings,
    is_vector_memory_enabled,
    load_memory_settings,
    resolve_memory_db_path,
    save_memory_settings,
)


class MemorySettingsTests(unittest.TestCase):
    def _runtime_dir(self) -> str:
        root = Path(__file__).resolve().parents[1] / "memory" / "test-runtime"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"case-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return str(path)

    def test_defaults_to_workspace_azul_database(self) -> None:
        tmp = self._runtime_dir()
        workspace = Path(tmp) / "workspace"
        with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": tmp}, clear=True):
            HatchingStore().save(HatchingProfile(workspace_root=str(workspace)))

            self.assertEqual(
                resolve_memory_db_path(),
                str(workspace / ".azul" / "azul_memory.db"),
            )
            self.assertTrue(is_vector_memory_enabled())

    def test_saved_memory_settings_override_legacy_env(self) -> None:
        tmp = self._runtime_dir()
        override = Path(tmp) / "custom" / "memory.sqlite"
        with patch.dict(
            os.environ,
            {
                "AZUL_RUNTIME_DIR": tmp,
                "AZUL_MEMORY_DB_PATH": "legacy.sqlite",
                "VECTOR_MEMORY_ENABLED": "true",
            },
            clear=True,
        ):
            save_memory_settings(
                MemorySettings(
                    memory_db_path=str(override),
                    vector_memory_enabled=False,
                )
            )

            self.assertEqual(resolve_memory_db_path(), str(override))
            self.assertFalse(is_vector_memory_enabled())

    def test_memory_db_path_expands_home_shortcut(self) -> None:
        tmp = self._runtime_dir()
        home = Path(tmp) / "home"
        with patch.dict(
            os.environ,
            {
                "AZUL_RUNTIME_DIR": tmp,
                "HOME": str(home),
                "USERPROFILE": str(home),
            },
            clear=True,
        ):
            saved = save_memory_settings(
                MemorySettings(
                    memory_db_path="~/azul/memory.sqlite",
                    vector_memory_enabled=True,
                )
            )
            expected = str(home / "azul" / "memory.sqlite")

            self.assertEqual(saved.memory_db_path, expected)
            self.assertEqual(load_memory_settings().memory_db_path, expected)
            self.assertEqual(resolve_memory_db_path(), expected)

    def test_save_memory_settings_parses_string_false(self) -> None:
        tmp = self._runtime_dir()
        with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": tmp}, clear=True):
            HatchingStore().save(HatchingProfile(workspace_root=str(Path(tmp) / "workspace")))
            saved = save_memory_runtime_settings({"vector_memory_enabled": "false"})

            self.assertFalse(saved["vector_memory_enabled"])
            self.assertFalse(is_vector_memory_enabled())

    def test_save_memory_settings_rejects_invalid_boolean(self) -> None:
        tmp = self._runtime_dir()
        with patch.dict(os.environ, {"AZUL_RUNTIME_DIR": tmp}, clear=True):
            with self.assertRaises(ValueError):
                save_memory_runtime_settings({"vector_memory_enabled": "definitely"})


if __name__ == "__main__":
    unittest.main()
