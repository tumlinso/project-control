#!/usr/bin/env python3
"""Isolated PCE2 review counterexamples; NOT the project's regression suite.

Source: Skills 6cf647f66bdd37668dfa219f7c7816192aae5c65, read through
Project Control source_context at the immutable commit. The three functions
below are copied from the inspected excerpts. Imports/helpers not relevant to
the demonstrated cases are replaced explicitly. No project code is imported,
no remote repository or Todo authority is opened, and no production state is
changed. All SQLite/filesystem activity is in a disposable local fixture.

Run with Python 3.10+; no third-party dependencies.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Test helpers, not copies of project helpers. Git metadata is outside the
# semantic hash for these fixtures (track_git_head is absent). Hashing uses the
# ordinary SHA256 of file bytes. Path helpers are not reached by malformed
# no-path fixtures; fail loudly instead of pretending to implement them.
def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dirty_paths(_root):
    return []


def git_head(_root):
    return "fixed-fixture-head"


def canonical_relative(*_args):
    raise AssertionError("Path validation is outside this isolated fixture")


def path_contains(*_args):
    raise AssertionError("Path validation is outside this isolated fixture")


def paths_overlap(*_args):
    raise AssertionError("Path validation is outside this isolated fixture")


class TodoError(Exception):
    pass


# Copied: todo-orchestrator/todo_orchestrator/evidence.py:13-48.
def gate_input_fingerprint(
    conn: sqlite3.Connection, repo_root: Path, config: dict[str, object], *, gate_type: str | None = None,
) -> tuple[str, dict[str, object]]:
    files: list[dict[str, str]] = []
    for value in sorted(str(item) for item in config.get("input_paths", [])):
        path = repo_root / value
        files.append({"path": value, "sha256": file_hash(path) if path.is_file() else "missing"})
    interfaces = []
    for interface_id in sorted(str(item) for item in config.get("interfaces", [])):
        row = conn.execute("SELECT id,state,version,content_hash FROM interfaces WHERE id=?", (interface_id,)).fetchone()
        interfaces.append(dict(row) if row else {"id": interface_id, "state": "missing"})
    command_contract = {
        key: config.get(key)
        for key in (
            "argv", "cwd", "env", "timeout", "expected_exit_code", "metric_file", "metric_path",
            "cuda", "operator", "threshold", "evaluation_required", "path", "pattern", "task_id", "status",
            "result", "checkpoint_id", "interface_id", "state", "version", "accepted", "resources", "locks",
        )
        if key in config
    }
    static_path = None
    if gate_type in {"file_exists", "pattern"} and config.get("path"):
        value = str(config["path"])
        path = repo_root / value
        static_path = {"path": value, "sha256": file_hash(path) if path.is_file() else "missing"}
    semantic = {"files": files, "interfaces": interfaces, "command_contract": command_contract, "static_path": static_path}
    if config.get("track_git_head"):
        semantic["git_head"] = git_head(repo_root)
    current_dirty = dirty_paths(repo_root)
    payload = {
        **semantic,
        "recorded_git_head": git_head(repo_root),
        "dirty_paths": current_dirty,
        "diff_fingerprint": hashlib.sha256(json.dumps(current_dirty, sort_keys=True).encode()).hexdigest(),
    }
    return hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest(), payload


# Copied: todo-orchestrator/todo_orchestrator/gates.py:255-280.
def _reusable_static_evidence(conn, gate, fingerprint: str, *, workspace_base_commit: str | None) -> dict[str, object] | None:
    """Reuse only deterministic static evidence with its complete input identity.

    Commands, GPU/resource gates, and manual acceptance deliberately execute
    again: their runtime observation lacks a complete reusable contract.
    """
    if workspace_base_commit is not None or gate["type"] not in {"file_exists", "pattern"}:
        return None
    config = json.loads(gate["config_json"])
    if config.get("cuda") is not None or config.get("resources") or config.get("locks"):
        return None
    if not gate["valid"] or gate["status"] != "passed" or gate["input_fingerprint"] != fingerprint:
        return None
    row = conn.execute(
        "SELECT id,status,metadata_json FROM evidence WHERE gate_id=? AND status='passed' ORDER BY revision DESC,id DESC LIMIT 1",
        (gate["id"],),
    ).fetchone()
    if not row:
        return None
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        return None
    if metadata.get("input_fingerprint") != fingerprint:
        return None
    return {"evidence_id": str(row["id"]), "status": "passed", "valid": True}


# Copied: todo-orchestrator/todo_orchestrator/gates.py:30-76.
def validate_gate_spec(
    gate: object,
    repo_root: Path | None,
    *,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    known_checkpoint_ids: set[str] | None = None,
    known_resources: set[str] | None = None,
) -> list[str]:
    """Validate the plan gate shape before either plan apply or claim binding."""
    if not isinstance(gate, dict) or not gate.get("id") or not gate.get("type"):
        return ["gate requires id and type"]
    gate_id = str(gate["id"])
    errors: list[str] = []
    if gate.get("type") in {"command", "benchmark", "json_predicate"} and (
        not isinstance(gate.get("argv"), list) or not gate.get("argv")
    ):
        errors.append(f"gate {gate_id} requires a non-empty argv array")
    for field in ("cwd", "path", "metric_file"):
        if not gate.get(field):
            continue
        try:
            path = "." if field == "cwd" and gate[field] == "." else canonical_relative(repo_root, str(gate[field])) if repo_root else str(gate[field])
            if path == "." and allowed_paths is not None:
                if not gate.get("input_paths"):
                    raise TodoError("gate_cwd_unscoped", "Repository-root cwd requires explicit owned input paths")
            elif allowed_paths is not None and not any(path_contains(scope, path) for scope in allowed_paths):
                raise TodoError("gate_path_outside_claim", "Gate path is outside the active task scope")
            if path != "." and forbidden_paths and any(paths_overlap(path, forbidden) for forbidden in forbidden_paths):
                raise TodoError("gate_path_forbidden", "Gate path intersects a forbidden task scope")
        except Exception:
            errors.append(f"gate {gate_id} {field} has unsafe or unowned repository path")
    for value in gate.get("input_paths", []):
        try:
            path = canonical_relative(repo_root, str(value)) if repo_root else str(value)
            if allowed_paths is not None and not any(path_contains(scope, path) for scope in allowed_paths):
                raise TodoError("gate_path_outside_claim", "Gate input is outside the active task scope")
            if forbidden_paths and any(paths_overlap(path, forbidden) for forbidden in forbidden_paths):
                raise TodoError("gate_path_forbidden", "Gate input intersects a forbidden task scope")
        except Exception:
            errors.append(f"gate {gate_id} input has unsafe or unowned repository path")
    if gate.get("checkpoint_id") and known_checkpoint_ids is not None and gate["checkpoint_id"] not in known_checkpoint_ids:
        errors.append(f"gate {gate_id} references unknown checkpoint {gate['checkpoint_id']}")
    for selector in gate.get("resources", []):
        if known_resources is not None and str(selector) not in known_resources and not (str(selector).endswith(":any") and str(selector)[:-4] in known_resources):
            errors.append(f"gate {gate_id} references unknown resource selector {selector}")
    return errors


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="pce2-review-") as temporary:
        root = Path(temporary)
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE evidence(id TEXT, gate_id TEXT, status TEXT, metadata_json TEXT, revision INTEGER)")
            directory = root / "required-directory"
            directory.mkdir()
            config = {"path": directory.name}
            before, before_inputs = gate_input_fingerprint(conn, root, config, gate_type="file_exists")
            # Same existence predicate as gates._evaluate_static: Path.exists().
            assert directory.exists()
            conn.execute("INSERT INTO evidence VALUES(?, ?, ?, ?, ?)", ("E", "G", "passed", json.dumps({"input_fingerprint": before}), 1))
            gate = {"id": "G", "type": "file_exists", "config_json": json.dumps(config), "valid": 1, "status": "passed", "input_fingerprint": before}
            directory.rmdir()
            after, after_inputs = gate_input_fingerprint(conn, root, config, gate_type="file_exists")
            reused = _reusable_static_evidence(conn, gate, after, workspace_base_commit=None)
            assert before == after and not directory.exists() and reused and reused["valid"]

            regular = root / "regular-file"
            regular.write_text("present\n", encoding="utf-8")
            regular_before, _ = gate_input_fingerprint(conn, root, {"path": regular.name}, gate_type="file_exists")
            regular.unlink()
            regular_after, _ = gate_input_fingerprint(conn, root, {"path": regular.name}, gate_type="file_exists")
            assert regular_before != regular_after

            malformed = [
                {"id": "MISSING-PATH", "type": "file_exists", "required": True},
                {"id": "MISSING-PATTERN", "type": "pattern", "required": True},
                {"id": "UNKNOWN-TYPE", "type": "not_an_executable_gate", "required": True},
                {"id": "INVALID-CUDA", "type": "command", "argv": ["true"], "cuda": {"gpus": 0}, "required": True},
            ]
            validator_results = [{"spec": item, "errors": validate_gate_spec(item, root, allowed_paths=["src/a"], forbidden_paths=[], known_resources=set(), known_checkpoint_ids=set())} for item in malformed]
            assert all(not result["errors"] for result in validator_results)
            argv_control = validate_gate_spec({"id": "NO-ARGV", "type": "command"}, root)
            assert argv_control

            # Exact inspected dispatch predicate, not a complete engine run.
            now = datetime.now(timezone.utc)
            heartbeat = (now - timedelta(seconds=5)).isoformat()
            expired = datetime.fromisoformat(heartbeat.replace("Z", "+00:00")) <= now
            process_state = "unavailable"
            blocked = process_state == "live" or (process_state == "unavailable" and not expired)
            assert not blocked

            return {
                "method": "isolated copied-function checks and one extracted predicate; not full project tests",
                "skills_commit": "6cf647f66bdd37668dfa219f7c7816192aae5c65",
                "directory_cache_counterexample": {"observed": True, "before_static_path": before_inputs["static_path"], "after_static_path": after_inputs["static_path"], "fingerprint_unchanged": before == after, "actual_exists_after": directory.exists(), "cache_result_after": reused},
                "regular_file_deletion_control": {"fingerprint_changed": regular_before != regular_after},
                "incomplete_gate_validation": validator_results,
                "missing_argv_control": {"rejected": bool(argv_control), "errors": argv_control},
                "unobservable_dispatch_predicate": {"heartbeat_age_seconds": 5, "state": process_state, "blocked": blocked, "limitation": "Extracted branch only; no process, claim, lock, authority or engine was exercised."},
                "remote_tests_run": False,
                "live_mutations": False,
            }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
