#!/usr/bin/env python3
"""Explicitly refresh the receiver's development source manifest.

This tool never runs as part of import, status inspection, or installation. The
caller must provide provenance and opt in with ``--write``; otherwise it only
prints the deterministic manifest that would be written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


MANIFEST_NAME = "receiver-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(root: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"refusing symlink in receiver tree: {relative.as_posix()}")
        if path.is_dir() and path.name == "__pycache__":
            continue
        if not path.is_file() or path.name == MANIFEST_NAME or path.suffix == ".pyc":
            continue
        entries[relative.as_posix()] = sha256(path)
    if not entries or "local_worker/__init__.py" not in entries:
        raise ValueError("receiver package is missing local_worker/__init__.py")
    return entries


def render(root: Path, *, source_root: str, source_commit: str,
           source_inventory_path: str | None, source_inventory_sha256: str | None) -> bytes:
    manifest = {
        "schema_version": 1,
        "source_root": source_root,
        "source_commit": source_commit,
        "files": collect_files(root),
    }
    existing_path = root / MANIFEST_NAME
    if source_inventory_path is None and source_inventory_sha256 is None and existing_path.is_file():
        try:
            previous = json.loads(existing_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            previous = {}
        source_inventory_path = previous.get("source_inventory_path") if isinstance(previous, dict) else None
        source_inventory_sha256 = previous.get("source_inventory_sha256") if isinstance(previous, dict) else None
    if (source_inventory_path is None) != (source_inventory_sha256 is None):
        raise ValueError("source inventory path and SHA256 must be supplied together")
    if source_inventory_path is not None:
        if not source_inventory_path.strip() or len(source_inventory_sha256 or "") != 64 or any(
                char not in "0123456789abcdef" for char in (source_inventory_sha256 or "")):
            raise ValueError("source inventory provenance is invalid")
        manifest["source_inventory_path"] = source_inventory_path.strip()
        manifest["source_inventory_sha256"] = source_inventory_sha256
    return (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parents[1] / "src/project_control/local_runtime"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root,
                        help="receiver root (defaults to this checkout's package-owned runtime)")
    parser.add_argument("--source-root", required=True, help="trusted supplier/source root recorded in manifest")
    parser.add_argument("--source-commit", required=True, help="source commit or reviewed working-tree identity")
    parser.add_argument("--source-inventory-path", help="optional path of reviewed transfer inventory")
    parser.add_argument("--source-inventory-sha256", help="optional digest of reviewed transfer inventory")
    parser.add_argument("--write", action="store_true", help="atomically replace receiver-manifest.json")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve(strict=True)
    if not args.source_root.strip() or not args.source_commit.strip():
        parser.error("source provenance values must be nonempty")
    try:
        rendered = render(root, source_root=args.source_root.strip(), source_commit=args.source_commit.strip(),
                          source_inventory_path=args.source_inventory_path,
                          source_inventory_sha256=args.source_inventory_sha256)
    except (OSError, ValueError) as error:
        print(f"manifest refresh refused: {error}", file=sys.stderr)
        return 2
    target = root / MANIFEST_NAME
    if not args.write:
        sys.stdout.buffer.write(rendered)
        print("dry run only; pass --write to update the receiver manifest", file=sys.stderr)
        return 0
    temporary = root / f".{MANIFEST_NAME}.{os.getpid()}.tmp"
    try:
        temporary.write_bytes(rendered)
        os.replace(temporary, target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        print(f"manifest refresh failed: {error}", file=sys.stderr)
        return 2
    print(f"updated {target} sha256={sha256(target)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
