from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "qualify_assistance_installed.py"
SPEC = importlib.util.spec_from_file_location("qualify_assistance_installed", SCRIPT)
qualifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = qualifier
SPEC.loader.exec_module(qualifier)


def test_default_invocation_is_inert_and_does_not_create_artifacts(tmp_path, capsys):
    output = tmp_path / "new-run"
    result = qualifier.main([
        "--project", "fixture", "--question", "Where is the baseline?",
        "--skill", "cuda", "--skill-question", "Explain the registered workflow.",
        "--out-dir", str(output),
    ])
    assert result == 2
    assert not output.exists()
    assert json.loads(capsys.readouterr().out) == {
        "status": "inert", "required_flag": "--execute-live"}


def test_private_artifact_directory_is_created_once_and_reuse_is_rejected(tmp_path):
    output = qualifier._private_new_directory(tmp_path / "run-1")
    assert output.stat().st_mode & 0o777 == 0o700
    with pytest.raises(qualifier.QualificationError, match="must be new"):
        qualifier._private_new_directory(output)


def test_status_identity_requires_all_server_and_release_fields():
    identity = {
        "release_digest": "a" * 64,
        "receiver_fingerprint": "b" * 64,
        "todo_runtime_fingerprint": "c" * 64,
        "supervisor_pid": 99,
        "supervisor_process_start": "12345",
        "daemon_epoch": "d" * 64,
    }
    value = {"runtime": {"readiness": "verified_ready", **identity}}
    assert qualifier._status_identity(value, "a" * 64) == identity
    value["runtime"].pop("daemon_epoch")
    with pytest.raises(qualifier.QualificationError, match="incomplete"):
        qualifier._status_identity(value)


def test_cold_gate_rejects_any_owned_slot_even_when_queue_is_empty():
    status = {"work": {"active_work": [], "active_execution_slots": [
        {"job_id": "job-1", "cleanup_pending": True}]}}
    assert not qualifier._work_is_idle(status)
    assert qualifier._work_is_idle({"work": {"active_work": [],
        "active_execution_slots": [], "owned_resources": {"active_sessions": 0}}})


def test_private_artifact_parent_symlink_is_resolved_before_creation(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    output = qualifier._private_new_directory(alias / "run")
    assert output.parent == parent.resolve()


def test_direct_role_helper_is_isolated_and_total_model_turn_ceiling_is_explicit():
    compile(qualifier.ROLE_HELPER, "<installed-role-helper>", "exec")
    assert "sys.path.insert" not in qualifier.ROLE_HELPER
    assert "RELEASE_DIGEST_VARIABLE" in qualifier.ROLE_HELPER
    assert qualifier.MAX_INQUIRY_SUBMISSIONS == 2
    assert qualifier.MAX_TURNS_PER_SUBMISSION == 6
    assert qualifier.MAX_DIRECT_ROLE_TURNS == 2
    potential_turns = (qualifier.MAX_INQUIRY_SUBMISSIONS
                       * qualifier.MAX_TURNS_PER_SUBMISSION
                       + qualifier.MAX_DIRECT_ROLE_TURNS)
    assert potential_turns == 14
