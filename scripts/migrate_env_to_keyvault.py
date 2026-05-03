"""Migrates local .env-style settings into Azure Key Vault."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from urllib.parse import urlparse, urlunparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from azul_backend.azul_brain.config import KEY_VAULT_HOST_SUFFIXES
from azul_backend.azul_brain.config import env_key_to_key_vault_secret_name


DEFAULT_ENV_FILE = Path("azul_backend") / "azul_brain" / ".env.local"


def parse_env_file(path: Path) -> dict[str, str]:
    """Reads key/value pairs from a simple .env file."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            values[key] = value
    return values


def resolve_vault_url(raw_value: str) -> str:
    """Accepts either a full Key Vault URL or a vault name."""
    value = raw_value.strip().rstrip("/")
    if not value:
        raise ValueError("A Key Vault URL or name is required.")
    if value.startswith("https://"):
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            raise ValueError("Key Vault URL must be an HTTPS endpoint.")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Key Vault URL must be the vault base URL.")
        if parsed.port:
            raise ValueError("Key Vault URL must not include a custom port.")
        if not any(host.endswith(f".{suffix}") for suffix in KEY_VAULT_HOST_SUFFIXES):
            raise ValueError("Key Vault URL host must be an Azure Key Vault hostname.")
        return urlunparse(("https", host, "", "", "", ""))
    return resolve_vault_url(f"https://{value}.vault.azure.net")


def migrate(
    *,
    env_file: Path,
    vault_url: str,
    dry_run: bool,
    delete_env_file: bool,
) -> int:
    """Uploads env values into Key Vault without printing secret values."""
    if not env_file.exists():
        raise FileNotFoundError(f"Env file not found: {env_file}")

    values = parse_env_file(env_file)
    if not values:
        print(f"No values found in {env_file}.")
        return 0

    print(f"Found {len(values)} value(s) in {env_file}.")
    for key in values:
        print(f"  {key} -> {env_key_to_key_vault_secret_name(key)}")

    if dry_run:
        print("Dry run only; no secrets were written.")
        return 0

    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(
        vault_url=vault_url,
        credential=DefaultAzureCredential(),
    )
    for key, value in values.items():
        client.set_secret(env_key_to_key_vault_secret_name(key), value)

    print(f"Wrote {len(values)} secret(s) to {vault_url}.")
    if delete_env_file:
        env_file.unlink()
        print(f"Deleted {env_file}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload .env.local values into Azure Key Vault."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"Env file to migrate. Default: {DEFAULT_ENV_FILE}",
    )
    parser.add_argument(
        "--vault",
        required=True,
        help="Key Vault name or URL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the mapping without writing secrets.",
    )
    parser.add_argument(
        "--delete-env-file",
        action="store_true",
        help="Delete the env file after a successful upload.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return migrate(
            env_file=args.env_file,
            vault_url=resolve_vault_url(args.vault),
            dry_run=args.dry_run,
            delete_env_file=args.delete_env_file,
        )
    except Exception as error:
        print(f"Migration failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
