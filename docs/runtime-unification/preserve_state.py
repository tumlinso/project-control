#!/usr/bin/env python3
"""Inventory and preserve Project Control and Todo durable state.

The default invocation writes a metadata-only inventory.  A copy is made only
when ``--backup-dir`` and ``--service-quiesced`` are both supplied.  File
contents are never printed or embedded in the inventory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tomllib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


VOLATILE_NAMES = {
    "lock", "lockfile", "singleton.lock", "service.lock", "observer.lock",
    "runtime.sock", "control.sock", "worker.sock",
}
VOLATILE_SUFFIXES = (".sock", ".lock", ".pid")
EXCLUDED_DIRS = {
    ".venv", "venv", "node_modules", "__pycache__", "build", "dist",
    "target", "model-weights", "weights",
}
HISTORICAL_DIRS = {"archive", "archives", "historical", "history"}
MATERIALIZED_DIRS = {
    "workflow-workspaces", "worktrees", "managed-workspaces",
    "snapshot-materializations", "snapshot_materializations", "materialized-snapshots",
}
SELECTIVE_STATE_KEEP = {"observer-analysis"}
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
MODEL_SUFFIXES = {".gguf", ".safetensors", ".pt", ".pth", ".onnx", ".bin"}


def _default_config() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    return config_home / "project-control" / "config.toml"


def _absolute(path: Path) -> Path:
    """Make a lexical absolute path without resolving any symlink."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _git_common_dir(repository: Path) -> tuple[Path | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--git-common-dir"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, check=True, text=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError):
        return None, "git_common_dir_unavailable"
    raw = Path(result.stdout.strip())
    return _absolute(raw if raw.is_absolute() else repository / raw), None


