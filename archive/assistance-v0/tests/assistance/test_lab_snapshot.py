"""Source-bound detached capture contracts for the scratch laboratory."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from project_control.assistance import lab_snapshot
from project_control.assistance.lab_snapshot import Snapshot, SnapshotError


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], text=True, capture_output=True, check=True)
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("initial = 1\n", encoding="utf-8")
    _git(root, "add", "src/module.py")
    subprocess.run(["git", "-c", "user.name=Snapshot Test", "-c", "user.email=snapshot@example.invalid",
                    "commit", "-qm", "initial"], cwd=root, check=True)
    return root


def test_captures_dirty_tracked_and_selected_untracked_with_deterministic_manifest(tmp_path):
    root = _repo(tmp_path)
    tracked = root / "src" / "module.py"
    tracked.write_text("dirty = 2\n", encoding="utf-8")
    tracked.chmod(0o751)
    (root / "src" / "new.py").write_text("untracked = True\n", encoding="utf-8")
    untouched = root / "outside.py"
    untouched.write_text("not selected\n", encoding="utf-8")
    sentinel = (root / ".git" / "HEAD").read_bytes()

    snapshot = Snapshot.capture(root, ["src"], tmp_path / "capture")

    assert (snapshot.destination / "src/module.py").read_text() == "dirty = 2\n"
    assert (snapshot.destination / "src/new.py").read_text() == "untracked = True\n"
    assert not (snapshot.destination / "outside.py").exists()
    assert (snapshot.destination / "src/module.py").stat().st_mode & 0o777 == 0o751
    assert snapshot.head_commit == _git(root, "rev-parse", "HEAD")
    assert snapshot.base_commit == snapshot.head_commit
    assert [entry.path for entry in snapshot.entries] == ["src/module.py", "src/new.py"]
    assert (root / ".git" / "HEAD").read_bytes() == sentinel
    manifest = json.loads((snapshot.destination / lab_snapshot.MANIFEST_NAME).read_text())
    assert manifest["files"] == [
        {"path": entry.path, "mode": entry.mode, "size": entry.size, "sha256": entry.sha256}
        for entry in snapshot.entries
    ]
    assert manifest["head_commit"] == snapshot.head_commit


@pytest.mark.parametrize("selected", [
    [".git"], [".todo-orchestrator/state.snapshot.json"], ["todos.md"],
    ["build"], [".env"], ["credentials.json"], ["id_ed25519"],
])
def test_rejects_git_todo_generated_and_credential_paths(tmp_path, selected):
    root = _repo(tmp_path)
    (root / ".env").write_text("safe placeholder\n")
    (root / "credentials.json").write_text("{}\n")
    (root / "id_ed25519").write_text("placeholder\n")
    (root / "build").mkdir()
    (root / ".todo-orchestrator").mkdir()
    (root / ".todo-orchestrator" / "state.snapshot.json").write_text("{}")
    (root / "todos.md").write_text("projection\n")

    with pytest.raises(SnapshotError):
        Snapshot.capture(root, selected, tmp_path / "capture")
    assert not (tmp_path / "capture").exists()


@pytest.mark.parametrize("name", ["auth.json", "runtime-state.json"])
@pytest.mark.parametrize("recursive", [False, True], ids=["direct", "recursive"])
def test_rejects_as1_private_state_files_from_direct_and_recursive_capture(
    tmp_path, name, recursive,
):
    root = _repo(tmp_path)
    private_dir = root / "src" / "private"
    private_dir.mkdir()
    (private_dir / name).write_text("private runtime material\n", encoding="utf-8")
    selected = ["src"] if recursive else [f"src/private/{name}"]

    with pytest.raises(SnapshotError, match="private or generated"):
        Snapshot.capture(root, selected, tmp_path / "capture")

    assert not (tmp_path / "capture").exists()


def test_rejects_symlink_even_when_target_is_inside_source(tmp_path):
    root = _repo(tmp_path)
    (root / "src" / "link.py").symlink_to(root / "src" / "module.py")

    with pytest.raises(SnapshotError, match="symlinks"):
        Snapshot.capture(root, ["src"], tmp_path / "capture")
    assert not (tmp_path / "capture").exists()


@pytest.mark.parametrize("recursive", [False, True], ids=["direct", "recursive"])
def test_rejects_hardlink_to_outside_private_sentinel_without_writing_it(tmp_path, recursive):
    root = _repo(tmp_path)
    sentinel = tmp_path / "outside-secret"
    sentinel.write_bytes(b"private sentinel must not change\n")
    hardlink = root / "src" / "module.py"
    hardlink.unlink()
    hardlink_target = root / "src" / "linked.py"
    os.link(sentinel, hardlink_target)
    if recursive:
        selected = ["src"]
    else:
        selected = ["src/linked.py"]

    with pytest.raises(SnapshotError, match="hard-linked"):
        Snapshot.capture(root, selected, tmp_path / "capture")

    assert sentinel.read_bytes() == b"private sentinel must not change\n"
    assert not (tmp_path / "capture").exists()
    assert not list(tmp_path.glob(".capture.capture-*"))


def test_enforces_capture_limit_before_destination_materialization(tmp_path):
    root = _repo(tmp_path)
    (root / "src" / "large.py").write_bytes(b"x" * 16)

    with pytest.raises(SnapshotError, match="byte limit"):
        Snapshot.capture(root, ["src"], tmp_path / "capture", max_bytes=8)
    with pytest.raises(SnapshotError, match="50 MiB"):
        Snapshot.capture(root, ["src"], tmp_path / "capture-2", max_bytes=50 * 1024 * 1024 + 1)
    assert not (tmp_path / "capture").exists()
    assert not (tmp_path / "capture-2").exists()


def test_racing_edit_defers_capture_and_removes_partial_detached_copy(tmp_path):
    root = _repo(tmp_path)
    source_file = root / "src" / "module.py"
    source_file.write_text("before\n", encoding="utf-8")
    original_hash = lab_snapshot._hash_regular_file
    changed = False

    def edit_then_hash(path):
        nonlocal changed
        if path == source_file and not changed:
            changed = True
            source_file.write_text("raced edit\n", encoding="utf-8")
        return original_hash(path)

    with patch.object(lab_snapshot, "_hash_regular_file", side_effect=edit_then_hash):
        with pytest.raises(SnapshotError, match="changed during capture"):
            Snapshot.capture(root, ["src/module.py"], tmp_path / "capture")
    assert changed
    assert source_file.read_text() == "raced edit\n"
    assert not (tmp_path / "capture").exists()
    assert not list(tmp_path.glob(".capture.capture-*"))


def test_rejects_destination_inside_source_and_existing_destination(tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(SnapshotError, match="outside"):
        Snapshot.capture(root, ["src/module.py"], root / "capture")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(SnapshotError, match="already exists"):
        Snapshot.capture(root, ["src/module.py"], existing)


@pytest.mark.parametrize(("kind", "kwargs", "paths", "pattern"), [
    ("files", {"max_files": 2}, ["src"], "file count"),
    ("directories", {"max_dirs": 2}, ["src"], "directory count"),
    ("manifest", {"max_manifest_bytes": 128}, ["src"], "manifest"),
])
def test_capture_count_and_manifest_caps_fail_before_publish(tmp_path, kind, kwargs, paths, pattern):
    root = _repo(tmp_path)
    if kind == "files":
        (root / "src" / "a.py").write_text("a\n", encoding="utf-8")
        (root / "src" / "b.py").write_text("b\n", encoding="utf-8")
    elif kind == "directories":
        (root / "src" / "one").mkdir()
        (root / "src" / "two").mkdir()
        (root / "src" / "one" / "a.py").write_text("a\n", encoding="utf-8")
        (root / "src" / "two" / "b.py").write_text("b\n", encoding="utf-8")

    with pytest.raises(SnapshotError, match=pattern):
        Snapshot.capture(root, paths, tmp_path / "bounded-capture", **kwargs)
    assert not (tmp_path / "bounded-capture").exists()
    assert not list(tmp_path.glob(".bounded-capture.capture-*"))


@pytest.mark.parametrize("selected", [
    [f"src/{index}.py" for index in range(lab_snapshot.MAX_SELECTION_PATHS + 1)],
    ["/".join(["src"] + ["deep"] * lab_snapshot.MAX_PATH_DEPTH + ["module.py"])],
    ["src/" + "x" * lab_snapshot.MAX_PATH_BYTES + ".py"],
    ["src/" + "x" * 2048, "src/" + "y" * 2048] * 17,
])
def test_selection_count_path_depth_and_metadata_caps_reject_early(tmp_path, selected):
    root = _repo(tmp_path)
    destination = tmp_path / "bounded-capture"
    with pytest.raises(SnapshotError):
        Snapshot.capture(root, selected, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".bounded-capture.capture-*"))
