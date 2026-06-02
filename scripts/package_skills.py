"""Package AzulClaw skills into .azulskill bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from azul_backend.azul_brain.api.skill_services import (  # noqa: E402
    SKILL_MANIFEST_FILENAME,
    validate_skill_manifest_path,
)


BLOCKED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".terraform",
    ".venv",
    "__pycache__",
    "node_modules",
}

BLOCKED_FILE_NAMES = {
    ".env",
    ".env.local",
    "local.settings.json",
}

BLOCKED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".tfstate",
    ".tfvars",
    ".azulskill",
}


@dataclass(frozen=True)
class PackagedSkill:
    skill_id: str
    version: str
    path: Path
    sha256: str
    files: int
    manifest: dict


def _is_blocked_file(path: Path) -> bool:
    name = path.name.lower()
    if name in BLOCKED_FILE_NAMES:
        return True
    if name.startswith(".env."):
        return True
    return any(name.endswith(suffix) for suffix in BLOCKED_SUFFIXES)


def iter_bundle_files(skill_dir: Path) -> list[Path]:
    """Returns files that are safe to include in a skill bundle."""
    root = skill_dir.resolve()
    if not (root / SKILL_MANIFEST_FILENAME).is_file():
        raise ValueError(f"{root} does not contain {SKILL_MANIFEST_FILENAME}.")

    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(root)
        if any(part in BLOCKED_DIR_NAMES for part in relative.parts):
            continue
        if _is_blocked_file(path):
            continue
        files.append(path)
    return files


def _safe_bundle_name(skill_id: str, version: str) -> str:
    cleaned_id = "".join(char if char.isalnum() or char in ".-" else "-" for char in skill_id)
    return f"{cleaned_id}-{version}.azulskill"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_skill(skill_dir: Path, output_dir: Path) -> PackagedSkill:
    """Packages one skill directory as a .azulskill bundle."""
    skill_dir = skill_dir.resolve()
    output_dir = output_dir.resolve()
    manifest = validate_skill_manifest_path(skill_dir / SKILL_MANIFEST_FILENAME)
    files = iter_bundle_files(skill_dir)
    if not files:
        raise ValueError(f"{skill_dir} does not contain bundle files.")

    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / _safe_bundle_name(str(manifest["id"]), str(manifest["version"]))
    if bundle_path.exists():
        bundle_path.unlink()

    with zipfile.ZipFile(bundle_path, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for file_path in files:
            archive_name = file_path.relative_to(skill_dir).as_posix()
            bundle.write(file_path, archive_name)

    return PackagedSkill(
        skill_id=str(manifest["id"]),
        version=str(manifest["version"]),
        path=bundle_path,
        sha256=_sha256(bundle_path),
        files=len(files),
        manifest=manifest,
    )


def catalog_entry(packaged: PackagedSkill, output_dir: Path) -> dict:
    """Builds one registry catalog entry for a packaged skill."""
    manifest = packaged.manifest
    runtime = manifest.get("runtime", {})
    try:
        artifact_path = packaged.path.relative_to(output_dir.resolve()).as_posix()
    except ValueError:
        artifact_path = packaged.path.name
    return {
        "id": packaged.skill_id,
        "name": manifest["name"],
        "version": packaged.version,
        "publisher": manifest["publisher"],
        "description": manifest["description"],
        "kind": manifest["kind"],
        "runtime_kind": runtime.get("kind", ""),
        "categories": manifest.get("categories", []),
        "tags": manifest.get("tags", []),
        "presentation": manifest.get("presentation", {}),
        "config_schema": manifest.get("config_schema", {}),
        "secrets": manifest.get("secrets", []),
        "permissions": manifest.get("permissions", {}),
        "workflow": manifest.get("workflow", {}),
        "capabilities": manifest.get("capabilities", []),
        "compatibility": manifest.get("compatibility", {}),
        "activation": manifest.get("activation", {}),
        "approved": True,
        "artifact": {
            "filename": packaged.path.name,
            "path": artifact_path,
            "sha256": packaged.sha256,
            "size_bytes": packaged.path.stat().st_size,
            "files": packaged.files,
        },
    }


def write_catalog(packaged_skills: list[PackagedSkill], output_dir: Path) -> Path:
    """Writes the registry catalog consumed by the Skill Registry API."""
    output_dir = output_dir.resolve()
    catalog_path = output_dir / "catalog.json"
    payload = {
        "schema_version": "1.0",
        "registry": "azulclaw-official",
        "skills": [catalog_entry(packaged, output_dir) for packaged in packaged_skills],
    }
    catalog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog_path


def discover_skill_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob(f"**/{SKILL_MANIFEST_FILENAME}"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Package AzulClaw skills as .azulskill bundles.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Skill directories or roots to scan. Defaults to skills/official.",
    )
    parser.add_argument(
        "--out",
        default="dist/skills",
        help="Output directory for .azulskill bundles.",
    )
    args = parser.parse_args()

    requested = [Path(path) for path in args.paths] or [Path("skills/official")]
    skill_dirs: list[Path] = []
    for path in requested:
        resolved = path.resolve()
        if (resolved / SKILL_MANIFEST_FILENAME).is_file():
            skill_dirs.append(resolved)
        else:
            skill_dirs.extend(discover_skill_dirs(resolved))

    if not skill_dirs:
        print("No skill manifests found.", file=sys.stderr)
        return 1

    output_dir = Path(args.out)
    packaged_skills: list[PackagedSkill] = []
    for skill_dir in skill_dirs:
        packaged = package_skill(skill_dir, output_dir)
        packaged_skills.append(packaged)
        print(
            f"PACKAGED {packaged.skill_id}@{packaged.version} "
            f"{packaged.path} sha256={packaged.sha256} files={packaged.files}"
        )
    catalog_path = write_catalog(packaged_skills, output_dir)
    print(f"CATALOG {catalog_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
