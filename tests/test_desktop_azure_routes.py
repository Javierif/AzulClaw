from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from azul_backend.azul_brain.api import routes
from azul_backend.azul_brain.config import RuntimeConfig


class _FakeAuthSnapshot:
    mode = "entra"
    startup_enabled = True
    status = "authenticated"
    detail = "ok"
    last_error = ""
    last_success_at = "2026-05-02T00:00:00Z"
    source = "frontend"
    requires_frontend_login = False


class _FakeAuthState:
    async def ensure_authenticated(self) -> _FakeAuthSnapshot:
        return _FakeAuthSnapshot()


class DesktopAzureRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_clears_optional_environment_values(self) -> None:
        payload = {
            "access_token": "token",
            "expires_on": int(time.time()) + 3600,
            "endpoint": "https://example.openai.azure.com",
            "deployment": "gpt-main",
            "key_vault_url": "",
            "fast_deployment": "",
            "embedding_deployment": "",
        }
        request = make_mocked_request("POST", "/api/desktop/azure/connect")
        request._read_bytes = json.dumps(payload).encode("utf-8")
        request.app["azure_auth_state"] = _FakeAuthState()

        with (
            patch.dict(
                os.environ,
                {
                    "AZUL_KEY_VAULT_URL": "https://old.vault.azure.net",
                    "AZURE_OPENAI_FAST_DEPLOYMENT": "old-fast",
                    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "old-embedding",
                },
                clear=True,
            ),
            patch("azul_backend.azul_brain.api.routes.set_frontend_azure_token"),
        ):
            response = await routes.desktop_azure_connect_handler(request)

            self.assertEqual(response.status, 200)
            self.assertNotIn("AZUL_KEY_VAULT_URL", os.environ)
            self.assertNotIn("AZURE_OPENAI_FAST_DEPLOYMENT", os.environ)
            self.assertNotIn("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", os.environ)

    async def test_key_vault_hydrate_preserves_missing_optional_bot_secrets(self) -> None:
        payload = {
            "access_token": "token",
            "expires_on": int(time.time()) + 3600,
            "key_vault_url": "https://example.vault.azure.net",
        }
        request = make_mocked_request("POST", "/api/desktop/azure/key-vault/hydrate")
        request._read_bytes = json.dumps(payload).encode("utf-8")
        request.app["runtime_config"] = RuntimeConfig(
            app_id="runtime-app-id",
            app_password="runtime-password",
            tenant_id="runtime-tenant",
            port=3978,
        )
        request.app["servicebus_worker"] = None

        async def fake_get_secret(**kwargs):
            return "app-id" if kwargs["secret_name"] == "MicrosoftAppId" else ""

        with (
            patch.dict(
                os.environ,
                {
                    "MicrosoftAppPassword": "old-password",
                    "MicrosoftAppTenantId": "old-tenant",
                },
                clear=True,
            ),
            patch("azul_backend.azul_brain.api.routes._key_vault_get_secret", new=AsyncMock(side_effect=fake_get_secret)),
            patch("azul_backend.azul_brain.api.routes.build_adapter", return_value=object()) as build_adapter,
        ):
            response = await routes.desktop_azure_key_vault_hydrate_handler(request)
            body = json.loads(response.text)

            self.assertEqual(os.environ["MicrosoftAppPassword"], "old-password")
            self.assertEqual(os.environ["MicrosoftAppTenantId"], "old-tenant")
            build_adapter.assert_called_once_with("app-id", "old-password", "old-tenant")

        self.assertEqual(response.status, 200)
        self.assertEqual(body["hydrated"], ["MicrosoftAppId"])
        self.assertEqual(body["missing"], ["MicrosoftAppPassword", "MicrosoftAppTenantId"])


if __name__ == "__main__":
    unittest.main()
