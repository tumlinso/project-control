"""Identity and command routing for the bounded PA1 qualification helpers.

This module deliberately has no service, inference, or workflow side effects.
It lets the same inspectable harness run against either a checkout or a frozen
release while retaining different identity checks for those two cases.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


class QualificationRuntimeError(RuntimeError):
    pass


_SOURCE_PINS = (
    "PROJECT_CONTROL_RELEASE_MANIFEST",
    "PROJECT_CONTROL_RELEASE_DIGEST",
    "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT",
    "CODING_WORKFLOW_RUNTIME_FINGERPRINT",
    "PROJECT_CONTROL_RUNTIME_PYTHON",
    "CODING_WORKFLOW_RUNTIME_PYTHON",
    "PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256",
    "PROJECT_CONTROL_LOCAL_RUNTIME_ROOT",
    "PROJECT_CONTROL_LOCAL_RUNTIME_MANIFEST_SHA256",
)


def clear_source_pins(environment: dict[str, str] | None = None) -> None:
    """Remove inherited release selectors before resolving checkout packages."""
    target = os.environ if environment is None else environment
    for name in _SOURCE_PINS:
        target.pop(name, None)


def _git_provenance(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        result = subprocess.run(["git", "-C", str(root), *args], check=False,
                                capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise QualificationRuntimeError("source Git provenance unavailable")
        return result.stdout.strip()

    return {"head": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=normal"))}


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def runtime_provenance(mode: str, *, repository_root: Path,
                       release_cli: Path | None = None) -> dict[str, Any]:
    """Validate current package identity and report the selected CLI command.

    Source mode only accepts imports from this checkout's ``src`` tree and
    derives the receiver inventory from its current files. Release mode keeps
    the runtime identity module's manifest, digest, package, and receiver
    verification in force.
    """
    if mode not in {"source", "release"}:
        raise QualificationRuntimeError("runtime must be source or release")
    repository_root = repository_root.resolve(strict=True)
    if mode == "source":
        clear_source_pins()
        expected_root = (repository_root / "src").resolve(strict=True)
        expected_python = repository_root / ".venv" / "bin" / "python"
        try:
            actual_python = Path(sys.executable).resolve(strict=True)
            configured_python = expected_python.resolve(strict=True)
            configured_prefix = (repository_root / ".venv").resolve(strict=True)
            actual_prefix = Path(sys.prefix).resolve(strict=True)
        except OSError as error:
            raise QualificationRuntimeError("source development interpreter is unavailable") from error
        if actual_python != configured_python or actual_prefix != configured_prefix:
            raise QualificationRuntimeError(
                "source interpreter differs from the checked-in pc-dev environment")
    else:
        expected_root = None

    pc = importlib.import_module("project_control")
    todo = importlib.import_module("todo_orchestrator")
    pc_file = Path(pc.__file__).resolve(strict=True)
    todo_file = Path(todo.__file__).resolve(strict=True)
    if mode == "source":
        if not _under(pc_file, expected_root) or not _under(todo_file, expected_root):
            raise QualificationRuntimeError("source runtime imported a package outside this checkout")
    else:
        from project_control.runtime_identity import (
            RELEASE_DIGEST_VARIABLE, RELEASE_MANIFEST_VARIABLE, bind_runtime,
            package_fingerprint,
        )

        manifest_value = os.environ.get(RELEASE_MANIFEST_VARIABLE)
        digest = os.environ.get(RELEASE_DIGEST_VARIABLE)
        if not manifest_value or not digest:
            raise QualificationRuntimeError("release runtime pins are missing")
        manifest = Path(manifest_value).expanduser().resolve(strict=True)
        raw = manifest.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise QualificationRuntimeError("release manifest digest mismatch")
        try:
            document = json.loads(raw)
        except (ValueError, TypeError) as error:
            raise QualificationRuntimeError("release manifest is invalid") from error
        if not isinstance(document, dict) or document.get("schema_version") not in {2, 3}:
            raise QualificationRuntimeError("release manifest schema is unsupported")
        if document.get("schema_version") == 3:
            declared_pc = document.get("project_control_fingerprint")
            if (not isinstance(declared_pc, str) or len(declared_pc) != 64
                    or any(char not in "0123456789abcdef" for char in declared_pc)):
                raise QualificationRuntimeError("release Project Control fingerprint is missing or invalid")
        release_root = manifest.parent.resolve(strict=True)
        if not _under(pc_file, release_root) or not _under(todo_file, release_root):
            raise QualificationRuntimeError("release runtime imported a package outside the selected release")
        if not _under(Path(sys.prefix), release_root):
            raise QualificationRuntimeError("release interpreter is outside the selected release")
        bound = bind_runtime()
        pc_fingerprint = package_fingerprint(Path(pc.__file__).resolve().parent)
        expected_pc = document.get("project_control_fingerprint")
        if expected_pc and pc_fingerprint != expected_pc:
            raise QualificationRuntimeError("release Project Control fingerprint mismatch")
        if bound.fingerprint != document.get("todo_runtime_fingerprint"):
            raise QualificationRuntimeError("release Todo fingerprint mismatch")
        receiver = _receiver_provenance(mode, manifest.parent, document)
        cli = release_cli
        if cli is None:
            cli = release_root / "bin" / "project-control"
        cli = cli.expanduser().resolve(strict=True)
        if not _under(cli, release_root):
            raise QualificationRuntimeError("release CLI is outside the selected release")
        return {
            "runtime": mode,
            "git": _git_provenance(repository_root),
            "interpreter": {"executable": sys.executable,
                            "resolved": str(Path(sys.executable).resolve()),
                            "prefix": str(Path(sys.prefix).resolve())},
            "cli_command": [str(cli)],
            "project_control": {"path": str(pc_file), "fingerprint": pc_fingerprint},
            "todo": {"path": str(todo_file), "fingerprint": bound.fingerprint},
            "receiver": receiver,
            "release": {"root": str(release_root), "manifest": str(manifest),
                        "manifest_sha256": digest},
        }

    from project_control.runtime_binding import local_runtime_identity, _source_receiver_files
    from project_control.runtime_identity import bind_runtime, package_fingerprint

    pc_fingerprint = package_fingerprint(pc_file.parent)
    bound_todo = bind_runtime()
    if (bound_todo.module_file != todo_file
            or bound_todo.package_root != todo_file.parent):
        raise QualificationRuntimeError("source Todo binding differs from imported package")
    todo_fingerprint = bound_todo.fingerprint
    receiver_identity = local_runtime_identity()
    receiver_files = _source_receiver_files(receiver_identity.root)
    if _fingerprint(receiver_files) != receiver_identity.fingerprint:
        raise QualificationRuntimeError("source receiver inventory changed during inspection")
    from project_control.assistance.demand_runtime import capture_runtime_pin

    demand_pin = capture_runtime_pin()
    if (demand_pin.runtime_mode != "source"
            or demand_pin.source_root is None
            or demand_pin.source_root.resolve(strict=True) != repository_root
            or demand_pin.receiver_fingerprint != receiver_identity.fingerprint):
        raise QualificationRuntimeError("source demand runtime identity differs from checkout")
    cli = (repository_root / "scripts" / "pc-dev").resolve(strict=True)
    return {
        "runtime": mode,
        "git": _git_provenance(repository_root),
            "interpreter": {"executable": sys.executable,
                            "resolved": str(Path(sys.executable).resolve()),
                            "prefix": str(Path(sys.prefix).resolve())},
        "cli_command": [str(cli), "run"],
        "project_control": {"path": str(pc_file), "fingerprint": pc_fingerprint},
        "todo": {"path": str(todo_file), "fingerprint": todo_fingerprint},
        "receiver": {"root": str(receiver_identity.root),
                     "fingerprint": receiver_identity.fingerprint,
                     "file_count": len(receiver_files),
                     "files": dict(sorted(receiver_files.items()))},
        "demand_runtime": {"mode": demand_pin.runtime_mode,
                           "source_root": str(demand_pin.source_root),
                           "project_control_fingerprint": demand_pin.project_control_fingerprint,
                           "receiver_fingerprint": demand_pin.receiver_fingerprint,
                           "todo_runtime_fingerprint": demand_pin.todo_runtime_fingerprint,
                           "skills_root": str(demand_pin.skills_root) if demand_pin.skills_root else None},
    }


def _fingerprint(files: dict[str, str]) -> str:
    raw = json.dumps(dict(sorted(files.items())), sort_keys=True,
                     separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _receiver_provenance(mode: str, release_root: Path,
                         manifest: dict[str, Any]) -> dict[str, Any]:
    from project_control.runtime_binding import local_runtime_identity

    identity = local_runtime_identity()
    value: dict[str, Any] = {"root": str(identity.root),
                             "fingerprint": identity.fingerprint,
                             "file_count": identity.file_count}
    if mode == "release":
        binding = manifest.get("local_runtime_binding")
        if not isinstance(binding, dict):
            raise QualificationRuntimeError("release receiver binding is missing")
        if identity.fingerprint != binding.get("fingerprint"):
            raise QualificationRuntimeError("release receiver fingerprint mismatch")
        receiver_manifest = identity.root / "receiver-manifest.json"
        try:
            inventory = json.loads(receiver_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise QualificationRuntimeError("release receiver manifest is unavailable") from error
        files = inventory.get("files") if isinstance(inventory, dict) else None
        if not isinstance(files, dict):
            raise QualificationRuntimeError("release receiver file inventory is invalid")
        value["files"] = dict(sorted(files.items()))
    return value
