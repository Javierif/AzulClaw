#!/usr/bin/env python3
"""Print where AzulClaw thinks your workspace is and list its contents.

Run from the repo root (same as ``npm run workspace:info``):

    python3 scripts/print_workspace.py

If the "MCP" path differs from the "Hatching" path, set ``AZUL_WORKSPACE_ROOT`` in
``azul_backend/azul_brain/.env.local`` to the same folder you chose in onboarding.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def main() -> None:
    from azul_backend.azul_brain.config import load_env_file

    load_env_file(_REPO / "azul_backend" / "azul_brain" / ".env.local")

    from azul_backend.azul_brain.api.services import get_workspace_root

    hatched = get_workspace_root()
    print("Hatching / API workspace:", hatched.resolve())
    if not hatched.exists():
        print("  (path does not exist yet)")
        return
    names = sorted(hatched.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    if not names:
        print("  (empty — scaffold should create folders on next backend start)")
        return
    for p in names:
        label = "dir " if p.is_dir() else "file"
        print(f"  {label} {p.name}")

    mcp = os.environ.get("AZUL_WORKSPACE_ROOT", "").strip()
    if not mcp:
        mcp = str(Path.home() / "Documents" / "dev" / "AzulWorkspace")
    mp = Path(mcp).expanduser()
    print("MCP tools workspace (env or default):", mp.resolve())
    try:
        if mp.resolve() != hatched.resolve():
            print(
                "  ^ differs from hatching path — set AZUL_WORKSPACE_ROOT to the same folder."
            )
    except OSError:
        pass


if __name__ == "__main__":
    main()
