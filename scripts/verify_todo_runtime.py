#!/usr/bin/env python3
"""Verify the explicitly paired standalone Todo wheel or installed package."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import zipfile
from pathlib import Path

LOCK_FORMAT = "project-control.todo-runtime-lock"
LOCK_NAME = "todo-orchestrator"
LOCK_VERSION = "0.2.0"


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    paths = tuple(root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("installed Todo package contains a symlink")
    sources = sorted(path for path in paths if path.suffix == ".py" and path.is_file())
    if not sources:
        raise ValueError("installed Todo package contains no Python source")
    for source in sources:
        digest.update(source.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_lock(root: Path) -> dict:
    value = json.loads((root / "config" / "todo-orchestrator-runtime.json").read_text(encoding="utf-8"))
    expected = {"format", "version", "distribution", "distribution_version", "source_commit",
                "wheel_sha256", "package_fingerprint", "provisional"}
    if (not isinstance(value, dict) or set(value) != expected or value.get("format") != LOCK_FORMAT
            or value.get("version") != 1 or value.get("distribution") != LOCK_NAME
            or value.get("distribution_version") != LOCK_VERSION
            or type(value.get("provisional")) is not bool):
        raise ValueError("Todo runtime lock has an unsupported shape or package version")
    if value["provisional"] and os.environ.get("PROJECT_CONTROL_ALLOW_PROVISIONAL_TODO") != "1":
        raise ValueError("provisional Todo runtime lock requires PROJECT_CONTROL_ALLOW_PROVISIONAL_TODO=1")
    if not value["provisional"]:
        commit, wheel_hash, source_hash = (value["source_commit"], value["wheel_sha256"], value["package_fingerprint"])
        if (not isinstance(commit, str) or len(commit) != 40
                or any(char not in "0123456789abcdef" for char in commit)):
            raise ValueError("Todo source commit pin is missing or invalid")
        for item in (wheel_hash, source_hash):
            if (not isinstance(item, str) or len(item) != 64
                    or any(char not in "0123456789abcdef" for char in item)):
                raise ValueError("Todo artifact or package fingerprint pin is missing or invalid")
    return value


def verify_wheel(wheel: Path, lock: dict) -> None:
    if wheel.is_symlink() or not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError("standalone Todo wheel is missing or unsafe")
    with zipfile.ZipFile(wheel) as archive:
        paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(paths) != 1:
            raise ValueError("Todo wheel metadata is missing or ambiguous")
        fields = {}
        for line in archive.read(paths[0]).decode("utf-8").splitlines():
            if line.startswith("Name: ") or line.startswith("Version: "):
                key, value = line.split(": ", 1)
                if key in fields:
                    raise ValueError("Todo wheel metadata has duplicate identity fields")
                fields[key] = value
    if fields != {"Name": LOCK_NAME, "Version": lock["distribution_version"]}:
        raise ValueError("Todo wheel does not match the paired distribution and version")
    observed = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if lock["wheel_sha256"] is not None and observed != lock["wheel_sha256"]:
        raise ValueError("Todo wheel hash differs from the paired runtime lock")


def verify_installed(lock: dict) -> None:
    distribution = importlib.metadata.distribution(LOCK_NAME)
    if distribution.version != lock["distribution_version"]:
        raise ValueError("installed Todo version differs from the paired runtime lock")
    package = Path(distribution.locate_file("todo_orchestrator"))
    if package.is_symlink():
        raise ValueError("installed Todo package root is a symlink")
    package = package.resolve(strict=True)
    observed = fingerprint(package)
    if lock["package_fingerprint"] is not None and observed != lock["package_fingerprint"]:
        raise ValueError("installed Todo source differs from the paired runtime lock")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    try:
        lock = load_lock(args.repository.resolve(strict=True))
        if args.wheel is not None:
            verify_wheel(args.wheel.expanduser(), lock)
        if args.installed:
            verify_installed(lock)
        if args.wheel is None and not args.installed:
            parser.error("provide --wheel and/or --installed")
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
