from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from azul_backend.azul_brain import config
from azul_backend.azul_brain.api.hatching_store import HatchingProfile
from azul_backend.azul_brain.api.routes import _validate_key_vault_url


class _Secret:
    def __init__(self, value: str) -> None:
        self.value = value


class _MissingSecret(Exception):
    pass


_MissingSecret.__name__ = "ResourceNotFoundError"


class _FakeSecretClient:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.requested: list[str] = []

    def get_secret(self, name: str) -> _Secret:
        self.requested.append(name)
        if name not in self.values:
            raise _MissingSecret()
        return _Secret(self.values[name])


class _FailingSecretClient:
    def get_secret(self, name: str) -> _Secret:
        raise RuntimeError(f"cannot read {name}")


class KeyVaultConfigTests(unittest.TestCase):
    def test_env_key_to_key_vault_secret_name_replaces_underscores(self) -> None:
        self.assertEqual(
            config.env_key_to_key_vault_secret_name("AZURE_OPENAI_API_KEY"),
            "AZURE-OPENAI-API-KEY",
        )
        self.assertEqual(
            config.env_key_to_key_vault_secret_name("MicrosoftAppPassword"),
            "MicrosoftAppPassword",
        )

    def test_resolve_key_vault_url_accepts_name_or_url(self) -> None:
        with patch.dict(os.environ, {"AZUL_KEY_VAULT_NAME": "azulclaw-dev"}, clear=True):
            self.assertEqual(
                config.resolve_key_vault_url(),
                "https://azulclaw-dev.vault.azure.net",
            )

        with patch.dict(
            os.environ,
            {"AZUL_KEY_VAULT_URL": "https://example.vault.azure.net/"},
            clear=True,
        ):
            self.assertEqual(
                config.resolve_key_vault_url(),
                "https://example.vault.azure.net",
            )

        with patch.dict(
            os.environ,
            {"AZUL_KEY_VAULT_URL": "https://example.vault.microsoftazure.de/"},
            clear=True,
        ):
            self.assertEqual(
                config.resolve_key_vault_url(),
                "https://example.vault.microsoftazure.de",
            )

    def test_resolve_key_vault_url_reads_hatching_profile(self) -> None:
        profile = HatchingProfile(
            skill_configs={"Azure": {"keyVaultUrl": "https://profile.vault.azure.net/"}}
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("azul_backend.azul_brain.api.hatching_store.HatchingStore") as store_cls,
        ):
            store_cls.return_value.load.return_value = profile

            self.assertEqual(
                config.resolve_key_vault_url(),
                "https://profile.vault.azure.net",
            )

    def test_resolve_key_vault_url_rejects_non_vault_hosts(self) -> None:
        for value in (
            "http://profile.vault.azure.net",
            "https://localhost",
            "https://example.com",
            "https://profile.vault.azure.net.evil.com",
            "https://profile.vault.azure.net/secrets/foo",
            "https://profile.vault.azure.net:8443",
        ):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"AZUL_KEY_VAULT_URL": value}, clear=True):
                    with self.assertRaises(ValueError):
                        config.resolve_key_vault_url()

    def test_validate_key_vault_url_rejects_non_vault_hosts(self) -> None:
        self.assertEqual(
            _validate_key_vault_url("https://profile.vault.azure.net/"),
            "https://profile.vault.azure.net",
        )
        self.assertEqual(
            _validate_key_vault_url("https://profile.vault.microsoftazure.de/"),
            "https://profile.vault.microsoftazure.de",
        )
        for value in (
            "http://profile.vault.azure.net",
            "https://localhost",
            "https://example.com",
            "https://profile.vault.azure.net.evil.com",
            "https://profile.vault.azure.net/secrets/foo",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _validate_key_vault_url(value)

    def test_apply_hatching_azure_runtime_settings_restores_backend_env(self) -> None:
        profile = HatchingProfile(
            skill_configs={
                "Azure": {
                    "connected": "true",
                    "tenantId": "tenant-id",
                    "clientId": "client-id",
                    "endpoint": "https://profile.openai.azure.com/",
                    "deployment": "gpt-main",
                    "fastDeployment": "gpt-fast",
                    "embeddingDeployment": "text-embedding",
                    "keyVaultUrl": "https://profile.vault.azure.net/",
                }
            }
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("azul_backend.azul_brain.api.hatching_store.HatchingStore") as store_cls,
        ):
            store_cls.return_value.load.return_value = profile

            config.apply_hatching_azure_runtime_settings()

            self.assertEqual(os.environ["AZURE_OPENAI_ENDPOINT"], "https://profile.openai.azure.com")
            self.assertEqual(os.environ["AZURE_OPENAI_DEPLOYMENT"], "gpt-main")
            self.assertEqual(os.environ["AZURE_OPENAI_SLOW_DEPLOYMENT"], "gpt-main")
            self.assertEqual(os.environ["AZURE_OPENAI_FAST_DEPLOYMENT"], "gpt-fast")
            self.assertEqual(os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"], "text-embedding")
            self.assertEqual(os.environ["AZUL_KEY_VAULT_URL"], "https://profile.vault.azure.net")
            self.assertEqual(os.environ["AZURE_TENANT_ID"], "tenant-id")
            self.assertEqual(os.environ["AZUL_ENTRA_BROWSER_CLIENT_ID"], "client-id")
            self.assertEqual(os.environ["AZUL_AZURE_OPENAI_AUTH_MODE"], "entra")
            self.assertNotIn("AZUL_ENABLE_INTERACTIVE_BROWSER_AUTH", os.environ)

    def test_apply_hatching_azure_runtime_settings_overwrites_stale_profile_env(self) -> None:
        profile = HatchingProfile(
            skill_configs={
                "Azure": {
                    "connected": "true",
                    "endpoint": "https://profile.openai.azure.com/",
                    "deployment": "gpt-main",
                    "tenantId": "profile-tenant",
                    "clientId": "profile-client",
                }
            }
        )
        with (
            patch.dict(
                os.environ,
                {
                    "AZURE_OPENAI_ENDPOINT": "https://env.openai.azure.com",
                    "AZURE_OPENAI_DEPLOYMENT": "old-main",
                    "AZURE_TENANT_ID": "old-tenant",
                    "AZUL_ENTRA_BROWSER_CLIENT_ID": "old-client",
                },
                clear=True,
            ),
            patch("azul_backend.azul_brain.api.hatching_store.HatchingStore") as store_cls,
        ):
            store_cls.return_value.load.return_value = profile

            config.apply_hatching_azure_runtime_settings()

            self.assertEqual(os.environ["AZURE_OPENAI_ENDPOINT"], "https://profile.openai.azure.com")
            self.assertEqual(os.environ["AZURE_OPENAI_DEPLOYMENT"], "gpt-main")
            self.assertEqual(os.environ["AZURE_TENANT_ID"], "profile-tenant")
            self.assertEqual(os.environ["AZUL_ENTRA_BROWSER_CLIENT_ID"], "profile-client")

    def test_apply_hatching_azure_runtime_settings_clears_removed_optional_values(self) -> None:
        profile = HatchingProfile(
            skill_configs={
                "Azure": {
                    "connected": "true",
                    "endpoint": "https://profile.openai.azure.com/",
                    "deployment": "gpt-main",
                    "fastDeployment": "",
                    "embeddingDeployment": "",
                    "keyVaultUrl": "",
                    "tenantId": "",
                    "clientId": "",
                }
            }
        )
        with (
            patch.dict(
                os.environ,
                {
                    "AZURE_OPENAI_FAST_DEPLOYMENT": "old-fast",
                    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "old-embedding",
                    "AZUL_KEY_VAULT_URL": "https://old.vault.azure.net",
                    "AZURE_TENANT_ID": "old-tenant",
                    "AZUL_ENTRA_BROWSER_CLIENT_ID": "old-client",
                },
                clear=True,
            ),
            patch("azul_backend.azul_brain.api.hatching_store.HatchingStore") as store_cls,
        ):
            store_cls.return_value.load.return_value = profile

            config.apply_hatching_azure_runtime_settings()

            self.assertNotIn("AZURE_OPENAI_FAST_DEPLOYMENT", os.environ)
            self.assertNotIn("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", os.environ)
            self.assertNotIn("AZUL_KEY_VAULT_URL", os.environ)
            self.assertNotIn("AZURE_TENANT_ID", os.environ)
            self.assertNotIn("AZUL_ENTRA_BROWSER_CLIENT_ID", os.environ)

    def test_normalize_azure_openai_endpoint_rejects_non_azure_hosts(self) -> None:
        self.assertEqual(
            config.normalize_azure_openai_endpoint("https://profile.openai.azure.com/"),
            "https://profile.openai.azure.com",
        )
        self.assertEqual(
            config.normalize_azure_openai_endpoint("https://profile.services.ai.azure.com/"),
            "https://profile.services.ai.azure.com",
        )
        for value in (
            "http://profile.openai.azure.com",
            "https://localhost",
            "https://example.com",
            "https://profile.openai.azure.com.evil.com",
            "https://profile.openai.azure.com/openai/deployments",
            "https://profile.openai.azure.com:8443",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    config.normalize_azure_openai_endpoint(value)

    def test_load_key_vault_secrets_hydrates_unset_values(self) -> None:
        client = _FakeSecretClient(
            {
                "AZURE-OPENAI-ENDPOINT": "https://example.openai.azure.com/",
                "MicrosoftAppPassword": "bot-secret",
            }
        )

        with patch.dict(
            os.environ,
            {"AZUL_KEY_VAULT_URL": "https://test.vault.azure.net"},
            clear=True,
        ):
            config.load_key_vault_secrets(secret_client=client)

            self.assertEqual(
                os.environ["AZURE_OPENAI_ENDPOINT"],
                "https://example.openai.azure.com/",
            )
            self.assertEqual(os.environ["MicrosoftAppPassword"], "bot-secret")

    def test_load_key_vault_secrets_does_not_override_existing_env(self) -> None:
        client = _FakeSecretClient({"AZURE-OPENAI-ENDPOINT": "from-vault"})

        with patch.dict(
            os.environ,
            {
                "AZUL_KEY_VAULT_URL": "https://test.vault.azure.net",
                "AZURE_OPENAI_ENDPOINT": "from-env",
            },
            clear=True,
        ):
            config.load_key_vault_secrets(secret_client=client)

            self.assertEqual(os.environ["AZURE_OPENAI_ENDPOINT"], "from-env")
            self.assertNotIn("AZURE-OPENAI-ENDPOINT", client.requested)

    def test_load_key_vault_secrets_uses_hatching_secret_name_overrides(self) -> None:
        profile = HatchingProfile(
            skill_configs={
                "Azure": {
                    "keyVaultUrl": "https://test.vault.azure.net",
                    "microsoftAppIdSecretName": "bot-app-id",
                    "microsoftAppPasswordSecretName": "bot-app-password",
                    "microsoftAppTenantIdSecretName": "bot-tenant-id",
                }
            }
        )
        client = _FakeSecretClient(
            {
                "bot-app-id": "app-id",
                "bot-app-password": "app-password",
                "bot-tenant-id": "tenant-id",
            }
        )

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("azul_backend.azul_brain.api.hatching_store.HatchingStore") as store_cls,
        ):
            store_cls.return_value.load.return_value = profile

            config.load_key_vault_secrets(secret_client=client)

            self.assertEqual(os.environ["MicrosoftAppId"], "app-id")
            self.assertEqual(os.environ["MicrosoftAppPassword"], "app-password")
            self.assertEqual(os.environ["MicrosoftAppTenantId"], "tenant-id")

    def test_load_key_vault_secrets_is_non_blocking_by_default(self) -> None:
        with patch.dict(
            os.environ,
            {"AZUL_KEY_VAULT_URL": "https://test.vault.azure.net"},
            clear=True,
        ):
            config.load_key_vault_secrets(secret_client=_FailingSecretClient())

            self.assertNotIn("MicrosoftAppId", os.environ)

    def test_load_key_vault_secrets_can_fail_fast_in_strict_mode(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AZUL_KEY_VAULT_URL": "https://test.vault.azure.net",
                "AZUL_KEY_VAULT_STRICT": "true",
            },
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                config.load_key_vault_secrets(secret_client=_FailingSecretClient())


if __name__ == "__main__":
    unittest.main()
