from __future__ import annotations

import unittest

from scripts.migrate_env_to_keyvault import resolve_vault_url


class MigrateEnvToKeyVaultTests(unittest.TestCase):
    def test_resolve_vault_url_accepts_name_or_key_vault_url(self) -> None:
        self.assertEqual(resolve_vault_url("azul-dev"), "https://azul-dev.vault.azure.net")
        self.assertEqual(
            resolve_vault_url("https://azul-dev.vault.azure.net/"),
            "https://azul-dev.vault.azure.net",
        )

    def test_resolve_vault_url_rejects_non_key_vault_hosts(self) -> None:
        for value in (
            "https://example.com",
            "https://azul-dev.vault.azure.net.evil.com",
            "https://azul-dev.vault.azure.net/secrets/foo",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    resolve_vault_url(value)


if __name__ == "__main__":
    unittest.main()
