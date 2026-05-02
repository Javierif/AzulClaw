from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from azul_backend.azul_brain import azure_auth
from azul_backend.azul_brain.runtime.agent_runtime import AgentRuntimeManager
from azul_backend.azul_brain.runtime.store import RuntimeModelProfile


def _clear_auth_caches() -> None:
    azure_auth.get_default_azure_credential.cache_clear()
    azure_auth.get_azure_openai_token_provider.cache_clear()


class AzureAuthTests(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_auth_caches()

    def test_auto_mode_prefers_entra_when_no_api_key_exists(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AZUL_AZURE_OPENAI_AUTH_MODE": "auto",
                "AZURE_OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            self.assertEqual(azure_auth.resolve_azure_openai_auth_mode(""), "entra")

    def test_auto_mode_prefers_api_key_when_key_exists(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AZUL_AZURE_OPENAI_AUTH_MODE": "auto",
                "AZURE_OPENAI_API_KEY": "secret",
            },
            clear=False,
        ):
            self.assertEqual(azure_auth.resolve_azure_openai_auth_mode("secret"), "api_key")

    def test_describe_entra_auth_requires_login_without_backend_auth_path(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AZUL_AZURE_OPENAI_AUTH_MODE": "entra",
                "AZURE_OPENAI_API_KEY": "",
                "AZUL_ENABLE_STARTUP_DEFAULT_AZURE_CREDENTIAL": "false",
                "AZUL_ENABLE_INTERACTIVE_BROWSER_AUTH": "false",
            },
            clear=False,
        ):
            available, detail = azure_auth.describe_azure_openai_auth(
                endpoint="https://example.openai.azure.com/",
                deployment="gpt-4o",
                explicit_api_key="",
            )

        self.assertFalse(available)
        self.assertIn("login", detail.lower())

    def test_describe_entra_auth_accepts_startup_default_credential(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AZUL_AZURE_OPENAI_AUTH_MODE": "entra",
                "AZURE_OPENAI_API_KEY": "",
                "AZUL_ENABLE_STARTUP_DEFAULT_AZURE_CREDENTIAL": "true",
            },
            clear=False,
        ):
            available, detail = azure_auth.describe_azure_openai_auth(
                endpoint="https://example.openai.azure.com/",
                deployment="gpt-4o",
                explicit_api_key="",
            )

        self.assertTrue(available)
        self.assertIn("default credential", detail)

    def test_describe_api_key_mode_requires_a_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AZUL_AZURE_OPENAI_AUTH_MODE": "api_key",
                "AZURE_OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            available, detail = azure_auth.describe_azure_openai_auth(
                endpoint="https://example.openai.azure.com/",
                deployment="gpt-4o",
                explicit_api_key="",
            )

        self.assertFalse(available)
        self.assertIn("no key", detail.lower())


class AzureAuthStateTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        _clear_auth_caches()

    async def test_state_reports_disabled_in_api_key_mode(self) -> None:
        state = azure_auth.AzureOpenAIAuthState()
        with patch.dict(
            os.environ,
            {"AZUL_AZURE_OPENAI_AUTH_MODE": "api_key", "AZURE_OPENAI_API_KEY": "secret"},
            clear=False,
        ):
            snapshot = await state.ensure_authenticated()

        self.assertEqual(snapshot.status, "disabled")
        self.assertEqual(snapshot.mode, "api_key")
        self.assertFalse(snapshot.requires_frontend_login)

    async def test_state_reports_authenticated_after_token_acquisition(self) -> None:
        state = azure_auth.AzureOpenAIAuthState()
        with (
            patch.dict(
                os.environ,
                {
                    "AZUL_AZURE_OPENAI_AUTH_MODE": "entra",
                    "AZURE_OPENAI_API_KEY": "",
                    "AZUL_ENABLE_STARTUP_DEFAULT_AZURE_CREDENTIAL": "true",
                },
                clear=False,
            ),
            patch("azul_backend.azul_brain.azure_auth.acquire_azure_openai_token", return_value="token"),
        ):
            snapshot = await state.ensure_authenticated()

        self.assertEqual(snapshot.status, "authenticated")
        self.assertEqual(snapshot.mode, "entra")
        self.assertTrue(snapshot.last_success_at)
        self.assertFalse(snapshot.requires_frontend_login)

    async def test_state_requires_frontend_login_when_no_backend_auth_path_exists(self) -> None:
        state = azure_auth.AzureOpenAIAuthState()
        with patch.dict(
            os.environ,
            {
                "AZUL_AZURE_OPENAI_AUTH_MODE": "entra",
                "AZURE_OPENAI_API_KEY": "",
                "AZUL_ENABLE_STARTUP_DEFAULT_AZURE_CREDENTIAL": "false",
                "AZUL_ENABLE_INTERACTIVE_BROWSER_AUTH": "false",
            },
            clear=False,
        ):
            snapshot = state.snapshot()

        self.assertEqual(snapshot.status, "idle")
        self.assertTrue(snapshot.requires_frontend_login)

    async def test_state_reports_failure_when_token_acquisition_fails(self) -> None:
        state = azure_auth.AzureOpenAIAuthState()
        with (
            patch.dict(
                os.environ,
                {
                    "AZUL_AZURE_OPENAI_AUTH_MODE": "entra",
                    "AZURE_OPENAI_API_KEY": "",
                    "AZUL_ENABLE_STARTUP_DEFAULT_AZURE_CREDENTIAL": "true",
                },
                clear=False,
            ),
            patch("azul_backend.azul_brain.azure_auth.acquire_azure_openai_token", side_effect=RuntimeError("login failed")),
        ):
            snapshot = await state.ensure_authenticated()

        self.assertEqual(snapshot.status, "failed")
        self.assertIn("login failed", snapshot.last_error)


class AgentRuntimeAzureProbeTests(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_auth_caches()

    def test_probe_azure_model_accepts_entra_configuration(self) -> None:
        manager = AgentRuntimeManager.__new__(AgentRuntimeManager)
        model = RuntimeModelProfile(
            id="fast",
            label="Fast brain",
            lane="fast",
            provider="azure",
            deployment="gpt-4o-mini",
        )

        with patch.dict(
            os.environ,
            {
                "AZUL_AZURE_OPENAI_AUTH_MODE": "entra",
                "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
                "AZURE_OPENAI_API_KEY": "",
                "AZUL_ENABLE_STARTUP_DEFAULT_AZURE_CREDENTIAL": "true",
            },
            clear=False,
        ):
            result = AgentRuntimeManager._probe_azure_model(manager, model)

        self.assertTrue(result["available"])
        self.assertIn("Entra ID", str(result["detail"]))


if __name__ == "__main__":
    unittest.main()