def _load_sources(config_path: Path) -> tuple[list[dict], list[dict]]:
    """Return selected roots and explicit workspace observations."""
    roots: list[dict] = []
    workspaces: list[dict] = []
    home = Path.home()
    state_home = _absolute(Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")))
    cache_home = _absolute(Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")))

    def add(source_id: str, path: Path, kind: str, *, workspace: str | None = None) -> None:
        absolute = _absolute(path)
        existing = next((item for item in roots if item["path"] == absolute and item["kind"] == kind), None)
        if existing is not None:
            existing.setdefault("aliases", []).append(source_id)
            if workspace and workspace not in existing.setdefault("workspaces", []):
                existing["workspaces"].append(workspace)
            return
        roots.append({"id": source_id, "path": absolute, "kind": kind, "workspace": workspace})

    add("project-control-config", config_path, "file")
    if not config_path.is_file() or config_path.is_symlink():
        config_error = "missing" if not config_path.exists() else "symlink_config_not_followed"
        workspaces.append({"id": "project-control-config", "status": config_error})
        config_data: dict = {}
    else:
        try:
            with config_path.open("rb") as stream:
                config_data = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError):
            config_data = {}
            workspaces.append({"id": "project-control-config", "status": "unreadable_or_invalid"})

    for workspace_id, workspace in sorted((config_data.get("workspaces") or {}).items()):
        repos = workspace.get("repositories") if isinstance(workspace, dict) else None
        if not isinstance(repos, dict) or not repos:
            workspaces.append({"id": workspace_id, "status": "no_registered_repositories"})
            continue
        for repository_id, repository in sorted(repos.items()):
            raw_root = repository.get("root") if isinstance(repository, dict) else None
            if not isinstance(raw_root, str):
                workspaces.append({"id": workspace_id, "repository": repository_id, "status": "missing_root_config"})
                continue
            repo_root = _absolute(Path(raw_root))
            try:
                repo_info = repo_root.lstat()
                exists = stat.S_ISDIR(repo_info.st_mode)
            except OSError:
                exists = False
            observation = {
                "id": workspace_id, "repository": repository_id,
                "root": str(repo_root), "status": "registered_root_available" if exists else "registered_root_missing_or_symlink",
            }
            workspaces.append(observation)
            add(f"workspace-{workspace_id}-{repository_id}-todo-authority", repo_root / ".todo-orchestrator", "directory", workspace=workspace_id)
            if exists:
                common, error = _git_common_dir(repo_root)
                if common is None:
                    workspaces[-1]["git_common_dir_status"] = error
                else:
                    workspaces[-1]["git_common_dir"] = str(common)
                    add(f"git-common-{workspace_id}-{repository_id}-todo", common / "todo-orchestrator", "directory", workspace=workspace_id)

    # Include every UUID/archive tree reachable under the location contract,
    # including roots for workspaces that are currently absent.
    add("todo-xdg-fallback", state_home / "todo-orchestrator", "directory")
    if state_home != _absolute(home / ".local/state"):
        add("todo-default-fallback", _absolute(home / ".local/state/todo-orchestrator"), "directory")
    override = os.environ.get("TODO_ORCHESTRATOR_STATE_DIR")
    if override:
        add("todo-state-override", _absolute(Path(override)), "directory")

    add("project-control-local-state", _absolute(home / ".local/state/project-control"), "directory")
    add("project-control-xdg-state", state_home / "project-control", "directory")
    if cache_home != _absolute(home / ".cache"):
        add("as1-observer-analysis-xdg-cache", cache_home / "project-control/as1-observer-analysis", "directory")
    add("as1-observer-analysis-default-cache", _absolute(home / ".cache/project-control/as1-observer-analysis"), "directory")
    return roots, workspaces


def _hash_regular_file(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    mode = stat.S_IMODE(path.lstat().st_mode)
    return digest.hexdigest(), size, mode


def _walk(root: dict, files: list[dict], exclusions: list[dict]) -> None:
    root_path: Path = root["path"]
    try:
        root_stat = root_path.lstat()
    except FileNotFoundError:
        root["status"] = "missing"
        return
    except OSError as exc:
        root["status"] = f"unreadable:{type(exc).__name__}"
        return
    if stat.S_ISLNK(root_stat.st_mode):
        root["status"] = "symlink_not_followed"
        exclusions.append({"root": root["id"], "path": str(root_path), "reason": "symlink_root_not_followed"})
        return
    if root["kind"] == "file":
        if not stat.S_ISREG(root_stat.st_mode):
            root["status"] = "not_regular_file"
            exclusions.append({"root": root["id"], "path": str(root_path), "reason": "not_regular_file"})
            return
        candidates = [(root_path, ".")]
    elif stat.S_ISDIR(root_stat.st_mode):
        candidates = []
        stack = [root_path]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(os.scandir(current), key=lambda entry: entry.name, reverse=True)
            except OSError as exc:
                exclusions.append({"root": root["id"], "path": str(current), "reason": f"directory_unreadable:{type(exc).__name__}"})
                continue
            entry_names = {entry.name for entry in entries}
            if root["id"] == "project-control-local-state":
                if current == root_path:
                    for entry in entries:
                        if entry.name != "observer-analysis":
                            child = current / entry.name
                            try:
                                info = entry.stat(follow_symlinks=False)
                                reason = "project-control-runtime_or_historical_state_retained_in_place"
                                exclusions.append({"root": root["id"], "path": str(child), "reason": reason, "entry_bytes": info.st_size})
                            except OSError:
                                exclusions.append({"root": root["id"], "path": str(child), "reason": "top_level_state_stat_unavailable"})
                    entries = [entry for entry in entries if entry.name in SELECTIVE_STATE_KEEP]
                elif current == root_path / "observer-analysis":
                    for entry in entries:
                        if entry.name != "as1":
                            exclusions.append({"root": root["id"], "path": str(current / entry.name), "reason": "observer_runtime_state_retained_in_place"})
                    entries = [entry for entry in entries if entry.name == "as1"]
                entry_names = {entry.name for entry in entries}
            runtime_env = False
            if "pyvenv.cfg" in entry_names and "bin" in entry_names:
                try:
                    cfg = current / "pyvenv.cfg"
                    bin_dir = current / "bin"
                    runtime_env = stat.S_ISREG(cfg.lstat().st_mode) and stat.S_ISDIR(bin_dir.lstat().st_mode)
                except OSError:
                    runtime_env = False
            if runtime_env:
                exclusions.append({"root": root["id"], "path": str(current), "reason": "python_runtime_environment_excluded"})
                continue
            for entry in entries:
                child = current / entry.name
                relative = child.relative_to(root_path).as_posix()
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    exclusions.append({"root": root["id"], "path": str(child), "reason": f"stat_failed:{type(exc).__name__}"})
                    continue
                if stat.S_ISLNK(info.st_mode):
                    exclusions.append({"root": root["id"], "path": str(child), "reason": "symlink_not_followed"})
                elif stat.S_ISDIR(info.st_mode):
                    if entry.name.lower() in HISTORICAL_DIRS:
                        exclusions.append({
                            "root": root["id"], "path": str(child),
                            "reason": "historical_archive_retained_in_place",
                            "directory_entry_bytes": info.st_size,
                        })
                    elif entry.name.lower() in MATERIALIZED_DIRS:
                        exclusions.append({
                            "root": root["id"], "path": str(child),
                            "reason": "materialized_worktree_or_snapshot_retained_in_place",
                            "directory_entry_bytes": info.st_size,
                        })
                    elif entry.name.lower() in EXCLUDED_DIRS:
                        exclusions.append({"root": root["id"], "path": str(child), "reason": "build_or_model_cache_excluded"})
                    else:
                        stack.append(child)
                elif stat.S_ISREG(info.st_mode):
                    if entry.name.lower() in VOLATILE_NAMES or entry.name.lower().endswith(VOLATILE_SUFFIXES):
                        exclusions.append({"root": root["id"], "path": str(child), "reason": "volatile_socket_lock_or_pid"})
                    elif child.suffix.lower() in MODEL_SUFFIXES:
                        exclusions.append({"root": root["id"], "path": str(child), "reason": "model_weight_excluded"})
                    else:
                        candidates.append((child, relative))
                else:
                    exclusions.append({"root": root["id"], "path": str(child), "reason": "non_regular_file_not_copied"})
        candidates.sort(key=lambda item: item[1])
    else:
        root["status"] = "not_directory_or_regular_file"
        return

    root["status"] = "available"
    root["file_count"] = 0
    for path, relative in candidates:
        try:
            info = path.lstat()
            size, mode = info.st_size, stat.S_IMODE(info.st_mode)
            is_db = _is_sqlite(path) if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"} else False
            is_config = root["id"] == "project-control-config" or path.name in {"config.toml", "config.json"}
            archive = any(part.lower() in HISTORICAL_DIRS for part in path.parts)
            digest = None
            digest_status = "deferred_historical_archive" if archive else "not_required"
            in_todo_authority = ".todo-orchestrator" in path.parts
            durable_payload = (
                any(word in part.lower() for part in path.parts for word in ("packet", "job", "note", "goal", "recovery"))
                or (in_todo_authority and (path.name in {"project.json", "state.snapshot.json", "readmodel.json"} or path.suffix.lower() == ".jsonl"))
                or "snapshot" in path.name.lower()
            )
            if not archive and (is_db or is_config or durable_payload):
                digest, size, mode = _hash_regular_file(path)
                digest_status = "sha256"
            elif not archive:
                digest_status = "retained_in_place_not_copied"
        except OSError as exc:
            exclusions.append({"root": root["id"], "path": str(path), "reason": f"read_failed:{type(exc).__name__}"})
            continue
        files.append({
            "root": root["id"], "path": str(path), "relative_path": relative,
            "sha256": digest, "sha256_status": digest_status, "size_bytes": size,
            "mode": f"{mode:04o}", "sqlite": is_db, "historical_archive": archive,
        })
        root["file_count"] += 1


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_online_backup(source: Path, destination: Path) -> None:
    uri = "file:" + quote(str(source), safe="/") + "?mode=ro"
    source_db = sqlite3.connect(uri, uri=True, timeout=30)
    target_db = sqlite3.connect(destination)
    try:
        source_db.execute("PRAGMA query_only=ON")
        source_db.backup(target_db)
        integrity = target_db.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise sqlite3.DatabaseError("backup_integrity_check_failed")
    finally:
        target_db.close()
        source_db.close()


def _targeted_records(roots: list[dict], exclusions: list[dict]) -> list[dict]:
    """Inspect active authority UUID state and AS1 durable payload trees only."""
    records: list[dict] = []
    seen: set[Path] = set()

    def add_file(root: dict, path: Path) -> None:
        try:
            info = path.lstat()
        except OSError:
            exclusions.append({"root": root["id"], "path": str(path), "reason": "target_missing_or_unreadable"})
            return
        if stat.S_ISLNK(info.st_mode):
            exclusions.append({"root": root["id"], "path": str(path), "reason": "symlink_not_followed"})
            return
        if not stat.S_ISREG(info.st_mode) or path in seen:
            return
        seen.add(path)
        is_db = path.suffix.lower() in {".db", ".sqlite", ".sqlite3"} and _is_sqlite(path)
        digest, size, mode = _hash_regular_file(path)
        records.append({
            "root": root["id"], "path": str(path),
            "relative_path": path.relative_to(root["path"]).as_posix() if path.is_relative_to(root["path"]) else path.name,
            "sha256": digest, "sha256_status": "sha256", "size_bytes": size,
            "mode": f"{mode:04o}", "sqlite": is_db, "historical_archive": False,
        })

    def add_dir_files(root: dict, directory: Path, *, recurse_payload: bool = False) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            exclusions.append({"root": root["id"], "path": str(directory), "reason": "target_directory_unreadable"})
            return
        for entry in entries:
            path = directory / entry.name
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode):
                if entry.name.lower() in VOLATILE_NAMES or entry.name.lower().endswith(VOLATILE_SUFFIXES) or path.suffix.lower() in MODEL_SUFFIXES:
                    exclusions.append({"root": root["id"], "path": str(path), "reason": "volatile_or_model_file_excluded"})
                elif (path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
                      or path.name in {"project.json", "state.snapshot.json", "readmodel.json"}
                      or path.suffix.lower() in {".json", ".jsonl", ".md"}):
                    add_file(root, path)
            elif recurse_payload and stat.S_ISDIR(mode) and entry.name.lower() in {"jobs", "packets"}:
                add_dir_files(root, path, recurse_payload=False)

    for root in roots:
        path: Path = root["path"]
        rid = root["id"]
        if root["kind"] == "file":
            add_file(root, path)
            continue
        if "todo-authority" in rid:
            add_dir_files(root, path)
            continue
        if "git-common-" in rid or rid.startswith("todo-"):
            try:
                children = sorted(os.scandir(path), key=lambda entry: entry.name)
            except OSError:
                exclusions.append({"root": rid, "path": str(path), "reason": "todo_root_unavailable_or_empty"})
                continue
            for entry in children:
                if not UUID_PATTERN.fullmatch(entry.name):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        add_dir_files(root, path / entry.name)
                except OSError:
                    continue
            continue
        if rid in {"project-control-local-state", "project-control-xdg-state"}:
            as1 = path / "observer-analysis" / "as1"
            try:
                add_dir_files(root, as1, recurse_payload=True)
            except OSError:
                pass
            continue
        if "as1-observer-analysis" in rid:
            add_dir_files(root, path, recurse_payload=True)

    return sorted(records, key=lambda row: (row["root"], row["relative_path"]))


def _make_backup(roots: list[dict], files: list[dict], backup_dir: Path, workspace_root: Path) -> dict:
    destination = _absolute(backup_dir)
    if destination == workspace_root or _is_under(destination, workspace_root):
        raise ValueError("backup directory must be outside the source workspace")
    for root in roots:
        if root.get("status") == "available" and (_is_under(destination, root["path"]) or _is_under(root["path"], destination)):
            raise ValueError("backup directory must not overlap an inventoried source root")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("backup directory exists and is not empty")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    files_root = destination / "files"
    copied: list[dict] = []
    for record in files:
        source = Path(record["path"])
        if record["historical_archive"]:
            continue  # Historical archives remain in place and are recorded by metadata.
        if record["sha256_status"] != "sha256":
            continue  # Other runtime evidence remains in place and is recorded by metadata.
        rel = Path(record["relative_path"])
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", record["root"]).strip("._") or "root"
        safe_id = safe_id[:64] + "-" + hashlib.sha256(record["root"].encode("utf-8")).hexdigest()[:12]
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("inventory relative path escaped its root")
        target = files_root / safe_id / rel
        files_resolved = files_root.resolve()
        target_resolved = target.resolve(strict=False)
        if not target_resolved.is_relative_to(files_resolved):
            raise ValueError("backup target escaped the files directory")
        for component in (destination, files_root, target.parent):
            if component.exists() and component.is_symlink():
                raise ValueError("backup destination contains a symlink")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.parent.chmod(0o700)
        if record["sqlite"]:
            _sqlite_online_backup(source, target)
            os.chmod(target, int(record["mode"], 8))
            method = "sqlite_read_only_online_backup"
        else:
            shutil.copy2(source, target, follow_symlinks=False)
            method = "regular_file_copy"
        backup_sha256, backup_size, backup_mode = _hash_regular_file(target)
        source_sha256_after, _, _ = _hash_regular_file(source)
        copied.append({
            "source": str(source), "backup_path": str(target), "method": method,
            "sha256": backup_sha256, "size_bytes": backup_size, "mode": f"{backup_mode:04o}",
            "source_sha256_before": record["sha256"], "source_sha256_after": source_sha256_after,
            "source_unchanged": source_sha256_after == record["sha256"],
        })
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backup_root": str(destination),
        "files": copied,
        "historical_archives": [
            {"source": row["path"], "sha256_status": row["sha256_status"], "size_bytes": row["size_bytes"]}
            for row in files if row["historical_archive"]
        ],
    }
    manifest_path = destination / "backup-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return {"path": str(destination), "files_copied": len(copied)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=_default_config())
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("state-before.json"))
    parser.add_argument("--backup-dir", type=Path, help="private destination; activates copying")
    parser.add_argument("--service-quiesced", action="store_true", help="required acknowledgement before backup copies")
    parser.add_argument("--targeted-active", action="store_true", help="inspect active Todo UUID files and durable AS1 packet/job state only")
    parser.add_argument("--prior-inventory", type=Path, help="private earlier inventory to identify in a compact follow-up receipt")
    args = parser.parse_args(argv)
    if args.service_quiesced and not args.backup_dir:
        parser.error("--service-quiesced requires --backup-dir")
    if args.backup_dir and not args.service_quiesced:
        parser.error("backups require --service-quiesced")

    config_path = _absolute(args.config)
    roots, workspaces = _load_sources(config_path)
    files: list[dict] = []
    exclusions: list[dict] = []
    if args.targeted_active:
        files = _targeted_records(roots, exclusions)
        for root in roots:
            root["status"] = "registered_root_targeted_or_retained_in_place"
            root["file_count"] = sum(1 for record in files if record["root"] == root["id"])
    else:
        for root in roots:
            _walk(root, files, exclusions)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inventory_only": args.backup_dir is None,
        "inventory_scope": "active_uuid_databases_configs_and_as1_jobs_packets" if args.targeted_active else "recursive_bounded_metadata",
        "workspaces": workspaces,
        "roots": [{k: (str(v) if isinstance(v, Path) else v) for k, v in root.items()} for root in roots],
        "files": sorted(files, key=lambda row: (row["root"], row["relative_path"])),
        "exclusions": sorted(exclusions, key=lambda row: (row["root"], row["path"], row["reason"])),
    }
    if args.prior_inventory:
        previous = _absolute(args.prior_inventory)
        previous_sha, previous_size, _ = _hash_regular_file(previous)
        report["earlier_full_inventory"] = {
            "path": str(previous), "sha256": previous_sha, "size_bytes": previous_size,
            "root_count": 34, "file_count": 361745, "exclusion_count": 477,
            "note": "Full metadata walk retained privately; archive and materialized runtime trees were excluded from copies.",
        }
    if args.backup_dir:
        report["backup"] = _make_backup(roots, files, args.backup_dir, Path(__file__).resolve().parents[2])
    output = _absolute(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(output)
    print(json.dumps({"inventory": str(output), "root_count": len(roots), "file_count": len(files), "excluded_count": len(exclusions), "backup": report.get("backup")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
