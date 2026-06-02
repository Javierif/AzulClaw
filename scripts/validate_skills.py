"""Validate AzulClaw skill manifests.

This script intentionally avoids external dependencies so it can run in CI and
developer machines before the full Python environment is installed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from azul_backend.azul_brain.api.skill_services import (
    SKILL_MANIFEST_FILENAME,
    validate_skill_manifest_path,
)


def iter_manifest_paths(root: Path) -> list[Path]:
    return sorted(root.glob(f"**/{SKILL_MANIFEST_FILENAME}"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate AzulClaw skill manifests.")
    parser.add_argument(
        "root",
        nargs="?",
        default="skills",
        help="Root folder to scan for azul.skill.json files.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    manifests = iter_manifest_paths(root)
    if not manifests:
        print(f"No {SKILL_MANIFEST_FILENAME} files found under {root}", file=sys.stderr)
        return 1

    failures: list[str] = []
    for manifest_path in manifests:
        try:
            validate_skill_manifest_path(manifest_path)
            print(f"OK {manifest_path.relative_to(root)}")
        except Exception as error:
            failures.append(f"FAIL {manifest_path}: {error}")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print(f"Validated {len(manifests)} skill manifest(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
