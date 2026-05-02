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
from azul_backend.azul_brain.runtime.store import RuntimeModelProfile, RuntimeSettings


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


class _FakeRuntimeManager:
    def __init__(self) -> None:
        self.settings = RuntimeSettings(
            models=[
                RuntimeModelProfile(
                    id="fast",
                    label="Fast brain",
                    lane="fast",
                    provider="azure",
                    deployment="old-fast",
                    enabled=False,
                    streaming_enabled=True,
                ),
                RuntimeModelProfile(
                    id="slow",
                    label="Slow brain",
                    lane="slow",
                    provider="azure",
                    deployment="old-slow",
                    enabled=True,
                    streaming_enabled=False,
                ),
            ]
        )
        self.saved_payloads: list[dict] = []
        self.agent_cache = {"old-agent": object()}
        self.probe_cache = {"old-probe": object()}
        self.cooldowns = {"fast": 1.0}
        self.last_errors = {"slow": "old error"}

    def load_settings(self) -> RuntimeSettings:
        return self.settings

    def list_model_status(self) -> list[dict]:
        return [{"id": model.id, "enabled": model.enabled} for model in self.settings.models]

    def save_settings(self, payload: dict) -> RuntimeSettings:
        self.saved_payloads.append(payload)
        by_id = {model.id: model for model in self.settings.models}
        for item in payload.get("models", []):
            model = by_id[item["id"]]
            by_id[item["id"]] = RuntimeModelProfile(
                id=model.id,
                label=model.label,
                lane=model.lane,
                provider=model.provider,
                deployment=item["deployment"],
                enabled=model.enabled,
                streaming_enabled=model.streaming_enabled,
                description=model.description,
            )
        self.settings.models = list(by_id.values())
        return self.settings


class _FakeJsonResponse:
    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, content_type=None):
        return self._body


class _FakeSession:
    def __init__(self, pages: dict[str, dict]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str, **_kwargs):
        self.requested.append(url)
        return _FakeJsonResponse(200, self.pages[url])


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
        runtime_manager = _FakeRuntimeManager()
        app = web.Application()
        app["azure_auth_state"] = _FakeAuthState()
        app["runtime_manager"] = runtime_manager
        request = make_mocked_request("POST", "/api/desktop/azure/connect", app=app)
        request._read_bytes = json.dumps(payload).encode("utf-8")

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
            self.assertEqual(runtime_manager.agent_cache, {})
            self.assertEqual(runtime_manager.probe_cache, {})
            self.assertEqual(runtime_manager.cooldowns, {})
            self.assertEqual(runtime_manager.last_errors, {})

    async def test_connect_rejects_non_azure_openai_endpoint(self) -> None:
        payload = {
            "access_token": "token",
            "expires_on": int(time.time()) + 3600,
            "endpoint": "https://example.com",
            "deployment": "gpt-main",
        }
        app = web.Application()
        app["azure_auth_state"] = _FakeAuthState()
        app["runtime_manager"] = _FakeRuntimeManager()
        request = make_mocked_request("POST", "/api/desktop/azure/connect", app=app)
        request._read_bytes = json.dumps(payload).encode("utf-8")

        response = await routes.desktop_azure_connect_handler(request)
        body = json.loads(response.text)

        self.assertEqual(response.status, 400)
        self.assertIn("Azure OpenAI endpoint", body["error"])

    async def test_arm_get_json_follows_next_link_pages(self) -> None:
        first_url = "https://management.azure.com/subscriptions?api-version=2022-12-01"
        second_url = "https://management.azure.com/subscriptions?page=2"
        session = _FakeSession(
            {
                first_url: {"value": [{"id": "one"}], "nextLink": second_url},
                second_url: {"value": [{"id": "two"}]},
            }
        )

        result = await routes._get_paginated_json(
            session=session,
            url=first_url,
            headers={},
            allowed_next_link_hosts={"management.azure.com"},
            error_prefix="Azure request failed",
        )

        self.assertEqual(result["value"], [{"id": "one"}, {"id": "two"}])
        self.assertEqual(session.requested, [first_url, second_url])

    async def test_paginated_json_rejects_untrusted_next_link_host(self) -> None:
        first_url = "https://management.azure.com/subscriptions?api-version=2022-12-01"
        session = _FakeSession(
            {
                first_url: {
                    "value": [{"id": "one"}],
                    "nextLink": "https://example.com/subscriptions?page=2",
                },
            }
        )

        with self.assertRaises(web.HTTPBadRequest):
            await routes._get_paginated_json(
                session=session,
                url=first_url,
                headers={},
                allowed_next_link_hosts={"management.azure.com"},
                error_prefix="Azure request failed",
            )

    async def test_backend_status_reports_default_runtime_dir(self) -> None:
        app = web.Application()
        app["runtime_manager"] = _FakeRuntimeManager()
        app["scheduler"] = type("Scheduler", (), {"get_status": lambda self: {"scheduler_running": False}})()
        app["azure_auth_state"] = type("AuthState", (), {"snapshot": lambda self: _FakeAuthSnapshot()})()
        request = make_mocked_request("GET", "/api/desktop/backend/status", app=app)

        with patch.dict(os.environ, {}, clear=True):
            response = await routes.desktop_backend_status_handler(request)
            body = json.loads(response.text)

        self.assertTrue(body["runtime_dir"].endswith("memory"))
        self.assertNotEqual(body["runtime_dir"], "")

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

    async def test_hatching_save_syncs_runtime_model_deployments(self) -> None:
        payload = {
            "skill_configs": {
                "Azure": {
                    "deployment": "new-slow",
                    "fastDeployment": "new-fast",
                }
            }
        }
        runtime_manager = _FakeRuntimeManager()
        app = web.Application()
        app["runtime_manager"] = runtime_manager
        app["orchestrator"] = None
        request = make_mocked_request("PUT", "/api/desktop/hatching", app=app)
        request._read_bytes = json.dumps(payload).encode("utf-8")

        with (
            patch("azul_backend.azul_brain.api.routes.save_hatching_profile", return_value=payload),
            patch("azul_backend.azul_brain.api.routes.apply_hatching_azure_runtime_settings"),
        ):
            response = await routes.desktop_hatching_put_handler(request)

        self.assertEqual(response.status, 200)
        self.assertEqual(
            runtime_manager.saved_payloads,
            [
                {
                    "models": [
                        {"id": "fast", "deployment": "new-fast"},
                        {"id": "slow", "deployment": "new-slow"},
                    ]
                }
            ],
        )
        models = {model.id: model for model in runtime_manager.settings.models}
        self.assertEqual(models["fast"].deployment, "new-fast")
        self.assertFalse(models["fast"].enabled)
        self.assertTrue(models["fast"].streaming_enabled)
        self.assertEqual(models["slow"].deployment, "new-slow")
        self.assertTrue(models["slow"].enabled)
        self.assertEqual(runtime_manager.agent_cache, {})
        self.assertEqual(runtime_manager.probe_cache, {})


if __name__ == "__main__":
    unittest.main()
